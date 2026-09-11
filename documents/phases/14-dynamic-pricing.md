# Phase 14 — Dynamic Pricing

> Demand increases price. However, the price shown to the user is the price charged.

---

## The Problem

Previously, every seat had a fixed price. Real-world ticketing does not work this way—airlines, Uber, and BookMyShow all increase prices based on demand.

When building this feature, the real question was **not** "how to increase the price" (that is a three-line formula). The real questions were:

1. **Where to store the price?** Should we keep updating the `price` column for every seat?
2. **What if the price changes during checkout?** A user sees ₹800, holds the seat, and reaches the payment page—but by then, 5 more seats have sold. Is it now ₹920?

The second question is the core of this phase. Getting the first wrong leads to messy code. Getting the second wrong leads to **silently overcharging the user**—a bug that leads to legal liability.

---

## Decision 1 — Base price is immutable

The most intuitive approach seems to be:

```python
# ❌ The first thought
seat.price = seat.price * multiplier
db.commit()
```

This is incorrect for four reasons:

| Problem | Consequence |
|---|---|
| **Loss of history** | Past bookings show `amount = ₹800`, but the seat now shows ₹1400. The original price is lost. |
| **Write amplification** | One booking triggers 500 `UPDATE` queries. A flash sale with 500 bookings = 250,000 row updates. |
| **New race condition** | Two parallel bookings now compete on price updates—we created a new contention point. |
| **Rounding drift** | Repeatedly applying `price × 1.1` (e.g., ₹800 → ₹880 → ₹968 → ₹1064) causes compounding, which was not the intent of the formula. |

Therefore:

```
seats.price       = BASE. Immutable. Set by the organizer.
current_price     = base × multiplier, CALCULATED on the fly
booking.amount    = the actual charged amount (already stored)
```

Three distinct entities, three distinct locations. None corrupt the others.

**Cost:** Two count queries per seat list request (`total seats`, `confirmed bookings`). Even for 500 seats, this remains 2 queries, not 500—because the multiplier is event-wide, not per-seat.

📁 [`backend/pricing.py`](../../backend/pricing.py) — pure functions, no DB
📁 [`backend/pricing_state.py`](../../backend/pricing_state.py) — state retrieval from DB

---

## Formula

```python
sold_ratio = confirmed_bookings / total_seats
multiplier = 1 + (sold_ratio × demand_factor)
multiplier = min(multiplier, max_surge)

current_price = round_to_10(base × multiplier)
```

`demand_factor = 0.5` → 1.5× price at sell-out. Linear in between.

**This is intentionally simple.** Real surge pricing involves time-to-event, booking velocity, competitor pricing, and historical demand curves. Without real data, those are just random constants—and being unable to defend "demand forecasting" in an interview is a major liability.

This formula is transparent: we can explain exactly why the price increased.

### A surprise regarding rounding

Python's `round()` uses **banker's rounding**:

```python
round(100.5)  # 100  (not 101!)
round(101.5)  # 102
```

Result: The final price may remain the same even if the multiplier increases slightly. Therefore, `_seats_until_increase()` does not estimate via formula—it increments the price to see when it actually changes.

This was caught during testing—the expectation was `1`, but the result was `2`. The code was correct; the test was wrong. (See "What broke" for details.)

---

## Decision 2 — ⭐ Price is locked upon hold

This is the most critical part of the phase.

```
User A: views seat        -> shows ₹1000
User A: holds seat        -> held_price = 1000  ← LOCKED here
User B,C,D,E: buy 4 seats -> market is now ₹1400
User A: pays              -> charged ₹1000 ✅
```

`seats.held_price` is a nullable column. It is set on hold and becomes `NULL` when the hold is released or expires.

**Why a column and not a calculation?** Because "what the price was at that moment" cannot be recomputed later—demand will have changed. A quote is a **promise**, and promises must be stored.

### The single source of truth for price

```python
# backend/pricing_state.py
def price_now(db, seat) -> float:
    if seat.held_price is not None:
        return float(seat.held_price)          # honor the promise
    event = db.get(Event, seat.event_id)
    return current_price(float(seat.price), pricing_state(db, event))
```

Both bookings and payments call this. Using `seat.price` directly anywhere is now a bug—that is the BASE price, and if dynamic pricing is enabled, the user never saw that number.

### A hidden error in payments

```python
# ❌ Two calls — hold could expire in between
payment = Payment(amount=price_now(db, seat), ...)
session = provider.create_checkout(amount=price_now(db, seat), ...)

# ✅ Fetch once, pass to both
quoted = price_now(db, seat)
payment = Payment(amount=quoted, ...)
session = provider.create_checkout(amount=quoted, ...)
```

In the first example, the gateway would charge ₹1400 while our DB recorded ₹1000. This mismatch would only be caught during reconciliation—by which time the user's money would have already been deducted.

### Lock is cleared on hold release

```python
.values(status=SEAT_AVAILABLE, held_price=None, ...)
```

Without this, a user could `hold → release → hold` to permanently secure the cheapest price, rendering surge pricing useless. This is also included in lazy-expiry cleanup, not just explicit unlocks.

📁 [`backend/routers/seats.py`](../../backend/routers/seats.py)

---

## Decision 3 — What to send via WebSocket

When one booking occurs, the price of **all** seats changes. The naive approach: broadcast a new object for every seat.

500 seats × every booking = 500 messages per booking. In a flash sale, this is a self-inflicted DoS.

The reality: the multiplier is **event-wide**, and the frontend already has the base price. Send an event-level message only:

```json
{
  "type": "pricing_update",
  "pricing": {
    "enabled": true, "multiplier": 1.4, "surge_percent": 40,
    "sold": 4, "total": 10, "seats_until_increase": 1
  }
}
```

One small message vs 500 — the result is identical.

### Making it impossible to forget

```python
# backend/events_broadcast.py
_SOLD_COUNT_CHANGED = ("booked", "cancelled")
...
if action in _SOLD_COUNT_CHANGED:
    _publish_pricing(seat.event_id, info)
```

This list is inside `broadcast_seat_update`, not at call sites. If a new booking route is added tomorrow, the pricing broadcast happens automatically. If left to the call site, someone would forget, and clients would show stale prices after bookings from that route.

---

## Frontend — DO NOT calculate price client-side

This is counter-intuitive. `base × multiplier` is a one-liner and saves a network round-trip. We still avoided it:

```js
Math.round(100.5)   // 101   ← JavaScript
round(100.5)        // 100   ← Python
```

They handle ties differently. The UI would show ₹1010 while the server charged ₹1000. ₹10 seems small—but it breaks the trust of "what you see is what you pay," which is the foundation of this feature.

Therefore: upon receiving `pricing_update`, the **banner updates immediately** (what the user sees), and exact seat prices arrive from the server after a 400ms debounce. Debouncing is used because 20 bookings can occur in one second during a flash sale.

📁 [`frontend/src/booking/BookingContext.jsx`](../../frontend/src/booking/BookingContext.jsx)

### Single source of price logic

```js
export function seatPrice(seat) {
  return seat.held_price ?? seat.current_price ?? seat.price
}
```

If every component calculated its own price, it would be easy to forget `held_price` in one place—leading to the user seeing the wrong price.

### What NOT to show in the UI

Ticketing sites plaster "Only 3 left! 🔥" even if 300 seats are empty. Here, every number comes from the server and is true:

- If `seats_until_increase` is **null** (price won't increase, or max surge reached), that line **is not shown**. Empty space is better than manufactured urgency.
- The HoldCard "🔒 Price locked" badge only appears if the market price is actually higher than the locked price. Claiming "you saved!" in every scenario is a lie.

📁 [`frontend/src/components/PricingBanner.jsx`](../../frontend/src/components/PricingBanner.jsx)

---

## Organizer controls

| Field | Range | Default |
|---|---|---|
| `dynamic_pricing` | on/off | **off** |
| `demand_factor` | 0 – 2.0 | 0.5 |
| `max_surge` | 1.0 – 3.0 | 2.0 |

**Why default off:** Surge is not appropriate for every event. It looks greedy for a free community meetup. The organizer must explicitly enable it.

**Why `max_surge` is a hard ceiling:** Regardless of the formula, it will not exceed this. Without a cap, pricing feels out of control and destroys trust. The upper bound for `demand_factor` is 2.0—accidentally typing `50` would be very costly (resulting in 422 errors).

**Surge knobs can be changed via PATCH, but base price cannot.** The difference: changing the base price invalidates past bookings ("You said the ticket was ₹800, now it says ₹1200"). Turning off surge only affects FUTURE bookings—which every organizer should have the right to do if sales are slow. A `pricing_update` is broadcast immediately upon change.

---

## Proof

10 seats @ base ₹1000, `demand_factor = 1.0`. User A holds one seat, then User B buys four seats.

```
A holds A-1
  quoted price = Rs.1000

B buys 4 seats:
  A-2 booked @ Rs.  1000   | remaining seats now Rs.1100
  A-3 booked @ Rs.  1100   | remaining seats now Rs.1200
  A-4 booked @ Rs.  1200   | remaining seats now Rs.1300
  A-5 booked @ Rs.  1300   | remaining seats now Rs.1400

WebSocket: 5 seat_update, 4 pricing_update
  +10%  sold 1/10  next increase in 1 seat(s)
  +20%  sold 2/10  next increase in 1 seat(s)
  +30%  sold 3/10  next increase in 1 seat(s)
  +40%  sold 4/10  next increase in 1 seat(s)

A's held seat: held_price=Rs.1000   (market is now Rs.1400)
A books -> charged Rs.1000
MATCH — promise honored
```

Two things proven:

1. **Surge is live** — price +10% per booking, and 4 pricing_update messages sent via WebSocket.
2. **Promise honored** — market is 40% higher, but A was charged exactly the ₹1000 shown.

### Tests

**63/63 passed** (50 original, 13 new).

New tests in two parts:

*Formula (pure functions, no DB):*
- `test_multiplier_grows_with_demand`
- `test_max_surge_is_a_hard_ceiling`
- `test_empty_event_does_not_divide_by_zero`
- `test_price_rounds_to_a_clean_number`
- `test_disabled_pricing_never_surges`
- `test_seats_until_increase_counts_forward`
- `test_max_surge_reached_reports_no_further_increase`

*Promise (full HTTP flow):*
- `test_new_event_starts_at_base_price`
- `test_price_rises_after_a_booking`
- ⭐ `test_held_price_survives_a_price_rise` — **most critical**
- `test_releasing_a_hold_drops_the_locked_price`
- `test_organizer_can_turn_surge_off`
- `test_base_price_cannot_be_edited`
- `test_absurd_surge_settings_are_rejected`

```bash
docker compose exec backend python -m pytest tests/test_concurrency.py -q
```

---

## What broke (and what was learned)

### 1. `NOT NULL` in migration — the fourth time

```
column "dynamic_pricing" of relation "events" contains null values
```

The same old pattern, for the fourth time. This should be a reflex now:

```python
op.add_column('events', sa.Column('dynamic_pricing', sa.Boolean(),
              nullable=False, server_default='false'))
op.alter_column('events', 'dynamic_pricing', server_default=None)
```

Apply default → backfill old rows → remove default (so the application decides the value, not the DB).

### 2. Test arithmetic was wrong, code was right

```
assert info.seats_until_increase == 1
E   assert 2 == 1
```

100 seats, factor 0.5, base ₹1000:
- 1 seat sold → 1.005× → ₹1005 → round to 10 → **₹1000** (no change)
- 2 seats sold → 1.010× → **₹1010** ← change here

The answer is 2. The ₹5 difference disappears in rounding to 10.

Admitting fault here is vital: **the test was wrong, not the code.** It would have been easy to "fix" `_seats_until_increase` to pass the test—and then the UI would lie, saying "price will increase on next booking" when it wouldn't. The rounding behavior is now explicitly documented in `pricing.py`.

### 3. Event loop blocked in proof script

The first proof run showed **0 pricing_update messages**, even though prices were changing correctly. It seemed the broadcast was broken.

The real reason: the proof script used a sync `httpx.Client` inside `asyncio.run()`. Sync calls block the entire event loop, so the WebSocket listener coroutine never got a chance to run.

Switching to `httpx.AsyncClient` immediately yielded the 4 messages.

**Lesson:** The proof script is code too. It can have bugs, and the conclusion "the feature is broken" should be checked against the script first. The production code was perfectly fine.

---

## Files

**New:**
| File | Purpose |
|---|---|
| `backend/pricing.py` | Formula. Pure functions, no DB — easy to test |
| `backend/pricing_state.py` | Pricing state from DB; `price_now()` is the single source of truth |
| `frontend/src/components/PricingBanner.jsx` | Live surge indicator |

**Modified:**
| File | Purpose |
|---|---|
| `backend/models.py` | `Event.dynamic_pricing/demand_factor/max_surge`, `Seat.held_price` |
| `backend/schemas.py` | `SeatOut.current_price/held_price`, `PricingOut`, `SeatLockOut.price` |
| `backend/routers/seats.py` | Freeze price on lock, clear on release |
| `backend/routers/bookings.py` | Amount from `price_now()` |
| `backend/routers/payments.py` | Single quote for payment + gateway |
| `backend/routers/organizer.py` | Surge knobs in create/update |
| `backend/events_broadcast.py` | `pricing_update` message |
| `frontend/src/hooks/useWebSocket.js` | Handle new message type |
| `frontend/src/booking/BookingContext.jsx` | `seatPrice()`, debounced refetch |
| `frontend/src/components/{SeatGrid,HoldCard}.jsx` | Displayed price |
| `frontend/src/pages/organizer/CreateEvent.jsx` | Surge toggle + slider |

---

## Related

- [Phase 11 — Payments](11-payments.md) — where the amount originates
- [Phase 05 — WebSockets](05-websockets.md) — pub/sub fan-out reused here
- [Phase 04 — Redis Locking](04-redis-locking.md) — hold lifecycle
- [Interview Prep](../interview-prep.md) — pricing questions
- [Roadmap](../roadmap.md)
