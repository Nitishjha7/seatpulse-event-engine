# 🎟️ SeatPulse — High-Concurrency Event Booking Engine

SeatPulse is a full-stack event ticketing platform designed to handle high-concurrency flash sales. It prevents overselling using Redis key-locking and streams real-time seat state changes via WebSockets.

## 🚀 Tech Stack

- **Backend:** FastAPI (ASGI), Python 3.11, Pydantic v2
- **Database & Cache:** PostgreSQL, Redis (Distributed Locking)
- **Frontend:** React (Vite), Tailwind CSS, WebSockets
- **DevOps:** Docker, Docker Compose

## 🧩 The Problem

When 5,000 people click "Book Seat A-12" at the same instant, a naive `SELECT` → `check` → `UPDATE` flow sells the same seat multiple times. SeatPulse solves this with:

- **Redis distributed locks** — a seat is held atomically the moment a user selects it, with a TTL so abandoned carts release automatically.
- **WebSocket broadcasts** — every other connected client sees the seat turn grey in real time, before they waste a click on it.
- **Database-level constraints** — the final safety net, so correctness never depends on application logic alone.

## ⚡ Quick Start

Only **Docker Desktop** is required — no local Node or Python installation needed.

```bash
git clone https://github.com/Nitishjha7/seatpulse-event-engine.git
cd seatpulse-event-engine
docker compose up --build
```

| Service | URL |
|---|---|
| Frontend (React) | http://localhost:5173 |
| Backend (FastAPI) | http://localhost:8000 |
| API Docs (Swagger) | http://localhost:8000/docs |
| Health Check | http://localhost:8000/api/health |

## 📁 Project Structure

```
seatpulse-event-engine/
├── backend/
│   ├── main.py             # FastAPI entry point
│   ├── requirements.txt
│   ├── Dockerfile
│   └── .dockerignore
├── frontend/
│   ├── src/
│   ├── package.json
│   ├── Dockerfile
│   └── .dockerignore
├── docker-compose.yml      # Orchestrates all services
└── README.md
```

## 🗺️ Roadmap

- [x] Dockerized FastAPI + React skeleton
- [x] CORS-enabled API with health check
- [ ] Frontend ↔ backend integration
- [ ] PostgreSQL + SQLAlchemy models (Event, Seat, Booking, User)
- [ ] Redis distributed seat locking
- [ ] WebSocket real-time seat state broadcasting
- [ ] JWT authentication
- [ ] Load testing (Locust) to prove zero overselling

## 🛠️ Common Commands

```bash
docker compose up -d              # start in background
docker compose down               # stop everything
docker compose logs -f backend    # live backend logs
docker compose exec backend bash  # shell into backend
```
