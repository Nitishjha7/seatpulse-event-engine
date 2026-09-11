# SeatPulse — Documentation

This is the complete build log for the project — detailing how every feature was built and **why it was built that way**.

> This folder is now part of the repository (previously gitignored). It contains no real credentials — only demo values (`demo1234`, `seatpulse_dev_password`) which are already present in `.env.example`.

---

## Getting Started

| I need... | Go to |
|---|---|
| Project progress and future roadmap | [roadmap.md](roadmap.md) |
| To run tests or demos | [reference/testing.md](reference/testing.md) |
| Interview preparation | [interview-prep.md](interview-prep.md) |
| To reuse this workflow for a new project | [agent-working-prompt.md](agent-working-prompt.md) |
| To set up the project from scratch | [setup/](setup/) |
| To understand how a feature was built | [phases/](phases/) |
| Docker / Postgres commands | [reference/](reference/) |

---

## 📁 Structure

```
documents/
├── README.md              ← this file
├── roadmap.md             ← progress tracker + frontend index
├── interview-prep.md      ← 50+ Q&A, with real metrics
├── agent-working-prompt.md ← prompt for AI agents in new projects
│
├── setup/                 ← project setup from scratch
│   ├── 01-docker-setup.md
│   └── 02-git-and-github.md
│
├── phases/                ← every feature: how it was built + WHY
│   ├── 01-frontend-backend-connect.md
│   ├── 02-postgres-models.md
│   ├── 03-api-and-seat-grid.md
│   ├── 04-redis-locking.md
│   ├── 05-websockets.md
│   ├── 06-load-testing.md
│   ├── 07-auth-google-oauth.md
│   ├── 08-dashboard-ui.md
│   ├── 09-rate-limit-idempotency.md
│   ├── 10-rbac-organizer.md
│   ├── 11-payments.md
│   ├── 12-background-tickets.md
│   ├── 13-gate-checkin.md
│   ├── 14-dynamic-pricing.md
│   ├── 15-locking-benchmark.md
│   ├── 16-multiworker-ci.md
│   ├── 17-group-booking.md
│   ├── 18-seat-layout.md
│   ├── 19-nl-seat-search.md
│   └── 20-ai-event-copy.md
│
└── reference/             ← command cheatsheets
    ├── docker-commands.md
    ├── postgres-commands.md
    └── testing.md
```

---

## 🚀 Setup

To set up the project on a new machine:

| # | File | Description |
|---|---|---|
| 01 | [setup/01-docker-setup.md](setup/01-docker-setup.md) | FastAPI + React skeleton via Docker. Includes line-by-line Dockerfile explanation |
| 02 | [setup/02-git-and-github.md](setup/02-git-and-github.md) | `.gitignore`, `.dockerignore`, git init, and GitHub push |

---

## 🧱 Phases

Every phase follows a consistent format: **problem → approach → steps → ✅ proof → common problems → files**.

| # | Phase | Key Takeaway |
|---|---|---|
| 01 | [Frontend ↔ Backend](phases/01-frontend-backend-connect.md) | Tailwind v4, `usePolling` (for hot reload in Docker+Windows) |
| 02 | [Postgres + Models](phases/02-postgres-models.md) | ⭐ `version` column and partial unique index — the foundation of the project |
| 03 | [API + Seat Grid](phases/03-api-and-seat-grid.md) | Pydantic schemas, introduction to optimistic locking |
| 04 | [Redis Locking](phases/04-redis-locking.md) | ⭐ `SET NX EX`, Lua release script, TTL |
| 05 | [WebSockets](phases/05-websockets.md) | ⭐ Redis pub/sub — necessity for multi-worker environments |
| 06 | [Load Testing](phases/06-load-testing.md) | ⭐ Locust + integrity checks. Discovered the first real bug |
| 07 | [Auth + Google OAuth](phases/07-auth-google-oauth.md) | ⭐ Token strategy, plus two additional bugs (pool exhaustion) |
| 08 | [Dashboard UI](phases/08-dashboard-ui.md) | Routing, shared context, single WebSocket connection |
| 09 | [Rate Limit + Idempotency](phases/09-rate-limit-idempotency.md) | Token bucket in Lua, per-user (not per-IP) |
| 10 | [RBAC + Organizer](phases/10-rbac-organizer.md) | ⭐ Role ≠ ownership. Distinction between 403 and 404 |
| 11 | [Payments](phases/11-payments.md) | ⭐ Webhook as source of truth, not redirect. Idempotent fulfillment |
| 12 | [Background Tickets](phases/12-background-tickets.md) | ARQ worker, QR + PDF, outbox email. Determining background tasks |
| 13 | [Gate Check-in](phases/13-gate-checkin.md) | ⭐ The exactly-once problem, in a different context |
| 14 | [Dynamic Pricing](phases/14-dynamic-pricing.md) | ⭐ Price lock — a quote is a promise. Never modify base price |
| 15 | [Locking Benchmark](phases/15-locking-benchmark.md) | ⭐ Measured optimistic vs `FOR UPDATE` — results were counter-intuitive |
| 16 | [Multi-Worker + CI](phases/16-multiworker-ci.md) | ⭐ Verified Phase 5 claims. Clean state revealed 3 hidden bugs |
| 17 | [Group Booking](phases/17-group-booking.md) | ⭐ "All or nothing" — atomicity across N payments. Where optimistic locking failed |
| 18 | [Seat Layout](phases/18-seat-layout.md) | Sections + aisles. Ensuring legacy events don't break — the purpose of nullable columns |
| 19 | [NL Seat Search](phases/19-nl-seat-search.md) | ⭐ Balancing LLM workload. Also, an API key was leaked in logs |
| 20 | [AI Event Copy](phases/20-ai-event-copy.md) | ⭐ "Don't hallucinate facts" — and why the poster wasn't generated |

> **Crucial for interviews:** 04, 06, 07, 11, 13. These cover the three defense layers and the three bugs found via load testing.

---

## 📖 Reference

| File | Purpose |
|---|---|
| [reference/testing.md](reference/testing.md) | Centralized verification commands |
| [reference/docker-commands.md](reference/docker-commands.md) | Container commands + debugging "site not loading" issues |
| [reference/postgres-commands.md](reference/postgres-commands.md) | psql, user management, queries, and backups |

---

## Daily Commands

```bash
# Health check
curl http://localhost:8000/api/health

# Run full test suite
docker compose exec backend pytest tests/ -v

# Reset to fresh state
docker compose exec backend python reset_state.py

# Verify data integrity (always after load testing)
docker compose exec backend python verify_integrity.py
```

Everything else is in [reference/testing.md](reference/testing.md).
