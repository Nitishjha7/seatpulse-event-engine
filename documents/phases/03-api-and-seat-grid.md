# Phase 3 — Pydantic Schemas + CRUD APIs + Seat Grid

[Phase 2 — Postgres + Models](02-postgres-models.md) ke baad ka kaam.

**Kya banega:** kaam karne wala booking system — 10×10 seat grid, click karke book, aur **overselling actually rukega**.

> ⭐ Is phase me pehli baar **concurrency handling live** hai. Phase 2 me constraints banaye the, ab wo use ho rahe hain.

---

## Backend

### Step 1 — `schemas.py` banao

**Models vs Schemas — dono kyu chahiye?**

| | `models.py` | `schemas.py` |
|---|---|---|
| Kya define karta hai | Database ka shape | API ka shape |
| Kis library se | SQLAlchemy | Pydantic |
| Example | `hashed_password` column hai | `UserOut` me wo **nahi** hai |

Alag rakhne ki teen wajah:
1. **Security** — `hashed_password` DB me hai par API se kabhi bahar nahi jana chahiye
2. **Input ≠ Output** — client `BookingCreate` bhejta hai (seat_id, user_id), wapas `BookingOut` milta hai (id, status, amount, created_at)
3. **Validation** — `Field(..., gt=0)` galat data pehle hi rok deta hai, aur FastAPI isi se `/docs` banata hai

```python
class ORMModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
```

> `from_attributes=True` se SQLAlchemy object seedha schema me badal jata hai. Iske bina har field haath se copy karni padti.

Schemas: `EventOut`, `EventDetail`, `SeatOut`, `BookingCreate`, `BookingOut`, `BookingDetail`, `UserOut`
Poora code: [../backend/schemas.py](../../backend/schemas.py)

---

### Step 2 — Routers me todo

Ab tak sab `main.py` me tha. Ab:

```
backend/routers/
├── __init__.py
├── events.py      GET /api/events, GET /api/events/{id}
├── seats.py       GET /api/events/{id}/seats, GET /api/seats/{id}
└── bookings.py    POST/GET/DELETE /api/bookings
```

`main.py` me bas:
```python
app.include_router(events.router)
app.include_router(seats.router)
app.include_router(bookings.router)
```

Har router me:
```python
router = APIRouter(prefix="/api/events", tags=["events"])
```

| Cheez | Kyu |
|---|---|
| `prefix` | Har route me `/api/events` likhne ki zaroorat nahi |
| `tags` | `/docs` me routes group ho jaate hain — dekhne me साफ |

> `main.py` ab sirf app banata hai aur routers jodta hai. Phase 4-5 me `seats.py` me locking aur WebSocket aayega — wahan sab ek file me hota to file 500 line ki ho jati.

---

### Step 3 — API endpoints

| Method | Route | Kaam |
|---|---|---|
| GET | `/api/events` | Saare events |
| GET | `/api/events/{id}` | Event + available/locked/booked counts |
| GET | `/api/events/{id}/seats` | Saari seats (grid isi se banta hai) |
| GET | `/api/seats/{id}` | Ek seat |
| POST | `/api/bookings` | **Seat book karo** |
| GET | `/api/bookings?user_id=` | Meri bookings |
| DELETE | `/api/bookings/{id}` | Cancel |
| GET | `/api/me` | Demo user (auth aane tak) |

**Counts ek hi query me:**
```python
counts = dict(
    db.execute(
        select(Seat.status, func.count(Seat.id))
        .where(Seat.event_id == event_id)
        .group_by(Seat.status)
    ).all()
)
```
Teen alag queries (`available`, `locked`, `booked`) maarne ki zaroorat nahi.

---

### Step 4 — ⭐ Booking route — asli concurrency logic

Poora code: [../backend/routers/bookings.py](../../backend/routers/bookings.py)

**Teen kadam:**

```python
# 1. Sasta check — saaf error message ke liye
if seat.status != SEAT_AVAILABLE:
    raise HTTPException(409, f"Seat available nahi hai (status: {seat.status})")

# 2. LAYER 2 — atomic update, asli faisla yahan hota hai
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

# 3. LAYER 3 — database ka aakhri jaal
try:
    db.commit()
except IntegrityError:
    db.rollback()
    raise HTTPException(409, "Is seat ki booking pehle se maujood hai")
```

**Step 1 ka check kaafi kyu nahi?**

Kyunki step 1 aur step 2 ke beech me **microseconds ka gap** hai. Us gap me dusra request wahi seat le sakta hai:

```
Request A                    Request B
────────────────────────────────────────────
status padha: available
                             status padha: available    <- dono ko available dikha
UPDATE ... version=3
✅ rowcount 1
                             UPDATE ... version=3
                             ❌ rowcount 0  -> 409       <- yahan pakda gaya
```

Step 1 sirf **achha error message** dene ke liye hai. **Asli guarantee step 2 ke atomic UPDATE me hai** — kyunki database ek row pe do UPDATE ek saath nahi chalne deta.

**Layer 3 kab bachata hai?** Jab layer 2 me bug ho, ya do backend server chal rahe hon, ya koi seedha SQL chala de. Tab partial unique index `IntegrityError` deta hai. Ye layer kabhi trigger na ho tab bhi rakhna chahiye — insurance ki tarah.

> **Phase 4 me Redis aane par ye code nahi badlega.** Redis sirf ek fast filter hai jo zyadatar requests ko yahan tak pahunchne hi nahi deta.

**Cancel me ek detail:**
```python
booking.status = BOOKING_CANCELLED   # row delete nahi kar rahe
```
Partial unique index sirf `confirmed` par lagta hai — isliye cancel hone ke baad wahi seat dubara bik sakti hai, aur record bhi bacha rehta hai (audit ke liye).

---

## Frontend

### Step 5 — `api.js` me central error handling

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
      if (body.detail) message = body.detail      // FastAPI ka format
    } catch { /* JSON nahi tha */ }
    const error = new Error(message)
    error.status = res.status                     // 409 alag treat karna hai
    throw error
  }
  return res.json()
}
```

| Cheez | Kyu |
|---|---|
| Ek `request()` wrapper | Har call me `res.ok` aur try/catch likhna nahi padta |
| `body.detail` nikalna | FastAPI errors `{"detail": "..."}` me aate hain. Bina iske UI me "500 Internal Server Error" jaisa bekaar message aata |
| `error.status` | UI me 409 ko alag dikhana hai — wo asli error nahi, **expected** hai |

---

### Step 6 — `SeatGrid.jsx`

**Flat list ko rows me todna:**
```js
const rows = seats.reduce((acc, seat) => {
  ;(acc[seat.row_label] ||= []).push(seat)
  return acc
}, {})
```

Backend already `ORDER BY row_label, seat_number` bhej raha hai, isliye frontend ko sort nahi karna padta.

**Colors ek jagah:**
```js
const SEAT_STYLES = {
  available: 'bg-emerald-600/80 hover:bg-emerald-500 cursor-pointer',
  locked:    'bg-amber-500/80 cursor-not-allowed',
  booked:    'bg-rose-900/60 line-through cursor-not-allowed',
  selected:  'bg-indigo-500 ring-2 ring-indigo-300',
}
```
Ek object me isliye — grid aur legend kabhi alag na dikhein.

Aur ek "Stage" marker upar, warna samajh nahi aata ki aage kaunsi taraf hai.

---

### Step 7 — `BookingPanel.jsx`

Right side: event summary + counts, selected seat, book button, my bookings (cancel ke saath).

Ek cheez jaan-boojh ke dikha rahe hain:
```jsx
<p className="text-xs text-slate-600">version {selectedSeat.version}</p>
```

**Seat ka `version` UI me** — booking ke baad ye number badalta hua dikhega. Interview demo me ye chhoti cheez bahut kaam aati hai, optimistic locking samjhane ke liye.

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

`Promise.all` — teeno requests **parallel** jaati hain, ek ke baad ek nahi.

**409 ko alag treat karna:**
```js
const text = err.status === 409 ? `⚠️ ${err.message}` : err.message
setMessage({ type: 'error', text })
await refresh(event.id, user.id)     // seat ki asli haalat dikhao
```

> ⚠️ Abhi har booking ke baad **poora data dubara** maang rahe hain. Ye Phase 3 tak theek hai. **Phase 5 me WebSocket ise replace karega** — sirf badli hui seat ka update aayega, aur wo bhi bina kisi ke maange.

---

## Step 9 — Rebuild

Naya package koi nahi, sirf naye files — `--reload` khud pick kar lega. Na chale to:

```bash
docker compose restart backend
```

---

## ✅ Proof

### 1. Browser
http://localhost:5173 — 10×10 grid (A-J rows), legend, right side panel.
Seat click karo → panel me dikhegi → **Book Seat** → hari se laal ho jayegi, counts badal jayenge.

### 2. Docs
http://localhost:8000/docs — routes ab **events / seats / bookings / meta** me grouped hain.

### 3. Duplicate booking

```bash
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":1,"user_id":1}'
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":1,"user_id":1}'
```
Pehla `201`, dusra **`409`**.

### 4. ⭐ Asli concurrency test

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
... (19 baar 409)
```

DB me check:
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "SELECT count(*) FROM bookings WHERE seat_id=5 AND status='confirmed';"
```
→ **exactly `1`**

> **Yahi Phase 3 ka asli proof hai.** 20 requests ek saath, ek hi seat, aur database me theek ek booking. Phase 6 me yahi test 500 users ke saath Locust se karenge — number resume pe likhne ke liye.

### 5. Reset (testing ke baad)
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "DELETE FROM bookings; UPDATE seats SET status='available', version=version+1;"
```

---

## Common Problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'routers'` | `routers/__init__.py` file bani hai? Khali honi chahiye par honi chahiye |
| Grid khali dikh raha | Seed nahi chala — `docker compose exec backend python seed.py` |
| "Koi event nahi mila" | Same — seed chalao |
| Seat click par kuch nahi hota | Wo seat `available` nahi hai. Sirf hari seats clickable hain |
| Book karne par CORS error | `backend/.env` me `CORS_ORIGINS` check karo |
| 422 Unprocessable Entity | Body ka shape galat. `/docs` me schema dekho |
| `/docs` me naye routes nahi dikh rahe | `docker compose restart backend` |
| Counts update nahi ho rahe | `refresh()` chal raha hai? Browser console (F12) dekho |

---

## Files jo is phase me bane/badle

```
backend/
├── schemas.py              ← naya
├── routers/
│   ├── __init__.py         ← naya (khali)
│   ├── events.py           ← naya
│   ├── seats.py            ← naya
│   └── bookings.py         ← naya  ⭐ concurrency logic
└── main.py                 ← update (routers + /api/me)

frontend/src/
├── api.js                  ← update (saare endpoints + error handling)
├── App.jsx                 ← update (poora rewrite)
└── components/
    ├── SeatGrid.jsx        ← naya
    └── BookingPanel.jsx    ← naya
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
- [roadmap.md](../roadmap.md) — aage kya

---

**Agla:** Phase 4 — Redis distributed locking. Ab tak seat select karte hi book ho jati hai; Phase 4 me "select karo → 5 min ke liye hold ho jaye → phir pay karo" wala flow aayega.
