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
│   ├── payments.py         # Gateway providers + webhook signature verification
│   ├── reconcile_payments.py # Safety net for missed webhooks
│   ├── worker.py           # ARQ background worker
│   ├── tickets.py          # QR + PDF rendering, email outbox
│   ├── websocket.py        # Connection manager + Redis pub/sub fan-out
│   ├── routers/            # auth, events, seats, bookings, organizer, admin
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
│   │   ├── pages/          # Dashboard, Events, MyBookings, Profile, organizer/, admin/
│   │   ├── components/     # SeatGrid, HoldCard, EventHero, BookingConfirmedModal, …
│   │   ├── hooks/          # useWebSocket (reconnect with backoff)
│   │   ├── App.jsx         # Routes + auth gate
│   │   └── api.js          # Single place for backend calls
│   └── Dockerfile
├── loadtest/               # Locust scenarios
├── docker-compose.yml      # Postgres + Redis + backend + worker + frontend
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

### Payments

Booking is confirmed by a **signature-verified webhook**, never by the browser redirect. That redirect is only a "thank you" page: if a user pays and closes the tab it never arrives, yet the booking still happens — and someone opening the success URL directly gets nothing, because the page only *asks* for status rather than granting it.

The seat moves `locked → payment_pending → booked`, and falls back to `available` if the payment fails or the window expires. Fulfilment is **idempotent** — webhooks are at-least-once, so a repeated event returns the original booking instead of creating a second one, enforced by a unique `provider_ref`. Signature verification is HMAC-SHA256 over the raw body with a timestamp tolerance (blocking replays) and `compare_digest` (blocking timing attacks).

Webhooks can still be missed, so [`reconcile_payments.py`](backend/reconcile_payments.py) sweeps expired pending payments, asks the gateway what actually happened, and either fulfils or releases the seat. The webhook is the fast path; reconciliation is the safety net.

Card details never reach the server — checkout is hosted, which keeps the application out of PCI scope. **Leave `STRIPE_SECRET_KEY` empty and a mock provider takes over**, so the whole flow is demonstrable from a fresh clone without any gateway account.

### Background work

Confirming a booking must feel instant, so anything the user does not need *right now* runs outside the request. Generating a QR code, rendering the PDF ticket and sending the email add up to 2-3 seconds — long enough that a synchronous checkout would look broken, and long enough that under load each booking would pin a connection for the whole duration.

[ARQ](backend/worker.py) handles this on the Redis instance already in the stack, so no broker was added. The worker runs from the **same image** as the API with a different command, which keeps models and config identical rather than duplicated.

Two details matter more than the queue itself. **Enqueueing never raises** — it is called after the booking exists and the money has moved, so failing the request there would be the worst possible outcome; a lost job leaves the ticket `pending` and [`retry_pending_tickets.py`](backend/retry_pending_tickets.py) picks it up. And **the job is idempotent** — ARQ retries, so regenerating a ready ticket would rotate its QR token and invalidate the copy the attendee already holds.

The QR encodes a random 32-character token, never the sequential booking id, and tickets are downloadable only by their owner — the QR *is* the entry pass. Email uses an outbox (written to disk and logged) rather than real SMTP; swapping in a provider means changing one function.

### Gate check-in

The same exactly-once problem as seat booking reappears at the gate: a QR screenshot forwarded to five friends must admit exactly one person. It gets the same answer — a single atomic statement, `UPDATE bookings SET checked_in_at = now() WHERE id = ? AND checked_in_at IS NULL`. Ten simultaneous scans of one ticket produce one admission and nine rejections.

The endpoint returns `200` even when entry is refused, with `ok: false` and a reason. Someone working a gate reads a screen, not a status code, and `already_checked_in` is a legitimate business answer rather than a technical error — so both outcomes flow through one code path, and the response carries what the argument at the gate actually needs: when the ticket was used and who scanned it. An unknown token returns nothing beyond "invalid", so tokens cannot be brute-forced by watching responses get more specific.

Scanning uses the browser's native `BarcodeDetector` rather than a ~200KB QR library, with manual token entry always available — needed anyway for a torn ticket or a dead phone.

### Dynamic pricing

Prices rise with demand — `multiplier = 1 + (sold / total) x demand_factor`, capped by a per-event `max_surge`. It is off by default; surge pricing is wrong for a free community meetup, so an organizer has to switch it on.

**A seat's `price` column never changes.** It is the base, and the current price is always computed from it. Rewriting seat prices on every booking would erase the answer to "what was this originally worth", turn one booking into hundreds of row updates, add a fresh contention point between parallel bookings, and compound the multiplier on every pass. Base price, current price and the amount actually charged are three different facts, so they live in three different places.

**The quoted price is locked when the seat is held.** Without that, a user sees Rs.1000, holds the seat, and gets charged Rs.1400 because four other people bought in the meantime — money taken quietly above what was shown. Holding writes `seats.held_price`; releasing or expiring the hold clears it, so hold-release-hold cannot farm the opening price forever. Every price decision routes through one `price_now()` helper, and payments read it once so the payment row and the gateway session can never disagree.

Because the multiplier is one number for the whole event, a booking broadcasts a single event-level `pricing_update` over the existing WebSocket channel rather than one message per seat. The browser does **not** recompute prices from it: JavaScript rounds `100.5` to `101` and Python to `100`, so a client-side calculation would display a price the server would not charge. The banner updates instantly; exact prices come from the server.

Verified end to end: one user held a seat at Rs.1000, four more bookings pushed the market to Rs.1400, and the held seat still charged exactly Rs.1000.

### Running multiple workers

Production runs four uvicorn workers rather than one, which is where a claim made back in Phase 5 finally gets tested. Broadcasts go through Redis pub/sub instead of an in-process dictionary specifically so they survive process boundaries — but with a single worker that had never actually been exercised. A dedicated check now connects twelve WebSocket clients, confirms via `worker_pid` in `/api/health` that they really are spread across processes, books one seat in one worker, and verifies all twelve receive the update. They do.

Multiple workers also break a configuration that was correct with one. Each worker is a separate process with its own connection pool, so `4 × (20 + 20)` asks Postgres for 160 connections against a default limit of 100. Pool size and the admission-control limit are now environment-driven and scaled down per worker, holding the same invariant as before: `MAX_CONCURRENT_REQUESTS < pool_size + max_overflow`. Measured at four workers: 5 connections of 100.

Images are multi-stage with `dev` and `prod` targets. The production backend runs as a non-root user and ships without test tooling; the production frontend is built assets served by nginx — 74MB against 407MB for the dev image. CI asserts both properties rather than trusting the comment.

### Continuous integration

Every push boots the **real** `docker compose` stack, runs migrations, seeds, executes all 66 tests, and finishes with the database integrity check. GitHub's `services:` block would have been simpler, but it only provides the database and cache — the application would run directly on the runner, which is not how it is deployed.

Standing the stack up from an empty volume immediately paid for itself by exposing three bugs that had survived months of development on a long-lived local database: `seed.py` derived its user numbering from a row count, so `user1` and `user2` were never created and 35 tests silently **skipped** while the suite still reported green; the seeded event had a null `organizer_id`, so gate check-in returned 403 and the demo event never appeared in the organizer portal; and two tests used a fixed idempotency key that outlived the run in Redis, making a second run replay a stale response. Details: [documents/phases/16-multiworker-ci.md](documents/phases/16-multiworker-ci.md).

### Locking strategy, measured

The obvious challenge to optimistic locking is "why not `SELECT … FOR UPDATE`?" — so both are implemented against the same booking path, switchable by a query parameter that only exists when `BENCHMARK_MODE` is on, and measured.

The result did not confirm the assumption. Across four runs of 1,000 contended requests each (including one with the run order reversed to rule out warm-up bias), pessimistic locking came out **5-7% faster on p50, within run-to-run variance** — not slower. The reason is visible once you count statements: one booking is **33 SQL statements**, and the locking strategy changes exactly one of them. Losers on the pessimistic path bail out at a status check without issuing a write at all, while optimistic losers still execute an `UPDATE` that matches zero rows.

Two further findings came out of it. With the Redis layer enabled — the actual production path — **1 of 1,433 contended requests reached the booking endpoint**; the database strategy is nearly unreachable by design. And at 300 concurrent users the admission-control semaphore, not the database, is the bottleneck, so all four scenarios landed within 8% of each other.

Optimistic remains the default, for failure mode rather than speed: a losing request returns immediately and frees its connection, where a pessimistic one queues while **holding** one. That cost scales with how long the winner holds the row lock — about 2ms today, which is why it never showed up here, and why it would show up sharply if a transaction ever grew slower. Full numbers and method: [documents/phases/15-locking-benchmark.md](documents/phases/15-locking-benchmark.md).

### Authorization

Three flat roles — `attendee`, `organizer`, `admin` — enforced by a `require_role(...)` dependency and backed by a check constraint on the column, so a typo can never become a role.

**Role and ownership are separate checks.** Holding the organizer role does not mean every event is yours: each organizer endpoint re-fetches the event and confirms `organizer_id` matches (admins bypass this). The two failures also return different codes on purpose — a missing *capability* is `403`, because the endpoint is public knowledge and only permission is absent; a resource that isn't yours returns `404`, so its existence stays hidden.

The frontend hides organizer and admin navigation by role, but that is **UX only** — it is trivially bypassed in DevTools, and the server is the real gate.

| Email | Password | Role |
|---|---|---|
| `demo@seatpulse.dev` | `demo1234` | attendee |
| `organizer@seatpulse.dev` | `demo1234` | organizer |
| `admin@seatpulse.dev` | `demo1234` | admin |

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
| `GET` | `/api/bookings` | Your bookings, each with its ticket status |
| `GET` | `/api/bookings/{id}/ticket` | Download the PDF ticket (owner only) |
| `POST` | `/api/checkin` | Scan a QR at the gate — admits exactly once |
| `DELETE` | `/api/bookings/{id}` | Cancel your booking, releasing the seat |
| `GET` | `/api/health` | Service, database and Redis status |
| `POST` | `/api/payments/checkout` | Start a checkout session for a held seat |
| `POST` | `/api/payments/webhook` | Gateway callback — signature verified, unauthenticated by necessity |
| `GET` | `/api/payments/{id}` | Payment status (the return page polls this) |
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

**Organizer & admin**

| Method | Endpoint | Role | Description |
|---|---|---|---|
| `POST` | `/api/organizer/events` | organizer, admin | Create an event; seats are generated from price tiers |
| `GET` | `/api/organizer/events` | organizer, admin | Your events with sales and revenue (admin sees all) |
| `PATCH` | `/api/organizer/events/{id}` | owner, admin | Edit details — never the layout or pricing |
| `DELETE` | `/api/organizer/events/{id}` | owner, admin | Blocked with `409` while confirmed bookings exist |
| `GET` | `/api/admin/stats` | admin | Platform totals, active Redis locks, live sockets |

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
| `events` | Name, venue, start time, total seats, description, category, surge pricing settings |
| `seats` | Position, **base** price, `status`, **`version`** (optimistic lock), lock holder + expiry, `held_price` (quote locked at hold time) |
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
docker compose exec backend pytest tests/ -v         # 66 tests: auth, RBAC, payments, tickets, check-in, pricing, locking, concurrency
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
- [x] RBAC — attendee / organizer / admin, with ownership checked separately from role
- [x] Organizer portal — create events with price-tier seat generation, track sales; admin platform stats
- [x] Payments — webhook-confirmed checkout with signature verification, idempotent fulfilment, and a reconciliation job for missed webhooks
- [x] Background worker (ARQ) — QR code, PDF ticket and email generated outside the request, with retries and a re-queue safety net
- [x] Gate check-in — camera QR scanning with an atomic single-entry guard, verified with 10 concurrent scans
- [x] Dynamic pricing — demand-based surge pushed over the existing WebSocket channel, with the quoted price locked at hold time so checkout never costs more than what was shown
- [x] Locking benchmark — `SELECT … FOR UPDATE` implemented alongside the optimistic path and measured against it; the difference turned out to be smaller than run-to-run variance, and the writeup says so
- [x] Multi-worker deployment and CI — a production compose running 4 uvicorn workers behind a built frontend, a GitHub Actions pipeline that boots the real stack for every push, and a test proving WebSocket broadcasts cross process boundaries

### Planned

Ordered by dependency — each item leans on the ones above it. None of these are built yet.

- [ ] **Visual seat layout builder** — organizer draws rows, sections and price bands; saved as JSON and expanded into seats server-side
- [ ] **Group booking + split payment** — shareable payment link with a deadline; every share paid or the whole group's seats are released
- [ ] **Natural-language seat finder** — an LLM turns *"3 seats together under ₹1500, centred on the stage"* into structured filters that run as an ordinary query
- [ ] **AI event copy + poster generator** — draft title, description and banner from a short prompt, always editable before publishing
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
