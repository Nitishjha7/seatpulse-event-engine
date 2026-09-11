# Phase 17 — Group Booking + Split Payment

> 4 friends want to sit together. Everyone pays for themselves.
> 3 have paid, the 4th hasn't, and the deadline has passed.
>
> **Now what?**

---

## Why this problem is new

Until now, every correctness question in the project had the same shape: **one seat, one booking**. Redis lock, `version` column, partial unique index — these were three answers to that same question.

In a group, the question changes:

```
Ek seat, ek booking          ->  ek row par exactly-once
Sab ya koi nahi              ->  N ALAG payments par atomicity
```

This is a true distributed problem because every payment arrives at a different time, from a different user, via a different browser — and the deadline can pass in between.

**Decision: All or nothing.** Giving seats to three people and not the fourth defeats the purpose of the group (they wanted to sit together). So, the group breaks, seats are released, and the three payments are refunded.

---

## ⭐ Decision 1 — New seat status, not `booking`

The straightforward path seems to be creating N normal bookings and linking them with a `group_id`.

That is wrong: **a booking means "the seat is confirmed"**. In a group, no seat is confirmed until everyone has paid.

We need an intermediate state — seats are held, some payments have arrived, but the final decision is pending:

```
seats.status = 'group_held'      <- new status
group_bookings                    <- decision happens here
group_shares                      <- each seat + its share
```

Bookings are created **only** when the group is `confirmed` — and then, all at once.

### Why `locked` was not reused

This is the most important design detail.

`locked` and `payment_pending` seats are cleaned up by lazy cleanup (`release_expired_locks`) which silently sets them to `available` when the TTL expires. Doing this with group seats would be **wrong**:

> Some of those seats have already been **paid for**. Releasing them doesn't just mean "free the seat" — it also requires a refund.

A refund is a decision, not a side-effect. Therefore, lazy cleanup ignores `group_held`.

📁 [`backend/models.py`](../../backend/models.py) · [`backend/groups.py`](../../backend/groups.py)

---

## Decision 2 — Expiry via cron, not lazy cleanup

In the rest of the project, we clean up expired holds **lazily**: when someone reads the seats, old locks are released. This is cheap and sufficient because nothing is lost there.

This won't work for groups:

> If no one opens the event page, lazy cleanup **never runs** — and people are left waiting for their money.
>
> **Getting a refund cannot depend on a stranger opening a page.**

Therefore, there is a cron job in ARQ, running every 30 seconds:

```python
cron_jobs = [
    cron(expire_groups, second={0, 30}, run_at_startup=True),
]
```

`run_at_startup` is included so that if a worker restarts, groups that expired in the interim are handled immediately.

The job is idempotent — `break_group` runs via an atomic conditional UPDATE, so even if two workers run simultaneously, only one will break the group.

📁 [`backend/worker.py`](../../backend/worker.py)

---

## ⭐⭐ Decision 3 — Where optimistic locking FAILED

This is the most interesting part of the phase and is directly linked to [Phase 15](15-locking-benchmark.md).

There, I benchmarked and showed that the difference between optimistic and pessimistic locking is not measurable, so I kept optimistic as the default. Here, I found a case where **correctness is impossible without pessimistic locking**.

### The race that actually broke in testing

```
payment thread                  expiry job
--------------                  ----------
read group.status
  -> 'collecting'
                                set group to 'expired'
                                read shares
                                  -> this share is still 'unpaid'
                                  -> no refund triggered
set share to 'paid'

Result: An 'expired' group with a 'paid' share.
The user's money was taken, they didn't get the seat, and no refund was issued.
```

This is a classic TOCTOU — `if group.status != COLLECTING` is a **read**, not an atomic guard.

### Why the optimistic pattern fails here

Throughout the project, our pattern is:

```sql
UPDATE x SET ... WHERE id = ? AND status = 'expected'
```

This works when both racers are making a decision on the **same row**. Here, that is not the case:

- The payment thread updates a `group_shares` row.
- The expiry job updates a `group_bookings` row.

**A conditional UPDATE on one row cannot stop a race on another row.** These must be serialized:

```python
group = db.execute(
    select(GroupBooking).where(GroupBooking.id == share.group_id).with_for_update()
).scalar_one()

if group.status != GROUP_COLLECTING:
    _refund_share(db, share, payment)
    return
```

With `FOR UPDATE`: if the expiry job is running first (holding a lock on the group row), the payment thread **waits** until it commits, then re-reads and sees `expired` → refund. Conversely, if the payment thread is first, the expiry job waits and then sees the `paid` share and issues a refund.

> Phase 15 said: "optimistic default, because the cost of pessimistic locking increases with lock hold time."
> Phase 17 says: "and where two DIFFERENT rows must be serialized, there is no option other than pessimistic."
>
> Both statements coexist. Here, the lock is held for ~1ms, so the danger mentioned in Phase 15 does not apply.

---

## Flow

```
1. CREATE     Claim N seats in one transaction  ->  status 'group_held'
              receive share_token  ->  link
                    |
2. CLAIM      each person takes an empty share
              UPDATE ... WHERE claimed_by IS NULL      <- atomic
                    |
3. PAY        each share has its own checkout, its own Payment row
              Payment.group_share_id is set
                    |
4. SETTLE     each payment sets status to 'paid'...
              ...and when ALL are paid:
              UPDATE group_bookings SET 'confirmed' WHERE status='collecting'
                    |                                     ^
                    |                          rowcount 1 = I won
              N bookings are created at once
```

And the other path:

```
   DEADLINE   cron -> break_group()
              UPDATE ... SET 'expired' WHERE status='collecting'
              -> seats available
              -> paid shares refunded
              -> pending payments expired
```

### Group creation is all-or-nothing

```python
for seat_id in seat_ids:
    result = db.execute(update(Seat).where(...).values(status=SEAT_GROUP_HELD))
    if result.rowcount == 0:
        db.rollback()          # seats already acquired are released
        raise GroupError(...)
```

If even one seat is unavailable, the entire group is rejected. Otherwise, the user would get 3 seats and keep waiting for the 4th — which would never come. **A partial hold is useless.**

The test catches this: after a failed creation, the remaining seats must be `available`, not stuck in `group_held`.

### Price is frozen when the group is created

A group can take 30 minutes to fill. During that time, surge pricing can increase significantly ([Phase 14](14-dynamic-pricing.md)). `GroupShare.amount` is written at the time of creation — that is the promise, and that is what is charged.

---

## Security

| Item | Why |
|---|---|
| `share_token` (`secrets.token_urlsafe`), not ID | If IDs were sequential, anyone could call `/api/groups/1`, `/2` and view others' groups |
| `id` is not sent in the API | No chance for it to leak |
| No name or email in response | The link can be shared; showing all members' emails is a privacy leak |
| One user, one share | Otherwise, one person could claim the entire group and the "split" concept would be void |
| 404 on cancel (not 403) | Same pattern as the rest of the project — don't even reveal existence |
| Claimer check on `pay` | Cannot pay for someone else's share |

---

## Proof

### 1. Three scenarios, via real HTTP

```
A. Everyone paid -> all confirmed
   Group created: 3 shares, seats ['A-4', 'A-5', 'A-6']
   Seat statuses: ['group_held', 'group_held', 'group_held']
     paid 1/3 -> group 'collecting'  | seats ['group_held', 'group_held', 'group_held']
     paid 2/3 -> group 'collecting'  | seats ['group_held', 'group_held', 'group_held']
     last person pays...
     paid 3/3 -> group 'confirmed'
     seats ['booked', 'booked', 'booked']
     bookings created: 3 (one for each user)

B. Deadline passed
   1 person pays -> paid 1/3, group 'collecting'
     group -> 'expired'
     share statuses: ['refunded', 'unpaid', 'unpaid']
     seats -> ['available', 'available', 'available']

C. One share, two people simultaneously
   two parallel claims -> [200, 409]
   200 received: 1
```

Note: Even with 2/3 paid, **no seat is booked** — this is the true proof of "all or nothing".

### 2. ⭐ Confirm vs expiry race — 60 runs

Last payment and expiry job at the exact same moment (synced via barrier, with jitter on expiry so both paths are tested):

```
20 runs — confirmed: 15, expired: 5, stuck: 0
Invariant violations: 0

20 runs — confirmed: 16, expired: 4, stuck: 0
20 runs — confirmed: 17, expired: 3, atke: 0
```

Every run checks:

| Group | Invariant |
|---|---|
| `confirmed` | all seats `booked`, booking created for every share |
| `expired` | all seats `available`, no bookings, paid share refunded |
| `collecting` | **never** — no one won, this is a failure |

Before adding `FOR UPDATE`, this failed 1 out of 20 times.

### 3. Tests

**79/79 pass** (69 previous + 10 new).

```bash
docker compose exec backend python -m pytest tests/ -q -k "group or share"
```

New tests:
- `test_group_holds_seats_without_booking_them`
- ⭐ `test_partial_payment_confirms_nobody` — nothing happens on 2 out of 3
- `test_all_paid_confirms_everyone`
- `test_expired_group_releases_seats_and_refunds`
- `test_pending_payment_dies_with_the_group`
- ⭐⭐ `test_late_webhook_after_expiry_is_refunded_not_booked`
- ⭐⭐ `test_confirm_and_expiry_race_has_exactly_one_winner`
- `test_broken_group_does_not_leave_pending_payments`
- `test_only_one_person_can_claim_a_share`
- `test_cannot_pay_someone_elses_share`
- `test_group_creation_is_all_or_nothing`
- `test_unknown_share_token_is_404`
- `test_only_creator_can_cancel`

---

## What broke (and what was learned)

### 1. Table order in migration

```
psycopg2.errors.UndefinedTable: relation "group_bookings" does not exist
```

Autogenerate placed `group_shares` first, even though its FK is on `group_bookings`. **Alembic does not understand dependency order when both tables are created in the same revision.** Had to reorder manually.

### 2. `ck_seat_status` — third time

Autogenerate did not add `group_held` to the check constraint. The same limitation as [Phase 11](11-payments.md) with `payment_pending`: **autogenerate does not compare the text INSIDE an existing check constraint.** Without this, the app would run, but the first group booking would trigger a `CheckViolation` — at runtime, not during migration.

### 3. ⭐ Dangling pending payments

Found while writing the race test:

```
duplicate key value violates unique constraint "uq_one_pending_payment_per_seat"
```

When a group broke, its shares' **pending** payments remained hanging. Two consequences:

1. A new checkout could not be created for that seat (the partial unique index from [Phase 11](11-payments.md) blocks it). **The seat looks `available` but cannot be purchased** — the worst kind of bug, as the UI looks fine.
2. A user could complete an old checkout page and pay for a **dead group**.

Fix: `break_group` now expires those payments.

### 4. ⭐⭐ TOCTOU caught by race test

Detailed above. Lesson: **conditional UPDATE is only sufficient when both racers are deciding on the same row.** If there are two different rows, `FOR UPDATE` is required.

And no normal test catches this bug — it appeared 1 out of 20 concurrent runs.

### 5. A test fix revealed a behavior improvement

After fixing Bug 3, `test_payment_after_expiry_is_refunded_not_booked` started failing: the share was `unpaid`, not `refunded`.

The test wasn't wrong — **the behavior had improved**. Now that the pending payment expires, `/simulate` doesn't even reach it, and the user's money **is not taken at all**. Not charging is better than refunding.

However, the real gateway doesn't stop just because we close our end — the webhook can arrive late. Therefore, the test was split into two:

- `test_pending_payment_dies_with_the_group` — checkout is closed
- `test_late_webhook_after_expiry_is_refunded_not_booked` — late webhook is refunded

Understanding what the system did was better than just "fixing" the test.

### 6. Group tests started hitting 429s

Passed individually, failed in the suite — all `429`. BOOKING limit is 5 bursts/user, and the group test makes many calls with one user (create + checkout for each share). Previous booking tests had already emptied the bucket.

Fix: The fixture only clears `rl:user:*`. It does not touch `rl:login:*` — the brute-force test relies on that.

---

## What was intentionally NOT built

This must be written, otherwise the doc is lying:

- **No real refund API call.** In the mock provider, `_refund_share` only writes the status. In a real gateway, refund confirmation also comes **via webhook** — meaning another state like `refund_pending` would be needed, just like payments. I did not build that state machine.
- **No email notification.** "Your friend has paid", "1 hour left" — the real product is incomplete without these. The outbox pattern ([Phase 12](12-background-tickets.md)) is already there, so integrating it will be easy.
- **No partial refund / grace period.** The deadline is strict. In a real product, there might be some flexibility if "the last person is 5 minutes late".

---

## Files

**New:**
| File | What |
|---|---|
| `backend/groups.py` | Core logic — create, claim, confirm, break, expire |
| `backend/routers/group_bookings.py` | HTTP layer |
| `frontend/src/pages/GroupBooking.jsx` | Share link page |

**Modified:**
| File | What |
|---|---|
| `backend/models.py` | `GroupBooking`, `GroupShare`, `SEAT_GROUP_HELD`, `Payment.group_share_id` |
| `backend/schemas.py` | `GroupCreate`, `GroupOut`, `GroupShareOut` |
| `backend/routers/payments.py` | Fulfillment path for group shares |
| `backend/worker.py` | `expire_groups` cron |
| `frontend/src/booking/BookingContext.jsx` | `startGroup()` |
| `frontend/src/components/{HoldCard,SeatGrid}.jsx` | Entry point + `group_held` color |
| `frontend/src/App.jsx`, `api.js` | Route + API calls |

---

## Related

- [Phase 11 — Payments](11-payments.md) — webhook source of truth, which this relies on
- [Phase 14 — Dynamic Pricing](14-dynamic-pricing.md) — same principle for price freezing
- [Phase 15 — Locking Benchmark](15-locking-benchmark.md) — optimistic vs pessimistic, and the exception here
- [Phase 12 — Background Tickets](12-background-tickets.md) — ARQ worker where the cron is attached
- [Interview Prep](../interview-prep.md) — "all or nothing" question
