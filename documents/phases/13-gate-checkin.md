# Phase 13 — Gate Check-in

Follow-up to [12-background-tickets.md](12-background-tickets.md).

**What was built:** Entry validation via QR code scanning — using the camera, with a manual entry fallback.

---

## The problem this solves

Phase 12 generated the QR ticket. However, on the day of the event, the gate staff faces two questions:

1. Is this ticket authentic?
2. **Has this ticket already been used?**

The second question is critical. Taking a screenshot of a QR code and sending it to five friends is the most common form of ticketing fraud.

---

## Concept — same problem, different context

| | Seat booking (Phase 4) | Gate check-in (this phase) |
|---|---|---|
| Invariant | One seat, one confirmed booking | One ticket, **one entry** |
| Race | 5000 people for one seat | Two gates, same QR |
| Solution | Atomic conditional UPDATE | **Exactly the same** |

```sql
-- Seat booking
UPDATE seats SET status='booked' WHERE id=? AND version=?

-- Check-in
UPDATE bookings SET checked_in_at=now() WHERE id=? AND checked_in_at IS NULL
```

In both cases, **read and write are not separate steps**. This prevents any interference.

> ⭐ Interview tip: Make this connection: *"The check-in problem was identical to seat booking — an exactly-once requirement. Therefore, the solution used the same pattern; no new logic was required."*

---

## Step 1 — `checked_in_at` NULL is the guard

```python
checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
checked_in_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
```

**Why not a boolean `is_checked_in`:**

| | Boolean | Timestamp ✅ |
|---|---|---|
| Guard | `WHERE is_checked_in = false` — works | `WHERE checked_in_at IS NULL` — works |
| "When did they arrive?" | ❌ unknown | ✅ known |
| Disputes | Useless | Essential information |

And `checked_in_by` tracks which staff member performed the scan. This is for auditing and identifying who processed the entry in case of duplicates.

---

## Step 2 — Atomic check-in

```python
result = db.execute(
    update(Booking)
    .where(Booking.id == booking.id, Booking.checked_in_at.is_(None))
    .values(checked_in_at=utcnow(), checked_in_by=staff.id)
)

if result.rowcount == 0:
    # Already checked in
    return _result(ok=False, reason="already_checked_in", ...)
```

Two gates simultaneously:
```
Gate A                          Gate B
────────────────────────────────────────────
booking found, checked_in NULL   booking found, checked_in NULL
UPDATE ... WHERE IS NULL        UPDATE ... WHERE IS NULL
✓ rowcount 1 — entry granted    ✗ rowcount 0 — already used
```

---

## Step 3 — ⚠️ Returning 200 even on failure

```python
return CheckInResult(ok=False, reason="already_checked_in", ...)
```

This seems counter-intuitive. The reason:

**The person at the gate does not look at HTTP status codes.** They need a clear answer — "Enter" or "This ticket was used at 7:42 pm".

If we returned 409, the frontend would have to navigate to an error path and fetch the same information again. By including `ok: false` in the body, both cases are handled in the same code path.

**Only genuine errors return non-200** — permissions (403), malformed requests (422).

> This is the distinction between "business outcome" and "technical error." `already_checked_in` is a **valid business response**, not an error.

---

## Step 4 — Role check and ownership

```python
staff: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))
...
if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
    raise HTTPException(403, "This ticket does not belong to your event")
```

Without an ownership check, an organizer could check in tickets for **any** event.

> The same role-vs-ownership distinction as in Phase 10. Role defines *what you can do*, ownership defines *on what*.

**Note:** Here, `organizer`/`admin` are treated as gate staff. In a real deployment, there would be a separate `gate_staff` role assigned by the organizer. That would require additional migration and role management UI — out of scope for now, but noted in the [roadmap](../roadmap.md).

---

## Step 5 — Do not leak info on invalid tokens

```python
if booking is None:
    logger.warning("Check-in: unknown token scanned by user %s", staff.id)
    return _result(ok=False, reason="invalid_ticket")     # nothing else
```

The response contains `None` for `booking_id`, `seat_label`, etc.

⚠️ If we provided a specific message like "token exists but for a different event," someone could **brute-force** tokens to find valid ones. A test case covers this.

---

## Step 6 — Camera scanning: why no library?

```js
const supported = 'BarcodeDetector' in window
const detector = new window.BarcodeDetector({ formats: ['qr_code'] })
const codes = await detector.detect(videoRef.current)
```

**`BarcodeDetector` is built into the browser** — Chrome, Edge, Android. Not in Firefox or Safari.

| Option | Size | Support |
|---|---|---|
| `html5-qrcode` / `jsQR` | ~200KB | All browsers |
| `BarcodeDetector` ✅ | 0 KB | Chrome/Edge/Android |

The gate portal is usually accessed on **specific devices** (staff Android phones or tablets). Adding 200KB is unnecessary overhead.

**Manual entry is required anyway** — for torn QR codes, dead batteries, or blocked camera permissions. The fallback is already in place.

### Two essential details

```js
// 1. Rear camera — standard for gate use
video: { facingMode: 'environment' }

// 2. A QR is visible for multiple frames — prevent 20 requests per scan
if (token !== lastScanned.current.token || now - lastScanned.current.at > 3000) {
    submit(token)
}
```

And cleanup:
```js
useEffect(() => () => stopCamera(), [])
```
Without this, the camera remains active after leaving the page (the phone light stays on) — making the app appear to be spying.

---

## Step 7 — Result card designed for the gate

There is a queue at the gate. Staff need a decision in **2 seconds**:

| Result | Color | Headline |
|---|---|---|
| `checked_in` | 🟢 green | "Enter" |
| `already_checked_in` | 🟡 yellow | "Already used" + **when and by whom** |
| `invalid_ticket` | 🔴 red | "Ticket not valid" |

The seat number is the largest text — as that is the next question.

In the duplicate case, the time and scanner name are shown to resolve arguments: *"I haven't scanned it yet!"* — *"It was used at 7:42 pm."*

---

## ✅ Proof

### 1. Valid ticket

```bash
curl -X POST -H "Authorization: Bearer $ORG" -d "{\"token\":\"$TOKEN\"}" \
  localhost:8000/api/checkin
```
```json
{"ok":true,"reason":"checked_in","booking_ref":"SP00273","seat_label":"B-5",
 "attendee_name":"Demo User","checked_in_at":"2026-08-30T05:00:25Z",
 "scanned_by":"Demo Organizer"}
```

### 2. ⭐ Same QR again

```json
{"ok":false,"reason":"already_checked_in","seat_label":"B-5",
 "checked_in_at":"2026-08-30T05:00:25Z","already_checked_in":true,
 "scanned_by":"Demo Organizer"}
```

Same timestamp — meaning no second entry occurred.

### 3. Fake token

```json
{"ok":false,"reason":"invalid_ticket","booking_id":null,"seat_label":null}
```

Everything is null — no details leaked.

### 4. ⭐ 10 gates simultaneously

```bash
for i in $(seq 1 10); do
  curl -s -X POST -H "Authorization: Bearer $ORG" -d "{\"token\":\"$TOKEN\"}" \
    localhost:8000/api/checkin | grep -o '"reason":"[^"]*"' &
done; wait
```

**Actual output:**
```
"reason":"checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
```

**Exactly one entry.** The same guarantee as in seat booking.

### 5. Test suite

```
49 passed in 42.39s
```

7 new tests — valid check-in, duplicate, **10 concurrent scans**, invalid token (and info leak check), attendee blocked, cancelled booking, stats.

---

## ⭐ Bug: two foreign keys, one confusion

After migration, the app failed to start:

```
sqlalchemy.exc.InvalidRequestError: Could not determine join condition between
parent/child tables on relationship User.bookings — there are multiple foreign
key paths linking the tables.
```

**Reason:** `Booking` now has **two** FKs pointing to `users`:

```python
user_id       -> who booked
checked_in_by -> which staff scanned
```

SQLAlchemy cannot determine which column to use for "user's bookings."

**Fix:**
```python
# User side
bookings = relationship(..., foreign_keys="Booking.user_id")

# Booking side
user = relationship(..., foreign_keys=[user_id])
```

> **Rule:** When a table points to another table with **more than one** FK, you must explicitly define `foreign_keys` on every relationship. This error appears at startup — always perform a health check after migrations.

---

## Interview questions

| Question | Answer |
|---|---|
| "How did you ensure a QR isn't used twice?" | `UPDATE ... WHERE checked_in_at IS NULL` — an atomic statement. If two gates scan simultaneously, only one gets rowcount 1. Tested with 10 concurrent scans: exactly 1 entry. |
| "This sounds familiar." | Yes — it is the exact-once problem from seat booking. The solution uses the same pattern. |
| "Why not a boolean flag?" | The timestamp acts as a guard and provides "when" info. Essential for resolving disputes at the gate. |
| "Why not 409 on failure?" | The person at the gate doesn't see status codes. `already_checked_in` is a valid business response. Both cases are handled in the same code path. |
| "What do you send on an invalid token?" | Only `invalid_ticket` — no details. Prevents brute-forcing. |
| "Which QR library?" | None — native browser `BarcodeDetector`. Saved 200KB. Fallback to manual entry is required anyway. |
| "Can any organizer scan any ticket?" | No — ownership check is in place. Admins can scan all. |
| "What about offline gates?" | Currently requires network. Offline would require pre-downloading tickets and local verification, then syncing — but duplicate detection becomes weaker. That is a separate design problem. |

---

## Common Problems

| Problem | Fix |
|---|---|
| "QR scanning not available" | Use Chrome/Edge, or manual entry |
| Camera won't open | Requires HTTPS or localhost — blocked on insecure origins |
| Request sent on every frame | Check the 3-second dedupe guard |
| `invalid_ticket` on valid QR | Is the ticket `ready`? Check `ticket_status` |
| 403 during scan | Event is not yours — organizers only scan their own events |
| `multiple foreign key paths` error | Add `foreign_keys` to the relationship |

---

## Files

```
backend/
├── routers/checkin.py          ← new ⭐ atomic check-in + stats
├── models.py                   ← checked_in_at, checked_in_by, foreign_keys fix
├── schemas.py                  ← CheckInRequest, CheckInResult
├── main.py                     ← checkin router
├── tests/test_concurrency.py   ← 7 new tests (42 → 49)
└── alembic/versions/...        ← checkin columns

frontend/src/
├── pages/gate/GatePortal.jsx   ← new ⭐ camera scan + manual + result card
├── api.js                      ← checkIn, getCheckinStats
├── App.jsx                     ← /gate route (role-gated)
└── layout/Sidebar.jsx          ← Gate Check-in link
```

---

## The journey is now complete

```
Organizer creates event      (Phase 10)
        ↓
User holds seat              (Phase 4)
        ↓
Payment processed            (Phase 11)
        ↓
Worker sends ticket          (Phase 12)
        ↓
QR scanned at gate           (this phase)
```

Every step has an **exactly-once** guarantee, using the same pattern — atomic conditional UPDATE.

---

## Related

- [04-redis-locking.md](04-redis-locking.md) — same exactly-once, for seats
- [12-background-tickets.md](12-background-tickets.md) — QR generation
- [10-rbac-organizer.md](10-rbac-organizer.md) — role vs ownership
- [../reference/testing.md](../reference/testing.md) — test commands
