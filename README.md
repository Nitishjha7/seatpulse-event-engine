# 🎟️ SeatPulse — High-Concurrency Event Booking Engine

SeatPulse is a full-stack event ticketing platform designed to handle high-concurrency flash sales. It prevents overselling using Redis key-locking and streams real-time seat state changes via WebSockets.

## 🚀 Tech Stack

- **Backend:** FastAPI (ASGI), Python 3.11, Pydantic v2, SQLAlchemy 2.0
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

> On PowerShell use `Copy-Item .env.example .env` instead of `cp`.

| Service | URL |
|---|---|
| Frontend (React) | http://localhost:5173 |
| Backend (FastAPI) | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |
| PostgreSQL | `localhost:5432` |

The seed script creates one event with **100 seats** (rows A–J, 10 seats each).

## 📁 Project Structure

```
seatpulse-event-engine/
├── backend/
│   ├── main.py             # FastAPI entry point
│   ├── config.py           # Settings via pydantic-settings
│   ├── database.py         # Engine, session, get_db dependency
│   ├── models.py           # User, Event, Seat, Booking
│   ├── seed.py             # Demo event + 100 seats
│   ├── alembic/            # Database migrations
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   │   ├── App.jsx
│   │   └── api.js          # Single place for backend calls
│   ├── vite.config.js
│   ├── package.json
│   └── Dockerfile
├── docker-compose.yml      # Postgres + backend + frontend
└── README.md
```

## 🔌 API

| Method | Endpoint | Description |
|---|---|---|
| `GET` | `/api/events` | List all events |
| `GET` | `/api/events/{id}` | Event detail with seat counts by status |
| `GET` | `/api/events/{id}/seats` | All seats for an event (powers the grid) |
| `GET` | `/api/seats/{id}` | Single seat |
| `POST` | `/api/seats/{id}/lock` | Hold a seat for 5 minutes — `409` if held by someone else |
| `DELETE` | `/api/seats/{id}/lock` | Release your hold (safe: only your own lock) |
| `GET` | `/api/seats/{id}/lock` | Who holds the seat and for how long |
| `POST` | `/api/bookings` | Book a seat — returns `409` if already taken |
| `GET` | `/api/bookings?user_id=` | A user's bookings |
| `DELETE` | `/api/bookings/{id}` | Cancel a booking, releasing the seat |
| `GET` | `/api/health` | Service, database and Redis status |
| `WS` | `/ws/events/{id}` | Live seat updates — pushed on every lock, release, booking or cancellation |

Interactive docs at [`/docs`](http://localhost:8000/docs).

WebSocket messages are fanned out through a Redis pub/sub channel per event, so a change processed by one worker reaches clients connected to any other worker:

```json
{ "type": "seat_update", "action": "locked", "seat": { "id": 42, "status": "locked", "locked_by": 3, ... } }
```

## 🗄️ Data Model

| Table | Purpose |
|---|---|
| `users` | Accounts (JWT auth coming in a later phase) |
| `events` | Event name, venue, start time, total seats |
| `seats` | Position, price, `status`, **`version`** (optimistic lock), lock holder + expiry |
| `bookings` | Links user ↔ seat, with a **partial unique index** enforcing one confirmed booking per seat |

## 📊 Load Test Results

Measured with [Locust](loadtest/locustfile.py) against the Docker Compose stack (single uvicorn worker, dev mode — all services on one machine).

**Flash sale — 500 concurrent users contending for a single seat:**

| Metric | Value |
|---|---|
| Total requests | 4,446 |
| Throughput | 150 req/s |
| HTTP failures | 0 |
| **Confirmed bookings in DB** | **1** |
| Integrity violations | 0 |

Every losing request received a clean `409 Conflict`. Verified with [`verify_integrity.py`](backend/verify_integrity.py), which asserts no seat has more than one confirmed booking and that seat status and bookings stay consistent.

**Realistic browsing — 50 concurrent users:**

| Endpoint | p50 | p75 | p95 | p99 |
|---|---|---|---|---|
| `GET /events/{id}/seats` | 13 ms | 19 ms | 93 ms | 180 ms |
| `GET /events/{id}` | 10 ms | 13 ms | 74 ms | 160 ms |
| `POST /seats/{id}/lock` | 19 ms | 23 ms | 46 ms | 83 ms |
| `POST /bookings` | 26 ms | 33 ms | 42 ms | 47 ms |

```bash
docker compose exec backend python seed.py           # 500 test users
docker compose exec backend python reset_state.py    # clean slate

docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s \
    --host http://backend:8000

docker compose exec backend python verify_integrity.py
docker compose exec backend pytest tests/ -v         # 6 concurrency tests
```

> The load test earned its keep: it surfaced a race where `lock_seat` could overwrite a seat that had just been booked, because its `UPDATE` had no status guard. A 20-request test never hit that window. Fixed with the same guarded-update pattern used elsewhere.

## 🗺️ Roadmap

- [x] Dockerized FastAPI + React skeleton
- [x] CORS-enabled API with health check
- [x] Frontend ↔ backend integration (live status, Tailwind v4)
- [x] PostgreSQL + SQLAlchemy models (User, Event, Seat, Booking)
- [x] Alembic migrations + seed data
- [x] Pydantic schemas + CRUD APIs + interactive seat grid
- [x] Optimistic locking — verified with 20 concurrent requests on one seat
- [x] Redis distributed seat locking (`SET NX EX` + Lua-based safe release)
- [x] WebSocket real-time seat broadcasting via Redis pub/sub (multi-worker safe)
- [x] Locust load tests + integrity verification + concurrency test suite
- [ ] JWT authentication
- [ ] Rate limiting
- [ ] Multi-worker deployment (`--workers`) and CI pipeline

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
