# Phase 16 — Multi-Worker Deploy + CI

> In Phase 5, I wrote: "Redis pub/sub is used because in-memory dicts are not shared across multi-workers."
>
> That claim had **never been tested** — development always ran on a single worker. This phase is about running that configuration in reality.

---

## Two Objectives

1. **Production config** — 4 uvicorn workers, built frontend, non-root containers. Proving that everything functions under this configuration.
2. **CI** — Running the full stack and 66 tests on every push.

Together, these revealed **three real bugs** that had been hidden in development for months. This is the most valuable part of this phase.

---

## Multi-stage Dockerfiles

Two targets in a single Dockerfile — `dev` and `prod`:

```
base ──┬── dev   (+ requirements-dev, --reload, root)
       └── prod  (non-root user, no dev tools, --workers N)
```

**Why not two separate files:** Base layers (Python version, requirements) would gradually diverge, leading to "it worked on my laptop" issues. Using one file ensures the base is shared.

| Image | Dev | Prod |
|---|---|---|
| backend | 372 MB | 354 MB |
| frontend | 407 MB | **74 MB** |

The frontend difference is significant because the production image contains no `node_modules` or source code — only built assets and Nginx.

📁 [`backend/Dockerfile`](../../backend/Dockerfile) · [`frontend/Dockerfile`](../../frontend/Dockerfile)

### Two intentional features in the Prod image

```dockerfile
RUN useradd --create-home --uid 1000 appuser
USER appuser
```

Even if a container escape occurs, the attacker should not gain root access. One line, major benefit.

Furthermore, development tools are not installed — pytest and locust are absent from the production image. Smaller size, smaller attack surface.

> This is not just a comment; CI **checks** both (see below). This hardening is real — I attempted to write a file in the production container and received a `PermissionError`. That was the intended result.

---

## nginx + SPA fallback

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

Without this line, refreshing at `/events/3` results in a **404**. Routing is handled by React Router, not Nginx — Nginx looks for a file named `events/3`, which does not exist.

There is also a trap in caching:

| Path | Cache | Why |
|---|---|---|
| `/assets/*` | 1 year, immutable | Filename contains a content hash (`index-DTJXVmPu.js`). If content changes, the name changes. |
| `/index.html` | **never** | This file points to new asset names. If cached, the user will request old assets after a deployment — which no longer exist. Result: **blank page**. |

📁 [`frontend/nginx.conf`](../../frontend/nginx.conf)

---

## ⭐ Connection pool — the first hurdle of multi-worker

This error is invisible on a single worker:

```
Each uvicorn worker is a SEPARATE PROCESS — its own pool, its own memory.

Dev  (1 worker):   1 x (20 + 20) =  40 connections
Prod (4 workers):  4 x (20 + 20) = 160 connections
                                    ^^^ Postgres default max is 100
```

The pool configuration that worked in development triggered `FATAL: sorry, too many clients already` with 4 workers.

Therefore, the pool is now sourced from environment variables, not hardcoded:

```yaml
# docker-compose.prod.yml
DB_POOL_SIZE: 5
DB_MAX_OVERFLOW: 5
MAX_CONCURRENT_REQUESTS: 8    # invariant: < pool + overflow
```

`4 × (5 + 5) = 40` — well within the 100-connection limit.

Admission control is also **per-worker**, so it had to be reduced. The invariant remains the same as in [Phase 7](07-auth-google-oauth.md): `MAX_CONCURRENT_REQUESTS < pool_size + max_overflow`.

Measured, with 4 workers: **5 / 100 connections**.

---

## ⭐⭐ Real proof — does broadcast cross the process boundary?

This is the heart of this phase. The argument from Phase 5 was, until now, just a **hope**.

Test:
1. Connect 12 WebSocket clients — the OS can assign each connection to any worker, distributing them across separate processes.
2. Use the `worker_pid` from `/api/health` to **prove** that multiple processes are actually running (otherwise the test proves nothing).
3. Book ONE seat — that booking occurs in ONE worker.
4. Verify that all 12 clients received the update.

```
Responding worker processes: 3  -> [9, 10, 12]
12 WebSocket clients connected
Seat A-1 booked (HTTP 201) — in one worker

Broadcast received: 12 / 12 clients

✅ PASS — 3 workers, broadcast reached all 12 clients
   Redis pub/sub truly crosses the process boundary.
```

If the broadcast used an in-memory dict, only the clients connected to the worker that processed the booking would receive the message. The other 8-9 clients would remain silent, and their grids would show the seat as **available even though it was sold**.

📁 [`loadtest/verify_multiworker.py`](../../loadtest/verify_multiworker.py)

### A mistake made while writing this proof

First run:

```
Responding worker processes: 1  -> [9]
```

4 workers were running, yet only one responded. Reason: I sent 40 health requests from a single `httpx` client — all received the same **keep-alive TCP connection**, which was tied to one worker.

**Worker distribution happens at the connection level, not the request level.** Creating a new client for every probe revealed 3 distinct PIDs.

---

## CI

📁 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)

Three jobs:

| Job | What |
|---|---|
| `test` | Full stack up → migrate → seed → 66 tests → integrity check |
| `build-prod` | Prod images build + **assertions** (no dev tools, no root) |
| `frontend` | `npm ci` + build |

### Did not use GitHub's `services:` block

It only provides DB/Redis containers, requiring the app to run separately on the runner — meaning CI would test something that isn't actually deployed.

Here, CI runs the **same `docker compose`** used on the laptop. If the compose file breaks, CI will catch it.

### Assertions, not just builds

```yaml
- name: Prod image must not contain dev tools
  run: |
    ... 'if python -c "import pytest" 2>/dev/null; then
           echo "FAIL: prod image contains pytest"; exit 1
         fi'

- name: Prod image must not run as root
  run: |
    USER_ID=$(... --entrypoint id backend -u 2>/dev/null | tail -1)
    if [ "$USER_ID" = "0" ]; then exit 1; fi
```

"Build successful" is not enough. If someone copies `requirements-dev.txt` into the prod stage, the build would still pass — but the image would be bloated and less secure. These two checks prevent that.

### Polling instead of `sleep 30`

```bash
for i in $(seq 1 60); do
  curl -sf http://localhost:8000/api/health && exit 0
  sleep 1
done
docker compose logs backend      # fail with logs
exit 1
```

Fixed sleep is flaky on slow runners and wastes time on fast ones. Debugging a CI failure without logs is impossible.

---

## ⭐⭐⭐ Three bugs caught by a clean, CI-like state

This is the most important section. All three bugs had been in the code for **months**, hidden only because my development database was stale.

### Bug 1 — 35 tests SKIPPED after `docker compose down -v`

```
31 passed, 35 skipped in 6.84s        <- Appears GREEN in CI
```

The `tokens` fixture logs in as `user1@seatpulse.dev`. If login fails, the fixture calls `pytest.skip()`. Skipped tests appear as **passed** in CI.

The cause was in `seed.py`:

```python
existing = 3                                  # demo, organizer, admin
for i in range(existing, existing + to_create)   # user3, user4, ...
```

Numbering was tied to the **count** of users. After named accounts were created, the counter reset to 3, so **`user1` and `user2` were never created.**

This was hidden in the old database because it was seeded when the numbering was different.

Fix: Numbering is now fixed (`user1..userN`) and only missing accounts are created — making the seed idempotent.

> **Lesson:** "66 passed" and "31 passed, 35 skipped" both look green in CI. You must monitor the skip count.

### Bug 2 — seeded event organizer was NULL

After the fix, 62 passed, **4 failed** — all related to gate check-in:

```
KeyError: 'ok'
```

The endpoint was returning something other than `{"ok": ...}`. Calling it directly:

```
HTTP 403
{"detail":"This ticket does not belong to your event"}
```

`seed.py` created the event but did not set the `organizer_id` — it remained NULL. The check-in ownership check looks for `event.organizer_id == user.id`, which never matches NULL.

This wasn't just a test failure — **the demo data was broken**: the seeded event didn't appear in the organizer portal, and its tickets wouldn't scan at the gate.

Hidden in the old database because it was created via the event portal.

Fix: Seed now links the event to the organizer account and backfills for the old database.

### Bug 3 — idempotency test failed on subsequent runs

Running the suite on the prod stack for the first time caused one test to fail:

```
assert 0 == 1    # "one booking expected, found 0"
```

My first suspicion was multi-worker. I was wrong. The test's idempotency key was **fixed**:

```python
"Idempotency-Key": f"test-{seat_id}-once"
```

The key persists in Redis until TTL. The next test run would **replay** the key: it received a 201, but no new booking was created.

The test relied on `reset_state.py`, which can be skipped (and doesn't run in CI).

Fix: Each run has its own `RUN_ID` suffix. The suite now passes twice in a row without any reset — which was previously impossible.

> **Lesson:** "Failing in multi-worker" does not automatically mean "it's a multi-worker bug." In all three cases, the real cause was something else.

---

## Commands

```bash
# ---- Production stack ----
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Multi-worker broadcast proof
docker compose cp loadtest/verify_multiworker.py backend:/tmp/vmw.py
docker compose exec backend python /tmp/vmw.py

# Connections check — 4 workers, 100 limit
docker compose exec db psql -U seatpulse -d seatpulse \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='seatpulse';"

# Back to dev
# ⚠️ --build is required: prod build overwrites the image tag,
#    so without a rebuild, the dev container runs the prod image (which lacks pytest).
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose up -d --build backend worker
```

⚠️ **Prod image does not contain pytest.** To run tests against the prod stack, run a throwaway container from the dev image:

```bash
docker build --target dev -t seatpulse-test:dev ./backend
docker run --rm --network seatpulse-event-engine_default \
  -e TEST_BASE_URL=http://backend:8000 \
  -e DATABASE_URL="postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse" \
  -e REDIS_URL=redis://redis:6379/0 \
  seatpulse-test:dev python -m pytest tests/ -q
```

### Clean run like CI locally

This should be run after every major change — it caught all three bugs above:

```bash
docker compose down -v          # ⚠️ wipes the entire database
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
docker compose exec backend python -m pytest tests/ -q
```

**Check the skip count** — you want `66 passed`, not `31 passed, 35 skipped`.

---

## Files

**New:**
| File | What |
|---|---|
| `.github/workflows/ci.yml` | Three jobs — tests, prod build + assertions, frontend |
| `docker-compose.prod.yml` | 4 workers, small pool, nginx frontend |
| `frontend/nginx.conf` | SPA fallback + cache headers |
| `frontend/package-lock.json` | Missing previously — reproducible builds impossible without `npm ci` |
| `loadtest/verify_multiworker.py` | Cross-worker broadcast proof |

**Modified:**
| File | What |
|---|---|
| `backend/Dockerfile` | `dev` / `prod` targets, non-root prod |
| `frontend/Dockerfile` | `dev` / `build` / `prod` (nginx) |
| `backend/config.py` | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` |
| `backend/database.py` | Pool now from config |
| `backend/main.py` | `worker_pid` in `/api/health` |
| `backend/seed.py` | **Bug 1 + Bug 2 fix** |
| `backend/tests/helpers.py` | **Bug 3 fix** — per-run idempotency keys |
| `docker-compose.yml` | `target: dev` explicit |

---

## Related

- [Phase 5 — WebSockets](05-websockets.md) — the claim verified here
- [Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) — admission control and pool invariant
- [Phase 15 — Locking Benchmark](15-locking-benchmark.md) — previous measurement phase
- [testing.md](../reference/testing.md) — commands
- [docker-commands.md](../reference/docker-commands.md) — compose reference
