# SeatPulse — Documentation

Is project ka poora build log — har feature kaise bana aur **kyu aise** bana.

> Ye folder ab repo ka hissa hai (pehle gitignored tha). Isme koi asli credential nahi hai — sirf demo values (`demo1234`, `seatpulse_dev_password`) jo `.env.example` me waise bhi hain.

---

## Kahan se shuru karein

| Mujhe ye chahiye | Yahan jao |
|---|---|
| Project me ab tak kya bana, aage kya | [roadmap.md](roadmap.md) |
| Kuch bhi test/demo karna hai | [reference/testing.md](reference/testing.md) |
| Interview ki tayyari | [interview-prep.md](interview-prep.md) |
| Naye project me ye hi tareeka dohrana hai | [agent-working-prompt.md](agent-working-prompt.md) |
| Zero se project setup karna hai | [setup/](setup/) |
| Koi feature kaise bana, kyu aise bana | [phases/](phases/) |
| Docker / Postgres command bhool gaya | [reference/](reference/) |

---

## 📁 Structure

```
documents/
├── README.md              ← ye file
├── roadmap.md             ← progress tracker + frontend index
├── interview-prep.md      ← 50+ Q&A, asli numbers ke saath
├── agent-working-prompt.md ← naye project me kisi bhi AI agent ko dene wala prompt
│
├── setup/                 ← zero se project khada karna
│   ├── 01-docker-setup.md
│   └── 02-git-and-github.md
│
├── phases/                ← har feature: kaise bana + KYU aise bana
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

Naya machine pe project khada karna ho:

| # | File | Kya |
|---|---|---|
| 01 | [setup/01-docker-setup.md](setup/01-docker-setup.md) | Docker se FastAPI + React skeleton. Har Dockerfile line explain ki hui hai |
| 02 | [setup/02-git-and-github.md](setup/02-git-and-github.md) | `.gitignore`, `.dockerignore`, git init, GitHub push |

---

## 🧱 Phases

Har phase ka format ek jaisa hai: **problem → approach → steps → ✅ proof → common problems → files**.

| # | Phase | Isme sabse important kya hai |
|---|---|---|
| 01 | [Frontend ↔ Backend](phases/01-frontend-backend-connect.md) | Tailwind v4, `usePolling` (Docker+Windows me hot reload) |
| 02 | [Postgres + Models](phases/02-postgres-models.md) | ⭐ `version` column aur partial unique index — poore project ki neev |
| 03 | [API + Seat Grid](phases/03-api-and-seat-grid.md) | Pydantic schemas, optimistic locking pehli baar |
| 04 | [Redis Locking](phases/04-redis-locking.md) | ⭐ `SET NX EX`, Lua release script, TTL |
| 05 | [WebSockets](phases/05-websockets.md) | ⭐ Redis pub/sub — multi-worker pe kyu zaroori hai |
| 06 | [Load Testing](phases/06-load-testing.md) | ⭐ Locust + integrity checks. Yahan pehla asli bug mila |
| 07 | [Auth + Google OAuth](phases/07-auth-google-oauth.md) | ⭐ Token strategy, aur do aur bug (pool exhaustion) |
| 08 | [Dashboard UI](phases/08-dashboard-ui.md) | Routing, shared context, ek hi WebSocket |
| 09 | [Rate Limit + Idempotency](phases/09-rate-limit-idempotency.md) | Token bucket Lua me, per-user (per-IP nahi) |
| 10 | [RBAC + Organizer](phases/10-rbac-organizer.md) | ⭐ Role ≠ ownership. 403 vs 404 ka farak |
| 11 | [Payments](phases/11-payments.md) | ⭐ Webhook source of truth, redirect nahi. Idempotent fulfilment |
| 12 | [Background Tickets](phases/12-background-tickets.md) | ARQ worker, QR + PDF, outbox email. Kya background me jaana chahiye |
| 13 | [Gate Check-in](phases/13-gate-checkin.md) | ⭐ Wahi exactly-once problem, alag kapdon me |
| 14 | [Dynamic Pricing](phases/14-dynamic-pricing.md) | ⭐ Price lock — quote ek waada hai. Base price kabhi mat badlo |
| 15 | [Locking Benchmark](phases/15-locking-benchmark.md) | ⭐ Optimistic vs `FOR UPDATE` maapa — aur andaza galat nikla |
| 16 | [Multi-Worker + CI](phases/16-multiworker-ci.md) | ⭐ Phase 5 ka daawa aakhirkar verify hua. Clean state ne 3 chhupe bug nikale |
| 17 | [Group Booking](phases/17-group-booking.md) | ⭐ "Sab ya koi nahi" — N payments par atomicity. Jahan optimistic locking kaam nahi aayi |
| 18 | [Seat Layout](phases/18-seat-layout.md) | Sections + aisles. Purane events na tootein — nullable columns ka poora point |
| 19 | [NL Seat Search](phases/19-nl-seat-search.md) | ⭐ LLM ko kitna kaam dena chahiye. Aur ek API key log me leak ho gayi thi |
| 20 | [AI Event Copy](phases/20-ai-event-copy.md) | ⭐ "Facts mat gadho" — aur poster kyu nahi bana |

> **Interview ke liye sabse zaroori:** 04, 06, 07, 11, 13. Wahan teeno defence layers aur load test se mile teen bug hain.

---

## 📖 Reference

| File | Kab kaam aayegi |
|---|---|
| [reference/testing.md](reference/testing.md) | Sab kuch verify karna ho — commands ek jagah |
| [reference/docker-commands.md](reference/docker-commands.md) | Container commands + "site khul hi nahi rahi" ka debug |
| [reference/postgres-commands.md](reference/postgres-commands.md) | psql, users banana, queries, backup |

---

## Rozmarra ke commands

```bash
# Sab theek hai?
curl http://localhost:8000/api/health

# Poora test suite
docker compose exec backend pytest tests/ -v

# Fresh state
docker compose exec backend python reset_state.py

# Data sahi hai? (load test ke baad hamesha)
docker compose exec backend python verify_integrity.py
```

Baaki sab [reference/testing.md](reference/testing.md) me.
