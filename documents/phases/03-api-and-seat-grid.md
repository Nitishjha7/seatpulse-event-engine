# Phase 3 — Pydantic Schemas + CRUD APIs + Seat Grid

Follow-up to [Phase 2 — Postgres + Models](02-postgres-models.md).

**Goal:** A functional booking system — 10×10 seat grid, click-to-book, and **actual prevention of overselling**.

> ⭐ This phase introduces **live concurrency handling**. The constraints defined in Phase 2 are now being actively utilized.

---

## Backend

### Step 1 — Create `schemas.py`

**Models vs. Schemas — why do we need both?**

| | `models.py` | `schemas.py` |
|---|---|---|
| Defines | Database shape | API shape |
| Library | SQLAlchemy | Pydantic |
| Example | Contains `hashed_password` | `UserOut` excludes it |

Three reasons for separation:
1. **Security** — `hashed_password` exists in the DB but must never leave via the API.
2. **Input ≠ Output** — The client sends `BookingCreate` (seat_id, user_id) and receives `BookingOut` (id, status, amount, created_at).
3. **Validation** — `Field(..., gt=0)` catches invalid data early, and FastAPI uses this to generate `/docs`.

```python
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

> `from_attributes=True` allows SQLAlchemy objects to convert directly into schemas. Without this, every field must be copied manually.

Schemas: `EventOut`, `EventDetail`, `SeatOut`, `BookingCreate`, `BookingOut`, `BookingDetail`, `UserOut`
Full code: [../backend/schemas.py](../../backend/schemas.py)

---

### Step 2 — Organize Routers

Previously, everything was in `main.py`. Now:

```
backend/routers/
├── __init__.py
├── events.py      GET /api/events, GET /api/events/{id}
├── seats.py       GET /api/events/{id}/seats, GET /api/seats/{id}
└── bookings.py    POST/GET/DELETE /api/bookings
```

In `main.py`:
```python
app.include_router(events.router)
app.include_router(seats.router)
app.include_router(bookings.router)
```

In each router:
```python
router = APIRouter(prefix="/api/events", tags=["events"])
```

| Feature | Why |
|---|---|
| `prefix` | Eliminates the need to write `/api/events` in every route |
| `tags` | Groups routes in `/docs` for better readability |

> `main.py` now only initializes the app and registers routers. In Phases 4-5, `seats.py` will handle locking and WebSockets; keeping them separate prevents a single 500-line file.

---

### Step 3 — API Endpoints

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/events` | All events |
| GET | `/api/events/{id}` | Event + available/locked/booked counts |
| GET | `/api/events/{id}/seats` | All seats (used for grid generation) |
| GET | `/api/seats/{id}` | Single seat |
| POST | `/api/bookings` | **Book a seat** |
| GET | `/api/bookings?user_id=` | My bookings |
| DELETE | `/api/bookings/{id}` | Cancel |
| GET | `/api/me` | Demo user (until auth is implemented) |

**Counts in a single query:**
```python
counts = dict(
    db.execute(
        select(Seat.status, func.count(Seat.id))
        .where(Seat.event_id == event_id)
        .group_by(Seat.status)
    ).all()
)
```
No need for three separate queries (`available`, `locked`, `booked`).

---

### Step 4 — ⭐ Booking route — actual concurrency logic

Full code: [../backend/routers/bookings.py](../../backend/routers/bookings.py)

**Three steps:**

```python
# 1. Cheap check — for clean error messages
if seat.status != SEAT_AVAILABLE:
    raise HTTPException(409, f"Seat available nahi hai (status: {seat.status})")

# 2. LAYER 2 — atomic update, the real decision happens here
result = db.execute(
    update(Seat)
    .where(
        Seat.id == payload.seat_id,
        Seat.version == expected_version,      # <- optimistic lock
        Seat.status == SEAT_AVAILABLE,
    )
    .values(status=SEAT_BOOKED, version=Seat.version + 1)
)
if result.rowcount == 0:
    db.rollback()
    raise HTTPException(409, "Seat abhi abhi kisi aur ne book kar li")

# 3. LAYER 3 — database safety net
try:
    db.commit()
except IntegrityError:
    db.rollback()
    raise HTTPException(409, "Is seat ki booking pehle se maujood hai")
```

**Why isn't the Step 1 check enough?**

Because there is a **microsecond gap** between Step 1 and Step 2. In that gap, another request could claim the seat:

```
Request A                    Request B
────────────────────────────────────────────
read status: available
                             read status: available    <- both see available
UPDATE ... version=3
✅ rowcount 1
                             UPDATE ... version=3
                             ❌ rowcount 0  -> 409       <- caught here
```

Step 1 is only for providing a **helpful error message**. The **real guarantee is in the atomic UPDATE in Step 2** — because the database prevents two concurrent UPDATEs on the same row.

**When does Layer 3 save us?** If there is a bug in Layer 2, multiple backend servers are running, or someone executes raw SQL. The partial unique index triggers an `IntegrityError`. This layer acts as insurance.

> **This code will not change when Redis is added in Phase 4.** Redis will act as a fast filter to prevent most requests from reaching this logic.

**Cancel detail:**
```python
booking.status = BOOKING_CANCELLED   # not deleting the row
```
The partial unique index only applies to `confirmed` status — this allows the seat to be resold after cancellation while keeping the record for audit purposes.

---

## Frontend

### Step 5 — Central error handling in `api.js`

```js
async function request(path, options = {}) {
  const res = await fetch(`${API_URL}${path}`, {
    headers: { "Content-Type": "application/json" },
    ...options,
  })

  if (!res.ok) {
    let message = `${res.status} ${res.statusText}`
    try {
      const body = await res.json()
      if (body.detail) message = body.detail      // FastAPI format
    } catch { /* Not JSON */ }
    const error = new Error(message)
    error.status = res.status                     // Treat 409 differently
    throw error
  }
  return res.json()
}
```

| Feature | Why |
|---|---|
| `request()` wrapper | Avoids repeating `res.ok` and try/catch blocks |
| `body.detail` extraction | FastAPI errors arrive as `{"detail": "..."}`. Without this, the UI shows generic "500 Internal Server Error" messages |
| `error.status` | 409 is an **expected** state, not a system failure |

---

### Step 6 — `SeatGrid.jsx`

**Splitting a flat list into rows:**
```js
const rows = seats.reduce((acc, seat) => {
  ;(acc[seat.row_label] ||= []).push(seat)
  return acc
}, {})
```

The backend sends data `ORDER BY row_label, seat_number`, so the frontend doesn't need to sort.

**Centralized colors:**
```js
const SEAT_STYLES = {
  available: 'bg-emerald-600/80 hover:bg-emerald-500 cursor-pointer',
  locked:    'bg-amber-500/80 cursor-not-allowed',
  booked:    'bg-rose-900/60 line-through cursor-not-allowed',
  selected:  'bg-indigo-500 ring-2 ring-indigo-300',
}
```
Keeps the grid and legend consistent.

---

### Step 7 — `BookingPanel.jsx`

Right side: event summary + counts, selected seat, book button, my bookings (with cancel).

We intentionally expose:
```jsx
<p className="text-xs text-slate-600">version {selectedSeat.version}</p>
```

**Seat `version` in UI** — this number updates after booking. It is a useful detail for interview demos to explain optimistic locking.

---

### Step 8 — `App.jsx`

```js
const refresh = useCallback(async (eventId, userId) => {
  const [eventData, seatData, bookingData] = await Promise.all([
    getEvent(eventId),
    getEventSeats(eventId),
    getMyBookings(userId),
  ])
  ...
}, [])
```

`Promise.all` executes all three requests in **parallel**.

**Handling 409:**
```js
const text = err.status === 409 ? `⚠️ ${err.message}` : err.message
setMessage({ type: 'error', text })
await refresh(event.id, user.id)     // Show actual seat state
```

> ⚠️ Currently, we refetch the **entire dataset** after every booking. This is acceptable for Phase 3. **Phase 5 will replace this with WebSockets** — only the updated seat will be pushed, without manual polling.

---

## Step 9 — Rebuild

No new packages, just new files — `--reload` will pick them up. If it fails:

```bash
docker compose restart backend
```

---

## ✅ Proof

### 1. Browser
http://localhost:5173 — 10×10 grid (A-J rows), legend, right side panel.
Click a seat → appears in panel → **Book Seat** → turns from green to red, counts update.

### 2. Docs
http://localhost:8000/docs — routes are now grouped into **events / seats / bookings / meta**.

### 3. Duplicate booking

```bash
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":1,"user_id":1}'
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":1,"user_id":1}'
```
First returns `201`, second returns `409`.

### 4. ⭐ Concurrency test

**Git Bash:**
```bash
for i in $(seq 1 20); do
  curl -s -o /dev/null -w "%{http_code}\n" -X POST http://localhost:8000/api/bookings \
    -H "Content-Type: application/json" -d '{"seat_id":5,"user_id":1}' &
done; wait
```

**Expected:**
```
201
409
409
... (19 times 409)
```

DB check:
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "SELECT count(*) FROM bookings WHERE seat_id=5 AND status='confirmed';"
```
→ **exactly `1`**

> **This is the proof for Phase 3.** 20 concurrent requests for the same seat, and exactly one booking in the database. In Phase 6, we will repeat this test with 500 users using Locust.

### 5. Reset (after testing)
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "DELETE FROM bookings; UPDATE seats SET status='available', version=version+1;"
```

---

## Common Problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'routers'` | Ensure `routers/__init__.py` exists (it should be empty) |
| Grid is empty | Run seed — `docker compose exec backend python seed.py` |
| "No event found" | Run seed |
| Seat click does nothing | Seat is not `available`. Only green seats are clickable |
| CORS error on booking | Check `CORS_ORIGINS` in `backend/.env` |
| 422 Unprocessable Entity | Incorrect request body. Check schema in `/docs` |
| New routes not in `/docs` | `docker compose restart backend` |
| Counts not updating | Check if `refresh()` is running. Check browser console (F12) |

---

## Files created/modified in this phase

```
backend/
├── schemas.py              ← new
├── routers/
│   ├── __init__.py         ← new (empty)
│   ├── events.py           ← new
│   ├── seats.py            ← new
│   └── bookings.py         ← new  ⭐ concurrency logic
└── main.py                 ← update (routers + /api/me)

frontend/src/
├── api.js                  ← update (all endpoints + error handling)
├── App.jsx                 ← update (full rewrite)
└── components/
    ├── SeatGrid.jsx        ← new
    └── BookingPanel.jsx    ← new
```

---

## Commit

```bash
git add .
git commit -m "Phase 3: Pydantic schemas, CRUD APIs, seat grid with optimistic locking"
git push
```

---

## Related

- [postgres-commands.md](../reference/postgres-commands.md) — queries, reset, constraints
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — next steps

---

**Next:** Phase 4 — Redis distributed locking. Currently, a seat is booked immediately upon selection; Phase 4 will introduce a "select → hold for 5 mins → pay" flow.
