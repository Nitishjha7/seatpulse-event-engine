# SeatPulse — Development Roadmap

The complete plan, phase by phase. Every phase ends with a **proof** — do not proceed to the next phase until you have it.

**Target (resume bullets):**
1. Real-time seat grid updates via WebSockets
2. Race condition prevention — Redis locking + PostgreSQL optimistic locking
3. Pydantic validation, fast API responses, auto OpenAPI docs
4. Multi-container Docker Compose architecture

---

> **For testing anything → [testing.md](reference/testing.md)** — all commands in one place.
> **Interview preparation → [interview-prep.md](interview-prep.md)** — 50+ Q&A with real metrics.

## Frontend implementation index

React work is distributed across phases; use this index:

| Frontend feature | File | Location |
|---|---|---|
| Vite + React setup, via Docker | — | [Docker setup](setup/01-docker-setup.md) |
| Tailwind v4, `vite.config.js`, `usePolling` | `vite.config.js`, `index.css` | [Phase 1](phases/01-frontend-backend-connect.md) Step 5-7 |
| `api.js` — centralized API calls | `api.js` | [Phase 1](phases/01-frontend-backend-connect.md) Step 8 |
| Health card, three states | `App.jsx` | [Phase 1](phases/01-frontend-backend-connect.md) Step 10 |
| Central error handling (`detail`, `error.status`) | `api.js` | [Phase 3](phases/03-api-and-seat-grid.md) Step 5 |
| **Seat grid** — row splitting, colors | `SeatGrid.jsx` | [Phase 3](phases/03-api-and-seat-grid.md) Step 6 |
| Booking panel, `Promise.all`, 409 handling | `BookingPanel.jsx`, `App.jsx` | [Phase 3](phases/03-api-and-seat-grid.md) Step 7-8 |
| **Hold + live countdown**, `beforeunload` release | `App.jsx`, `BookingPanel.jsx` | [Phase 4](phases/04-redis-locking.md) Step 5 |
| **`useWebSocket` hook** — reconnect, backoff, refs | `hooks/useWebSocket.js` | [Phase 5](phases/05-websockets.md) Step 4 |
| Live updates, derived counts | `App.jsx` | [Phase 5](phases/05-websockets.md) Step 5 |
| **Token in memory, 401 retry** | `api.js` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| **AuthContext** — session restore, silent refresh | `auth/AuthContext.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| Login/signup page, Google button | `auth/AuthPage.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| Auth gate, `key={user.id}` | `App.jsx` | [Phase 7](phases/07-auth-google-oauth.md) → Frontend |
| **Routing + AppShell** (sidebar, topbar) | `layout/*` | [Phase 8](phases/08-dashboard-ui.md) Step 3 |
| **BookingContext** — shared state, single WebSocket | `booking/BookingContext.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 2 |
| Theme tokens, glow, scrollbar | `index.css` | [Phase 8](phases/08-dashboard-ui.md) Step 4 |
| Hero banner (CSS + SVG, no images) | `components/EventHero.jsx` | [Phase 8](phases/08-dashboard-ui.md) Step 5 |
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
| **Payment return page** — polling, no decision logic | `pages/PaymentReturn.jsx` | [Phase 11](phases/11-payments.md) Step 7 |
| `payForSeat()` — redirect to gateway | `booking/BookingContext.jsx` | [Phase 11](phases/11-payments.md) Step 7 |
| Ticket download via blob (header + navigation) | `api.js`, `components/BookingsList.jsx` | [Phase 12](phases/12-background-tickets.md) Step 8 |
| **Camera QR scan** — native BarcodeDetector, no library | `pages/gate/GatePortal.jsx` | [Phase 13](phases/13-gate-checkin.md) Step 6 |
| **Group share page** — polling, countdown, per-share pay | `pages/GroupBooking.jsx` | [Phase 17](phases/17-group-booking.md) |
| **Layout builder** + live preview (form, no drag-drop) | `components/LayoutBuilder.jsx` | [Phase 18](phases/18-seat-layout.md) |
| **NL search box** — renders only if key exists | `components/SeatSearch.jsx` | [Phase 19](phases/19-nl-seat-search.md) |
| **AI draft box** + "read before publish" warning | `components/AiDraft.jsx` | [Phase 20](phases/20-ai-event-copy.md) |
| Interpretation chips — query intent display | `components/SeatSearch.jsx` | [Phase 19](phases/19-nl-seat-search.md) |
| Grid sections + aisles, fallback for old events | `components/SeatGrid.jsx` | [Phase 18](phases/18-seat-layout.md) |
| `startGroup()` — auto-selects adjacent seats | `booking/BookingContext.jsx` | [Phase 17](phases/17-group-booking.md) |
| **Live surge banner** + honest "N seats left at this price" | `components/PricingBanner.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| `seatPrice()` — centralized pricing logic | `booking/BookingContext.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| Price calculation **not** client-side (JS vs Python rounding) | `booking/BookingContext.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |
| Surge toggle + slider in create-event | `pages/organizer/CreateEvent.jsx` | [Phase 14](phases/14-dynamic-pricing.md) |

> **Plan beyond Phase 7** — 13 new features across 4 tracks — see "Roadmap → Planned" in [../README.md](../README.md).
> That section details the problem + approach for every feature. This table only tracks what is **already built**.

## Progress

| Phase | Task | Status |
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
| 20 | AI event copy | ✅ Done — [Phase 20 — AI Event Copy](phases/20-ai-event-copy.md) |
| — | **Poster generator NOT built** — Gemini free tier has image quota limits (all image models return 429). Requires paid tier. |
| — | **Follow-up:** `pricing_state()` runs twice per booking (6 redundant queries). Identified by Phase 15 query-count analysis. |

---

## Phase 0 — Setup ✅

Completed. Details: [Docker setup](setup/01-docker-setup.md) and [Git & GitHub setup](setup/02-git-and-github.md)

- Dockerized FastAPI (`python:3.11-slim`) + Vite React (`node:20-alpine`)
- `docker-compose.yml` — both services, live reload via volume mounts
- CORS enabled, `/api/health` endpoint
- Git repo, `.gitignore`, `.dockerignore`, README

---

## Phase 1 — Frontend ↔ Backend Connect

**Why first:** Until they communicate, everything else is blind. This is the first "everything connected" moment.

### Tasks
1. `App.jsx` with `fetch("http://localhost:8000/api/health")` — display status on screen
2. Install Tailwind CSS (`npm install -D tailwindcss @tailwindcss/vite`)
3. Backend `.env` support — add `pydantic-settings`, remove hardcoded values
4. Create `.env.example` (commit to Git, not `.env`)

### Proof
Browser displays "Backend: healthy"; stopping the backend displays "Backend: offline".

---

## Phase 2 — PostgreSQL + SQLAlchemy Models

> ⚠️ **Most important phase. Do not rush.** Bullet 2 relies entirely on this design. If you fail here, Redis won't save you.

### Tasks
1. `docker-compose.yml` with `postgres:16` service:
   - Named volume (data persistence)
   - Credentials from `.env`
   - `healthcheck` (ensure backend waits for DB)
2. `requirements.txt`: `sqlalchemy`, `psycopg2-binary`, `alembic`, `pydantic-settings`
3. `app/database.py` — engine, SessionLocal, `get_db()` dependency
4. **Models** (`app/models.py`):

| Model | Required fields |
|---|---|
| `User` | id, email (unique), hashed_password, created_at |
| `Event` | id, name, venue, starts_at, total_seats |
| `Seat` | id, event_id, row, number, **status**, **version**, locked_by, locked_until |
| `Booking` | id, user_id, seat_id, status, created_at |

5. **Three critical elements in the `Seat` model:**

```python
status  = Column(String, default="available")  # available | locked | booked
version = Column(Integer, default=0, nullable=False)   # optimistic locking
__table_args__ = (UniqueConstraint("event_id", "row", "number"),)
```

| Element | Why |
|---|---|
| `status` | Current state of the seat |
| `version` | Increments on every update. If two users update simultaneously, the version mismatch causes one to fail. **This is optimistic locking.** |
| `UniqueConstraint` | Final safety net. Even if application logic fails, the database prevents duplicates. |

6. Alembic setup + first migration
7. Seed script — 1 dummy event + 100 seats (10×10 grid)

### Proof
```bash
docker compose exec backend alembic upgrade head
docker compose exec db psql -U postgres -d seatpulse -c "SELECT count(*) FROM seats;"
```
Should return 100.

---

## Phase 3 — Pydantic Schemas + CRUD + Seat Grid UI

**Base for Bullet 3.** The seat grid needs data.

### Backend
1. `app/schemas.py` — `EventOut`, `SeatOut`, `BookingCreate`, `BookingOut`
2. Refactor `main.py` — `app/routers/events.py`, `app/routers/seats.py`, `app/routers/bookings.py`
3. Routes:

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/events` | All events |
| GET | `/api/events/{id}` | Event details |
| GET | `/api/events/{id}/seats` | All seats + status for event |
| POST | `/api/bookings` | Create booking |
| GET | `/api/bookings/me` | My bookings |

### Frontend
4. `api.js` — axios instance, base URL from `.env`
5. Event list page
6. **Seat Grid component** — 10×10 grid, color-coded by status:
   - green = available, yellow = locked, red = booked
7. Seat click → selected state

### Proof
Full API visible in `/docs`. Invalid body returns **422** via Pydantic. Seat grid renders in browser.

---

## Phase 4 — Redis + Concurrency Logic

> **Most frequently asked in interviews.** Understand this, do not copy-paste.

### Tasks
1. `docker-compose.yml` with `redis:7-alpine` service
2. `requirements.txt`: `redis`
3. `app/redis_client.py` — connection

### Acquire lock — single atomic command

```python
ok = r.set(f"seat:{seat_id}:lock", user_id, nx=True, ex=300)
```

| Flag | Purpose |
|---|---|
| `nx=True` | Set only if key **does not exist**. If two users try simultaneously, only one wins in Redis — because this is a single atomic operation. |
| `ex=300` | Auto-delete in 5 minutes. User leaves cart? Seat becomes available automatically — no cleanup job needed. |

`ok` False received → seat held by someone else → **409 Conflict**

### Release lock — via Lua script

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
  return redis.call("del", KEYS[1])
else
  return 0
end
```

**Why Lua:** A simple `DEL` risks deleting someone else's lock if yours expired. Lua script performs check+delete atomically.

### Confirm booking — optimistic locking

```sql
UPDATE seats
SET status = 'booked', version = version + 1
WHERE id = :seat_id AND version = :expected_version AND status != 'booked'
```

`rowcount == 0` → someone else beat you to it → **409**

### Why two layers?

| Layer | Purpose |
|---|---|
| Redis lock | **Fast rejection** — 4999 of 5000 requests never reach the DB. Protects the DB. |
| DB optimistic lock | **Correctness** — If Redis restarts, locks expire, or bugs occur, the DB prevents invalid bookings. |

Redis only = fast but potentially incorrect. DB only = correct but slow. **You need both** — this is the interview answer.

### Routes
- `POST /api/seats/{id}/lock`
- `DELETE /api/seats/{id}/lock`
- `POST /api/bookings` (verify lock before booking)

### Proof
Fire requests for the same seat from two terminals simultaneously — one gets 200, the other 409.

---

## Phase 5 — WebSockets + Broadcasting

**Bullet 1.** Previously, seat status required a refresh; now it is live.

### Backend
1. `app/websocket.py` — `ConnectionManager` class:
   - `connect(ws, event_id)` — rooms per event
   - `disconnect(ws)`
   - `broadcast(event_id, message)`
2. `WS /ws/events/{event_id}` endpoint
3. Broadcast on seat lock / unlock / book:
```json
{ "type": "seat_locked", "seat_id": 42, "status": "locked" }
```
4. Cleanup on disconnect (prevent dead connections)

### Frontend
5. `useWebSocket` hook:
   - connect on mount, close on unmount
   - update seat state on message
   - **reconnect with backoff** (1s, 2s, 4s... retry on disconnect)
6. Smooth color transition in seat grid

### Proof
**Open two browser tabs.** Click a seat in one — it turns yellow in the other **instantly**, without refresh.

---

## Phase 6 — Load Testing + Proof

> Without this, bullets 2 and 3 are just claims. This phase makes them **true**.

### Tasks
1. Add `locust`, write `locustfile.py`
2. **Test: 500 concurrent users, same seat**
   - Expected: exactly **1** booking success, **499** rejected
   - DB check: `SELECT count(*) FROM bookings WHERE seat_id = X` → 1
3. **Measure response time** — bullet says "sub-50ms", you must have this metric.
4. Pytest — concurrency tests (parallel requests via `asyncio.gather`)
5. Redis-based rate limiting
6. **Add load test result/screenshot to README**

### Proof
Locust report screenshot + DB count query output.

> ⚠️ **Do not write "sub-50ms" on your resume** until you have measured it. Interviewers will ask "how did you measure it?" — if you don't have an answer, your other bullets lose credibility.

---

## Final Architecture (Post-Phase 6)

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

## Task Order (Summary)

```
✅ 0. Docker + React + FastAPI skeleton
   1. Frontend ↔ Backend connect          ~30 min
   2. PostgreSQL + models                 ← spend time here
   3. Pydantic + CRUD + Seat Grid UI
   4. Redis + concurrency logic           ← interview core
   5. WebSockets + broadcasting
   6. Load test + proof
```

**Biggest mistake:** People jump straight to Phase 4-5 (Redis + WebSocket) because they look cool. Without a solid Phase 2 (DB design), it's just a facade — and interviewers will dig exactly there.
