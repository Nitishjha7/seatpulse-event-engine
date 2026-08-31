# SeatPulse — Development Roadmap

Poora plan, phase wise. Har phase ke end me ek **proof** hai — wo na mile to agle phase pe mat jaana.

**Target (resume bullets):**
1. Real-time seat grid updates via WebSockets
2. Race condition prevention — Redis locking + PostgreSQL optimistic locking
3. Pydantic validation, fast API responses, auto OpenAPI docs
4. Multi-container Docker Compose architecture

---

> **Kuch bhi test karna ho → [testing.md](reference/testing.md)** — saare commands ek jagah.
> **Interview ki tayyari → [interview-prep.md](interview-prep.md)** — 50+ sawaal-jawab, asli numbers ke saath.

## Frontend kahan-kahan explain hua hai

React ka kaam har phase me thoda-thoda hua hai, isliye ye index:

| Frontend cheez | File | Kahan |
|---|---|---|
| Vite + React setup, Docker se | — | [Docker setup](setup/01-docker-setup.md) |
| Tailwind v4, `vite.config.js`, `usePolling` | `vite.config.js`, `index.css` | [Phase 1](phases/01-frontend-backend-connect.md) Step 5-7 |
| `api.js` — ek jagah se saare calls | `api.js` | [Phase 1](phases/01-frontend-backend-connect.md) Step 8 |
| Health card, teen states | `App.jsx` | [Phase 1](phases/01-frontend-backend-connect.md) Step 10 |
| Central error handling (`detail`, `error.status`) | `api.js` | [Phase 3](phases/03-api-and-seat-grid.md) Step 5 |
| **Seat grid** — rows me todna, colors | `SeatGrid.jsx` | [Phase 3](phases/03-api-and-seat-grid.md) Step 6 |
| Booking panel, `Promise.all`, 409 handling | `BookingPanel.jsx`, `App.jsx` | [Phase 3](phases/03-api-and-seat-grid.md) Step 7-8 |
| **Hold + live countdown**, `beforeunload` release | `App.jsx`, `BookingPanel.jsx` | [Phase 4](phases/04-redis-locking.md) Step 5 |
| **`useWebSocket` hook** — reconnect, backoff, refs | `hooks/useWebSocket.js` | [Phase 5](phases/05-websockets.md) Step 4 |
| Live updates, derived counts | `App.jsx` | [Phase 5](phases/05-websockets.md) Step 5 |
| **Token in memory, 401 retry** | `api.js` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| **AuthContext** — session restore, silent refresh | `auth/AuthContext.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| Login/signup page, Google button | `auth/AuthPage.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| Auth gate, `key={user.id}` | `App.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| **Routing + AppShell** (sidebar, topbar) | `layout/*` | [Phase 8](phases/08-dashboard-ui.md) Step 3 |
| **BookingContext** — shared state, ek WebSocket | `booking/BookingContext.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 2 |
| Theme tokens, glow, scrollbar | `index.css` | [Phase 8](phases/08-dashboard-ui.md) Step 4 |
| Hero banner (CSS + SVG, koi image nahi) | `components/EventHero.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 5 |
| Pages — Dashboard, Events, Bookings, Profile | `pages/*` | [Phase 8](phases/08-dashboard-ui.md) Step 6 |
| **Booking Confirmed modal** + `bookingRef()` | `components/BookingConfirmedModal.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 7 |
| CSS-only confetti (`useMemo`, `--x`/`--r`) | `components/Confetti.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 7 |
| **Event detail page** (`/events/:id`) | `pages/EventDetail.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 8 |
| Animations, `prefers-reduced-motion`, `:focus-visible` | `index.css` | [Phase 8](phases/08-dashboard-ui.md) Step 9 |
| **Role-gated nav**, `RequireRole` (UX only) | `layout/Sidebar.jsx`, `App.jsx` | [Phase 10](phases/10-rbac-organizer.md) Step 7 |
| Create-event form, live seat preview | `pages/organizer/CreateEvent.jsx` | [Phase 10](phases/10-rbac-organizer.md) Step 7 |
| Organizer events + sales bar | `pages/organizer/MyEvents.jsx` | [Phase 10](phases/10-rbac-organizer.md) Step 7 |
| Admin stats, 10s polling | `pages/admin/AdminStats.jsx` | [Phase 10](phases/10-rbac-organizer.md) Step 6 |
| **Mock checkout page** (simulated gateway) | `pages/MockCheckout.jsx` | [Phase 11](phases/11-payments.md) Step 7 |
| **Payment return page** — polls, decide nahi karta | `pages/PaymentReturn.jsx` | [Phase 11](phases/11-payments.md) Step 7 |
| `payForSeat()` — redirect to gateway | `booking/BookingContext.jsx` | [Phase 11](phases/11-payments.md) Step 7 |
| Ticket download via blob (header + navigation) | `api.js`, `components/BookingsList.jsx` | [Phase 12](phases/12-background-tickets.md) Step 8 |
| **Camera QR scan** — native BarcodeDetector, no library | `pages/gate/GatePortal.jsx` | [Phase 13](phases/13-gate-checkin.md) Step 6 |
| **Group share page** — polling, countdown, per-share pay | `pages/GroupBooking.jsx` | [Phase 17](phases/17-group-booking.md) |
| **Layout builder** + live preview (form, drag-drop nahi) | `components/LayoutBuilder.jsx` | [Phase 18](phases/18-seat-layout.md) |
| **NL search box** — key na ho to render hi nahi hota | `components/SeatSearch.jsx` | [Phase 19](phases/19-nl-seat-search.md) |
| Interpretation chips — query ka kya matlab nikala | `components/SeatSearch.jsx` | [Phase 19](phases/19-nl-seat-search.md) |
| Grid me sections + aisles, purane events ka fallback | `components/SeatGrid.jsx` | [Phase 18](phases/18-seat-layout.md) |
| `startGroup()` — saath wali seats khud chunta hai | `booking/BookingContext.jsx` | [Phase 17](phases/17-group-booking.md) |
| **Live surge banner** + honest "N seats left at this price" | `components/PricingBanner.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| `seatPrice()` — ek hi jagah price ka faisla | `booking/BookingContext.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| Price client-side calculate **nahi** karte (JS vs Python rounding) | `booking/BookingContext.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| Surge toggle + slider in create-event | `pages/organizer/CreateEvent.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |

> **Phase 7 ke aage ka plan** — 13 naye features 4 tracks me — [../README.md](../README.md) ke "Roadmap → Planned" section me hai.
> Wahan har feature ka problem + approach likha hai. Yahan wali table sirf jo **ban chuka** hai wo track karti hai.

## Progress

| Phase | Kaam | Status |
|---|---|---|
| 0 | Docker + FastAPI + React skeleton | ✅ Done |
| 0 | Git, ignore files, README | ✅ Done |
| 1 | Frontend ↔ Backend connect | ✅ Done — [Phase 1 — Frontend ↔ Backend](phases/01-frontend-backend-connect.md) |
| 2 | PostgreSQL + SQLAlchemy models | ✅ Code ready — [Phase 2 — Postgres + Models](phases/02-postgres-models.md) |
| 3 | Pydantic schemas + CRUD + Seat Grid UI | ✅ Done — [Phase 3 — API + Seat Grid](phases/03-api-and-seat-grid.md) |
| 4 | Redis locking + concurrency logic | ✅ Done — [Phase 4 — Redis Locking](phases/04-redis-locking.md) |
| 5 | WebSockets + broadcasting + React hook | ✅ Done — [Phase 5 — WebSockets](phases/05-websockets.md) |
| 6 | Load testing + proof | ✅ Done — [Phase 6 — Load Testing](phases/06-load-testing.md) |
| 7 | JWT auth + Google OAuth | ✅ Done — [Phase 7 — Auth + Google OAuth](phases/07-auth-google-oauth.md) |
| 8 | Dashboard UI shell (sidebar, routes, theme) | ✅ Done — [Phase 8 — Dashboard UI](phases/08-dashboard-ui.md) |
| 9 | Rate limiting + idempotency keys | ✅ Done — [Phase 9 — Rate Limit + Idempotency](phases/09-rate-limit-idempotency.md) |
| 10 | RBAC + organizer portal | ✅ Done — [Phase 10 — RBAC + Organizer](phases/10-rbac-organizer.md) |
| 11 | Payments + webhooks | ✅ Done — [Phase 11 — Payments](phases/11-payments.md) |
| 12 | Background queue + QR + PDF ticket | ✅ Done — [Phase 12 — Background Tickets](phases/12-background-tickets.md) |
| 13 | Gate check-in (QR scan) | ✅ Done — [Phase 13 — Gate Check-in](phases/13-gate-checkin.md) |
| 14 | Dynamic pricing + price lock | ✅ Done — [Phase 14 — Dynamic Pricing](phases/14-dynamic-pricing.md) |
| 15 | Locking benchmark (optimistic vs pessimistic) | ✅ Done — [Phase 15 — Locking Benchmark](phases/15-locking-benchmark.md) |
| 16 | Multi-worker deploy + CI | ✅ Done — [Phase 16 — Multi-Worker + CI](phases/16-multiworker-ci.md) |
| 17 | Group booking + split payment | ✅ Done — [Phase 17 — Group Booking](phases/17-group-booking.md) |
| 18 | Visual seat layout builder | ✅ Done — [Phase 18 — Seat Layout](phases/18-seat-layout.md) |
| 19 | Natural-language seat search | ✅ Done — [Phase 19 — NL Seat Search](phases/19-nl-seat-search.md) |
| — | **Follow-up:** `pricing_state()` har booking me do baar chalta hai (6 faaltu queries). Phase 15 ki query-count ne pakda. |

---

## Phase 0 — Setup ✅

Ho chuka. Details: [Docker setup](setup/01-docker-setup.md) aur [Git & GitHub setup](setup/02-git-and-github.md)

- Dockerized FastAPI (`python:3.11-slim`) + Vite React (`node:20-alpine`)
- `docker-compose.yml` — dono services, volume mounts se live reload
- CORS enabled, `/api/health` endpoint
- Git repo, `.gitignore`, `.dockerignore`, README

---

## Phase 1 — Frontend ↔ Backend Connect

**Kyu pehle:** jab tak dono aapas me baat nahi karte, baaki sab kaam andhere me hai. Ye pehla "sab jud gaya" moment hai.

### Kaam
1. `App.jsx` me `fetch("http://localhost:8000/api/health")` — status screen pe dikhao
2. Tailwind CSS install karo (`npm install -D tailwindcss @tailwindcss/vite`)
3. Backend me `.env` support — `pydantic-settings` add karo, hardcoded values abhi se hatao
4. `.env.example` banao (ye Git me jayega, `.env` nahi)

### Proof
Browser me "Backend: healthy" dikhe, aur backend band karo to "Backend: offline" dikhe.

---

## Phase 2 — PostgreSQL + SQLAlchemy Models

> ⚠️ **Sabse important phase. Jaldi mat karna.** Bullet 2 poora isi ke design pe tikta hai. Yahan galti hui to Redis lagane se bhi nahi bachega.

### Kaam
1. `docker-compose.yml` me `postgres:16` service:
   - named volume (data persist rahe)
   - `.env` se credentials
   - `healthcheck` (taaki backend DB ready hone ka wait kare)
2. `requirements.txt`: `sqlalchemy`, `psycopg2-binary`, `alembic`, `pydantic-settings`
3. `app/database.py` — engine, SessionLocal, `get_db()` dependency
4. **Models** (`app/models.py`):

| Model | Zaroori fields |
|---|---|
| `User` | id, email (unique), hashed_password, created_at |
| `Event` | id, name, venue, starts_at, total_seats |
| `Seat` | id, event_id, row, number, **status**, **version**, locked_by, locked_until |
| `Booking` | id, user_id, seat_id, status, created_at |

5. **`Seat` model me ye teen cheezein critical hain:**

```python
status  = Column(String, default="available")  # available | locked | booked
version = Column(Integer, default=0, nullable=False)   # optimistic locking
__table_args__ = (UniqueConstraint("event_id", "row", "number"),)
```

| Cheez | Kyu |
|---|---|
| `status` | Seat abhi kis haalat me hai |
| `version` | Har update pe +1. Do log ek saath update karein to ek ka version match nahi karega → wo fail hoga. **Ye hi optimistic locking hai** |
| `UniqueConstraint` | Aakhri safety net. Application logic fail bhi ho jaye to database khud duplicate rok dega |

6. Alembic setup + pehli migration
7. Seed script — 1 dummy event + 100 seats (10×10 grid)

### Proof
```bash
docker compose exec backend alembic upgrade head
docker compose exec db psql -U postgres -d seatpulse -c "SELECT count(*) FROM seats;"
```
100 aana chahiye.

---

## Phase 3 — Pydantic Schemas + CRUD + Seat Grid UI

**Bullet 3 ka base.** Seat grid ko data kahin se to aana hai.

### Backend
1. `app/schemas.py` — `EventOut`, `SeatOut`, `BookingCreate`, `BookingOut`
2. `main.py` ko todo — `app/routers/events.py`, `app/routers/seats.py`, `app/routers/bookings.py`
3. Routes:

| Method | Route | Kaam |
|---|---|---|
| GET | `/api/events` | Saare events |
| GET | `/api/events/{id}` | Ek event ki detail |
| GET | `/api/events/{id}/seats` | Us event ki saari seats + status |
| POST | `/api/bookings` | Booking banao |
| GET | `/api/bookings/me` | Meri bookings |

### Frontend
4. `api.js` — axios instance, base URL `.env` se
5. Event list page
6. **Seat Grid component** — 10×10 grid, status ke hisaab se color:
   - hara = available, peela = locked, laal = booked
7. Seat click → selected state

### Proof
`/docs` me poora API dikhe. Galat body bhejo to Pydantic **422** de. Browser me seat grid render ho.

---

## Phase 4 — Redis + Concurrency Logic

> **Interview me sabse zyada yahi poocha jayega.** Ye samajh ke likhna, copy-paste mat karna.

### Kaam
1. `docker-compose.yml` me `redis:7-alpine` service
2. `requirements.txt`: `redis`
3. `app/redis_client.py` — connection

### Lock lena — ek hi atomic command

```python
ok = r.set(f"seat:{seat_id}:lock", user_id, nx=True, ex=300)
```

| Flag | Kaam |
|---|---|
| `nx=True` | Sirf tab set karo jab key **exist na kare**. Do log ek saath try karein to Redis me sirf ek jeetega — kyunki ye ek hi atomic operation hai, check aur set alag-alag nahi |
| `ex=300` | 5 minute me apne aap delete. User cart chhod ke chala gaya? Seat khud wapas available ho jayegi — koi cleanup job nahi chahiye |

`ok` False mila → seat kisi aur ke paas hai → **409 Conflict**

### Lock chhodna — Lua script se

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
```

**Kyu Lua:** seedha `DEL` karoge to risk hai ki tumhara lock expire ho chuka ho, kisi aur ne le liya ho, aur tum **uska** lock delete kar do. Lua script me check+delete ek saath (atomic) chalta hai.

### Booking confirm — optimistic locking

```sql
UPDATE seats
SET status = 'booked', version = version + 1
WHERE id = :seat_id AND version = :expected_version AND status != 'booked'
```

`rowcount == 0` → koi aur pehle kar gaya → **409**

### Do layer kyu?

| Layer | Kaam |
|---|---|
| Redis lock | **Fast rejection** — 5000 me se 4999 request DB tak pahunchti hi nahi. DB bach jata hai |
| DB optimistic lock | **Correctness** — Redis restart ho jaye, lock expire ho jaye, ya koi bug ho, DB phir bhi galat booking nahi hone dega |

Sirf Redis = tez par galat ho sakta hai. Sirf DB = sahi par dheema. **Dono chahiye** — ye interview ka jawab hai.

### Routes
- `POST /api/seats/{id}/lock`
- `DELETE /api/seats/{id}/lock`
- `POST /api/bookings` (lock verify karke hi book kare)

### Proof
Do terminal se ek hi seat pe ek saath request maaro — ek 200, dusra 409.

---

## Phase 5 — WebSockets + Broadcasting

**Bullet 1.** Ab tak seat status refresh karne pe hi update hota tha — ab live hoga.

### Backend
1. `app/websocket.py` — `ConnectionManager` class:
   - `connect(ws, event_id)` — event ke hisaab se rooms
   - `disconnect(ws)`
   - `broadcast(event_id, message)`
2. `WS /ws/events/{event_id}` endpoint
3. Seat lock / unlock / book hone par broadcast:
```json
{ "type": "seat_locked", "seat_id": 42, "status": "locked" }
```
4. Disconnect pe cleanup (dead connections list me na pade rahen)

### Frontend
5. `useWebSocket` hook:
   - connect on mount, close on unmount
   - message aane pe seat state update
   - **reconnect with backoff** (connection tootne pe 1s, 2s, 4s... retry)
6. Seat grid me smooth color transition

### Proof
**Do browser tabs kholo.** Ek me seat click karo — dusre me **turant** peela ho jaye, bina refresh ke.

---

## Phase 6 — Load Testing + Proof

> Bina iske bullet 2 aur 3 sirf daawa hai. Ye phase unhe **sach** banata hai.

### Kaam
1. `locust` add karo, `locustfile.py` likho
2. **Test: 500 concurrent users, ek hi seat**
   - Expected: exactly **1** booking success, **499** rejected
   - DB me check: `SELECT count(*) FROM bookings WHERE seat_id = X` → 1
3. **Response time maapo** — bullet me "sub-50ms" likha hai, wo number tumhare paas hona chahiye
4. Pytest — concurrency tests (`asyncio.gather` se parallel requests)
5. Redis-based rate limiting
6. **README me load test ka result/screenshot daalo**

### Proof
Locust ka report screenshot + DB count query ka output.

> ⚠️ Resume pe **"sub-50ms" tab tak mat likhna** jab tak actually maap na lo. Interviewer poochta hai "kaise measure kiya?" — jawab na ho to baaki bullets pe bhi shak jata hai.

---

## Final Architecture (Phase 6 ke baad)

```
┌─────────────┐         HTTP + WebSocket        ┌──────────────┐
│   React     │ ◄─────────────────────────────► │   FastAPI    │
│  (Vite)     │                                  │   (ASGI)     │
│ Seat Grid   │                                  └──────┬───────┘
└─────────────┘                                         │
                                            ┌───────────┴───────────┐
                                            │                       │
                                    ┌───────▼──────┐      ┌─────────▼────────┐
                                    │    Redis     │      │   PostgreSQL     │
                                    │ Seat locks   │      │ Events, Seats,   │
                                    │ (NX + EX)    │      │ Bookings         │
                                    │ Fast reject  │      │ version column   │
                                    └──────────────┘      └──────────────────┘
```

---

## Kaam ka Order (short)

```
✅ 0. Docker + React + FastAPI skeleton
   1. Frontend ↔ Backend connect          ~30 min
   2. PostgreSQL + models                 ← yahan time lagana
   3. Pydantic + CRUD + Seat Grid UI
   4. Redis + concurrency logic           ← interview ka core
   5. WebSockets + broadcasting
   6. Load test + proof
```

**Sabse badi galti jo log karte hain:** Phase 4-5 (Redis + WebSocket) dikhne me sabse cool hain, isliye log seedha wahan kood jaate hain. Par bina solid Phase 2 (DB design) ke wo sirf dikhawa hai — aur interviewer exactly wahi khodta hai.
