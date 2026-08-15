# 🎟️ SeatPulse — High-Concurrency Event Booking Engine

SeatPulse is a full-stack event ticketing platform designed to handle high-concurrency flash sales. It prevents overselling using Redis key-locking and streams real-time seat state changes via WebSockets.

## 🚀 Tech Stack

- **Backend:** FastAPI (ASGI), Python 3.11, Pydantic v2, SQLAlchemy 2.0, JWT + Google OAuth
- **Database & Cache:** PostgreSQL 16, Alembic migrations, Redis (Distributed Locking)
- **Frontend:** React 19 (Vite), Tailwind CSS v4, WebSockets
- **DevOps:** Docker, Docker Compose

## 🧩 The Problem

When 5,000 people click "Book Seat A-12" at the same instant, a naive `SELECT` → `check` → `UPDATE` flow sells the same seat multiple times. SeatPulse solves this with three layers of defence:

| Layer | Mechanism | Role |
|---|---|---|
| 1 | **Redis distributed lock** (`SET NX EX`) | Fast rejection — most requests never reach the database. TTL releases abandoned carts automatically |
| 2 | **Optimistic locking** (`version` column) | Two concurrent updates: one wins, the other's `WHERE version = ?` no longer matches → `409 Conflict` |
| 3 | **Database constraints** (partial unique index) | Final guarantee — a seat can have only one `confirmed` booking, even if Redis is down or the application logic has a bug |

Correctness never depends on application code alone, and **WebSocket broadcasts** let every connected client see a seat turn grey in real time — before they waste a click on it.

## ⚡ Quick Start

Only **Docker Desktop** is required — no local Node, Python, or PostgreSQL installation needed.

```bash
git clone https://github.com/Nitishjha7/seatpulse-event-engine.git
cd seatpulse-event-engine

# Create env files from the templates
cp .env.example .env
cp backend/.env.example backend/.env
cp frontend/.env.example frontend/.env

docker compose up --build -d

# Set up the database
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

Log in with **`demo@seatpulse.dev`** / **`demo1234`** — the login screen has a one-click button for it.


> On PowerShell use `Copy-Item .env.example .env` instead of `cp`.

| Service | URL |
|---|---|
| Frontend (React) | http://localhost:5173 |
| Backend (FastAPI) | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |
| PostgreSQL | `localhost:5433` |
| Redis | `localhost:6379` |

The seed script creates one event with **100 seats** (rows A–J, 10 seats each).

## 📁 Project Structure

```
seatpulse-event-engine/
├── backend/
│   ├── main.py             # App wiring, WebSocket endpoint, admission control
│   ├── config.py           # Settings via pydantic-settings
│   ├── database.py         # Engine, session, get_db dependency
│   ├── models.py           # User, Event, Seat, Booking
│   ├── schemas.py          # Pydantic request/response contracts
│   ├── auth.py             # Password hashing, JWT, current-user dependency
│   ├── redis_client.py     # Seat locking (SET NX EX + Lua release)
│   ├── rate_limit.py       # Token bucket in Lua
│   ├── idempotency.py      # Replay-safe POST handling
│   ├── websocket.py        # Connection manager + Redis pub/sub fan-out
│   ├── routers/            # auth, events, seats, bookings
│   ├── tests/              # Concurrency + auth test suite
│   ├── seed.py             # Demo event, 100 seats, test users
│   ├── verify_integrity.py # Post-load-test invariant checks
│   ├── alembic/            # Database migrations
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── auth/           # AuthContext (token in memory) + login page
│   │   ├── booking/        # Shared booking state + the single WebSocket
│   │   ├── layout/         # AppShell, Sidebar, Topbar, icons
│   │   ├── pages/          # Dashboard, Events, EventDetail, MyBookings, Profile
│   │   ├── components/     # SeatGrid, HoldCard, EventHero, BookingConfirmedModal, …
│   │   ├── hooks/          # useWebSocket (reconnect with backoff)
│   │   ├── App.jsx         # Routes + auth gate
│   │   └── api.js          # Single place for backend calls
│   └── Dockerfile
├── loadtest/               # Locust scenarios
├── docker-compose.yml      # Postgres + Redis + backend + frontend
└── README.md
```

## 🔐 Authentication

| Token | Stored in | Lifetime | Used for |
|---|---|---|---|
| **Access** | React memory (never `localStorage`) | 30 min | `Authorization: Bearer` on every API call |
| **Refresh** | `httpOnly` cookie, `SameSite=Lax`, `path=/api/auth` | 7 days | Minting a new access token |

- **`localStorage` is avoided deliberately** — any XSS, npm package or extension can read it. An `httpOnly` cookie cannot be read by JavaScript at all.
- **The cookie is not used for normal API calls** either, since cookies ride along automatically and open the door to CSRF. Real work goes through the `Authorization` header.
- **Refresh tokens are revocable.** Each carries a `jti` whitelisted in Redis with a matching TTL; logout deletes the key, so the token dies immediately instead of living out its 7 days. `/refresh` rotates — using a stolen token invalidates the real user's session, which surfaces the theft.
- **Passwords** are hashed with bcrypt (deliberately slow, salt included).
- **Google OAuth** uses the Authorization Code flow — the code is exchanged for user info server-to-server, so `client_secret` never reaches the browser and no token ever appears in a URL. Leave `GOOGLE_CLIENT_ID` empty and the button simply disappears; email/password keeps working.

## 🔌 API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/events` | List all events |
| `GET` | `/api/events/{id}` | Event detail — description, seat counts by status, price range |
| `GET` | `/api/events/{id}/seats` | All seats for an event (powers the grid) |
| `GET` | `/api/seats/{id}` | Single seat |
| `POST` | `/api/seats/{id}/lock` | Hold a seat for 5 minutes — `409` if held by someone else |
| `DELETE` | `/api/seats/{id}/lock` | Release your hold (safe: only your own lock) |
| `GET` | `/api/seats/{id}/lock` | Who holds the seat and for how long |
| `POST` | `/api/bookings` | Book a seat — returns `409` if already taken. Accepts an `Idempotency-Key` header |
| `GET` | `/api/bookings` | Your bookings |
| `DELETE` | `/api/bookings/{id}` | Cancel your booking, releasing the seat |
| `GET` | `/api/health` | Service, database and Redis status |
| `WS` | `/ws/events/{id}` | Live seat updates — pushed on every lock, release, booking or cancellation |

**Auth**

| Method | Endpoint | Description |
|---|---|---|
| `POST` | `/api/auth/register` | Create an account (returns a session) |
| `POST` | `/api/auth/login` | Email + password |
| `POST` | `/api/auth/refresh` | New access token from the refresh cookie (rotates) |
| `POST` | `/api/auth/logout` | Revoke this device's refresh token |
| `POST` | `/api/auth/logout-all` | Revoke every refresh token for the user |
| `GET` | `/api/auth/me` | Current user |
| `GET` | `/api/auth/google/login` | Start Google OAuth |

Everything that acts on a user's behalf — locking, booking, cancelling — takes the user from the token, never from the request body.

**Rate limiting** uses a Redis token bucket implemented in Lua, so the read-modify-write is atomic. Limits are keyed by **user or email, never by IP** — behind a proxy every request appears to come from one address, and `X-Forwarded-For` can be spoofed, so per-IP throttling belongs at the edge (nginx, Cloudflare) rather than in the application. Login only spends its budget on *failed* attempts, so a legitimate user who signs in often is never throttled. Every response carries `X-RateLimit-Limit` and `X-RateLimit-Remaining`; a `429` adds `Retry-After`. If Redis is unreachable the limiter fails **open** — throttling is a protection, not a correctness guarantee, and correctness already has three layers of its own.

**Idempotency:** `POST /api/bookings` accepts an `Idempotency-Key`. The first response is cached in Redis for 24 hours against that key plus a fingerprint of the request body, so a double-click or a network retry returns the original booking (with `X-Idempotent-Replay: true`) rather than creating a second one. Reusing a key with a *different* body is rejected with `422` instead of silently replaying the wrong answer.

Interactive docs at [`/docs`](http://localhost:8000/docs).

WebSocket messages are fanned out through a Redis pub/sub channel per event, so a change processed by one worker reaches clients connected to any other worker:

```json
{ "type": "seat_update", "action": "locked", "seat": { "id": 42, "status": "locked", "locked_by": 3, ... } }
```

## 🗄️ Data Model

| Table | Purpose |
|---|---|
| `users` | Accounts — bcrypt password (nullable for Google users), `google_id`, avatar |
| `events` | Name, venue, start time, total seats, description, category |
| `seats` | Position, price, `status`, **`version`** (optimistic lock), lock holder + expiry |
| `bookings` | Links user ↔ seat, with a **partial unique index** enforcing one confirmed booking per seat |

## 📊 Load Test Results

Measured with [Locust](loadtest/locustfile.py) against the Docker Compose stack (single uvicorn worker, dev mode — all services on one machine).

**Flash sale — 200 authenticated users contending for a single seat:**

| Metric | Value |
|---|---|
| Total requests | 8,154 |
| Throughput | 137 req/s |
| HTTP failures | **0** |
| p50 / p99 | 1,000 ms / 1,400 ms |
| **Confirmed bookings in DB** | **1** |
| Integrity violations | 0 |

Every losing request received a clean `409 Conflict`. Verified with [`verify_integrity.py`](backend/verify_integrity.py), which asserts no seat has more than one confirmed booking and that seat status and bookings stay consistent.

**Realistic browsing — 50 concurrent users:**

| Endpoint | p50 | p75 | p95 | p99 |
|---|---|---|---|---|
| `GET /events/{id}/seats` | 21 ms | 31 ms | 95 ms | 200 ms |
| `GET /events/{id}` | 14 ms | 19 ms | 70 ms | 110 ms |
| `POST /seats/{id}/lock` | 33 ms | 49 ms | 130 ms | 150 ms |
| `POST /bookings` | 41 ms | 55 ms | 83 ms | 150 ms |

```bash
docker compose exec backend python seed.py           # 500 test users (demo@seatpulse.dev / demo1234)
docker compose exec backend python reset_state.py    # clean slate

docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s \
    --host http://backend:8000

docker compose exec backend python verify_integrity.py
docker compose exec backend pytest tests/ -v         # 20 auth + concurrency tests
```

### What load testing actually caught

Three real bugs that smaller tests never reached:

1. **A lost-update race.** `lock_seat`'s `UPDATE` had no status guard, so a seat booked microseconds earlier could be flipped back to `locked` — leaving a confirmed booking against a non-booked seat. A 20-request test never hit that window; 500 concurrent users did. Fixed with the same guarded-update pattern used everywhere else.

2. **bcrypt holding a transaction open.** Login read the user (opening a transaction), then spent ~100 ms hashing before the transaction closed. Under load, `pg_stat_activity` showed **50 of 50 connections `idle in transaction` and exactly 1 active** — nothing was working, everything was holding. Fixed by committing the read before hashing.

3. **In-flight requests outnumbering the connection pool.** Sync routes acquire a DB connection at the start of the request via `get_db`, then wait for a threadpool slot while still holding it — so held connections exceeded the threadpool size and the pool ran dry (`QueuePool limit ... reached`). Growing the pool only moves the cliff, since in-flight requests are unbounded. Fixed with **admission control**: a semaphore middleware caps concurrent requests below the pool size, so requests queue at the door instead of failing inside.

The invariant is now explicit:

```
MAX_CONCURRENT_REQUESTS (30)  <  pool_size + max_overflow (40)  <=  threadpool (40)
```

Result on the same 200-user flash sale: **1,250 requests with 58 failures and a 21 s p99 → 8,154 requests, 0 failures, 1.4 s p99.**

## 🗺️ Roadmap

### Shipped

- [x] Dockerized FastAPI + React skeleton
- [x] CORS-enabled API with health check
- [x] Frontend ↔ backend integration (live status, Tailwind v4)
- [x] PostgreSQL + SQLAlchemy models (User, Event, Seat, Booking)
- [x] Alembic migrations + seed data
- [x] Pydantic schemas + CRUD APIs + interactive seat grid
- [x] Optimistic locking — verified with concurrent requests on a single seat
- [x] Redis distributed seat locking (`SET NX EX` + Lua-based safe release)
- [x] WebSocket real-time seat broadcasting via Redis pub/sub (multi-worker safe)
- [x] Locust load tests + integrity verification + concurrency test suite
- [x] JWT authentication — access token in memory, refresh token in an `httpOnly` cookie, revocable via Redis
- [x] Google OAuth (Authorization Code flow)
- [x] Admission control — bounded concurrency so the connection pool cannot be exhausted
- [x] Dashboard UI — sidebar navigation, routed pages, and a shared booking context so one WebSocket survives navigation
- [x] Event detail page, booking confirmation modal, and motion-safe animations (`prefers-reduced-motion`, `:focus-visible`)
- [x] Rate limiting — Redis token bucket in Lua, scoped to the user or email rather than the IP
- [x] Idempotency keys — a replayed booking returns the original result instead of creating a second one

### Planned

Ordered by dependency — each item leans on the ones above it. None of these are built yet.

- [ ] **RBAC** — organizer / attendee / admin roles behind a `require_role(...)` dependency
- [ ] **Organizer portal** — create events, define seat pricing, view sales
- [ ] **Background task queue** (ARQ + Redis) — QR code, PDF ticket and email generated outside the request, so checkout stays instant
- [ ] **Payment gateway** — Razorpay/Stripe with signature-verified webhooks as the source of truth; the seat stays held until the webhook confirms
- [ ] **QR check-in portal** — mobile scanner with an atomic `valid → checked_in` flip, so one ticket cannot pass two gates
- [ ] **Dynamic pricing** — demand-based surge recomputed on booking events and pushed over the existing WebSocket channel
- [ ] **Visual seat layout builder** — organizer draws rows, sections and price bands; saved as JSON and expanded into seats server-side
- [ ] **Group booking + split payment** — shareable payment link with a deadline; every share paid or the whole group's seats are released
- [ ] **Natural-language seat finder** — an LLM turns *"3 seats together under ₹1500, centred on the stage"* into structured filters that run as an ordinary query
- [ ] **AI event copy + poster generator** — draft title, description and banner from a short prompt, always editable before publishing
- [ ] **Pessimistic locking benchmark** — `SELECT … FOR UPDATE` measured against the current optimistic approach with the existing Locust suite
- [ ] Multi-worker deployment (`--workers`) and CI pipeline
- [ ] Screenshots / demo GIF, and a deployed live demo
- [ ] **Demand forecasting** — base price and sell-out prediction from booking velocity. Last on purpose: without real historical data this produces a plausible-looking number rather than a useful one

---

**Target architecture once the roadmap lands:**

```
[ Organizer Portal ] ──> Layout builder · Pricing · AI-assisted event copy
                                  │
                                  ▼
[ Customer Portal ]  ──> Live seat grid · Redis holds · Dynamic pricing · NL seat search
                                  │
                                  ▼
[ Checkout ]         ──> Payment webhooks · Idempotency · Split-bill links
                                  │
                                  ▼
[ Async Workers ]    ──> QR code · PDF ticket · Email
                                  │
                                  ▼
[ Venue Gate ]       ──> QR scanner · Atomic check-in validation
```

## 🛠️ Common Commands

```bash
docker compose up -d              # start in background
docker compose down               # stop everything
docker compose ps                 # container status
docker compose logs -f backend    # live backend logs
docker compose exec backend bash  # shell into backend
```

**Database**

```bash
docker compose exec backend alembic revision --autogenerate -m "message"
docker compose exec backend alembic upgrade head       # apply migrations
docker compose exec backend alembic current            # current revision
docker compose exec backend python seed.py             # reseed demo data
docker compose exec db psql -U seatpulse -d seatpulse  # psql shell
```

> ⚠️ `docker compose down -v` deletes the Postgres volume along with all data. Use plain `down` unless you intend to wipe the database.
