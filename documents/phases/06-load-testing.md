# Phase 6 — Load Testing + Proof

[Phase 5 — WebSockets](05-websockets.md) is the final phase.

> **Without this, Phases 2-4 are just claims.** This phase converts them into **numbers** — and those are the numbers that go on your resume.

---

## Two distinct things to measure

| What | Using | Question |
|---|---|---|
| **Correctness** | `verify_integrity.py` | Did overselling occur? |
| **Performance** | Locust | How fast? |

**Correctness is more important.** Locust might show "0 failures" while the database contains two bookings — because both requests received a `201`. Therefore, **always** check the DB after a load test.

---

## Step 1 — Locust setup

```
loadtest/
├── Dockerfile
├── requirements.txt      (locust)
└── locustfile.py
```

**In Compose with profile:**
```yaml
  locust:
    build: ./loadtest
    profiles: ["loadtest"]      # Will NOT start on normal `up`
    volumes:
      - ./loadtest:/loadtest
    ports:
      - "8089:8089"
    environment:
      LOCUST_HOST: http://backend:8000
    depends_on:
      - backend
```

> `profiles: ["loadtest"]` — This container will not start during a standard `docker compose up`. It only runs when `--profile loadtest` is provided.

### ⚠️ Dockerfile requires ENTRYPOINT, not just CMD

```dockerfile
ENTRYPOINT ["locust"]
CMD ["-f", "locustfile.py"]
```

**Why:** Arguments passed to `docker compose run --rm locust -f locustfile.py --headless ...` **completely replace the CMD**. If only CMD were used, Docker would treat `-f` as the executable:

```
exec: "-f": executable file not found in $PATH
```

The ENTRYPOINT remains fixed, and arguments are appended to it.

---

## Step 2 — `locustfile.py`

Full code: [../loadtest/locustfile.py](../../loadtest/locustfile.py)

### Two scenarios

| Class | Action | What it proves |
|---|---|---|
| `FlashSaleUser` | All target the **same seat** | No overselling |
| `BrowsingUser` | View grid + random booking | Realistic response times |

### ⚠️ Each user must have a unique `user_id` — this is critical

```python
_user_ids = itertools.cycle(range(1, USER_POOL_SIZE + 1))

def on_start(self):
    self.user_id = next(_user_ids)
```

If two Locust users send the same `user_id`, the second one will receive a **`200` with `already_owned`** — because it already holds the lock. The success count will inflate, rendering the test invalid.

Therefore, `seed.py` now creates **500 users**:
```python
SEED_USERS = int(os.getenv("SEED_USERS", "500"))
```

### Treat 409 as success

```python
elif res.status_code == 409:
    res.success()      # This is NOT a failure — it is the correct response
```

In a flash sale, 499 people receiving a 409 is **expected**. Marking it as a failure would make Locust show a "99% failure rate," making the app look broken when it is actually working perfectly.

True failures are only `500` or timeouts.

---

## Step 3 — `verify_integrity.py`

Full code: [../backend/verify_integrity.py](../../backend/verify_integrity.py)

**Four checks:**

| # | Check | What it catches |
|---|---|---|
| 1 | Any seat with 2+ confirmed bookings? | **Overselling** |
| 2 | `booked` seats == confirmed bookings | State mismatch |
| 3 | Any `booked` seat without a booking? | Orphan seat |
| 4 | Any confirmed booking without a `booked` seat? | Orphan booking |

Check #1 is the most important:
```sql
SELECT seat_id, count(*) FROM bookings
WHERE status = 'confirmed'
GROUP BY seat_id HAVING count(*) > 1
```
If even one row is returned = overselling occurred.

---

## Step 4 — Run the test

```bash
# 1. Seed users (once)
docker compose exec backend python seed.py

# 2. Clean state
docker compose exec backend python reset_state.py

# 3. Flash sale — 500 users, one seat
docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s \
    --host http://backend:8000

# 4. ⭐ Verify
docker compose exec backend python verify_integrity.py
```

**Flags:**

| Flag | Meaning |
|---|---|
| `-u 500` | 500 concurrent users |
| `-r 100` | Ramp up at 100 users/second |
| `-t 30s` | Run for 30 seconds |
| `--headless` | No Web UI, run in terminal |

**To use Web UI** (with graphs, good for screenshots):
```bash
docker compose --profile loadtest up locust
```
Then visit http://localhost:8089

---

## ⭐ Load test caught a real bug

This is the biggest lesson of this phase — **load testing is not just for "getting numbers."**

First run:

```
✅ No seat sold twice
❌ Seat status and bookings match  — 0 booked seats, 1 confirmed bookings
❌ No booking without a booked seat — 1 mismatched bookings
   Seats:    locked=1, available=99
   Bookings: confirmed=1
```

The seat was `locked`, but it also had a **confirmed booking**. No overselling, but the state was inconsistent.

### The race condition

```
User B: read seat (status = locked by A)  -> check passed
User A: book seat -> status = booked, Redis lock released
User B: acquired Redis lock (now free)
User B: wrote to DB -> status = locked     ← 'booked' was overwritten
```

The `lock_seat` DB update lacked a guard:
```python
update(Seat).where(Seat.id == seat_id).values(status=SEAT_LOCKED, ...)
```
It was blindly writing `locked` without checking the seat's current state.

### Fix — the optimistic locking pattern

```python
result = db.execute(
    update(Seat)
    .where(
        Seat.id == seat_id,
        Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)),   # <- guard
    )
    .values(status=SEAT_LOCKED, ...)
)

if result.rowcount == 0:
    db.rollback()
    release_seat_lock(seat_id, payload.user_id)    # release your Redis lock
    raise HTTPException(409, "Seat was just booked")
```

> **Takeaway:** This bug was **not caught** in the 20-request test (Phase 3). The timing window only opened at 500 users. This is why load testing is essential — not just for metrics, but for **bugs**.
>
> This is worth mentioning in an interview: "The load test caught a race condition invisible in smaller tests, which I fixed using a guarded-update pattern."

---

## ✅ Results (actual, on this machine)

### Test A — Flash sale: 500 users, one seat

```
Total requests   : 4446
Failures         : 0
Requests/sec     : 150.0
Median (p50)     : 2400 ms
p95              : 5700 ms
p99              : 11000 ms
```

```
==============================================================
INTEGRITY CHECK
==============================================================
  ✅ No seat sold twice
  ✅ Seat status and bookings match  — 1 booked seats, 1 confirmed bookings
  ✅ No orphan seats                 — 0 orphan seats
  ✅ No mismatched bookings          — 0 mismatched bookings
--------------------------------------------------------------
  Seats:    available=99, booked=1
  Bookings: confirmed=1
==============================================================
  ✅ ALL PASS — no overselling
==============================================================
```

**4446 requests, 500 concurrent users, one seat → exactly 1 booking.** This is the real proof.

### Test B — Realistic browsing: 50 users

```
Endpoint                    p50     p75     p95     p99
GET  /events/{id}/seats     13ms    19ms    93ms    180ms
GET  /events/{id}           10ms    13ms    74ms    160ms
POST /seats/{id}/lock       19ms    23ms    46ms     83ms
POST /bookings              26ms    33ms    42ms     47ms
--------------------------------------------------------
Aggregated                  13ms    21ms    85ms    160ms
```

---

## ⚠️ How to write these numbers on a resume

**Do not write:** "sub-50ms API response times" — without context.

Because at 500 users, the p50 is **2400ms**. If an interviewer asks "at what load?" and you cannot answer, you lose all credibility.

**Write this:**

> Sustained **p50 13ms / p95 85ms** at 50 concurrent users; verified **zero overselling** across 4,400+ requests from 500 concurrent users contending for a single seat.

This is true, specific, and exactly what was measured.

### Why so slow at 500 users?

The honest reason — and it is important to understand:

| Reason | What happens in production |
|---|---|
| **Single uvicorn worker** | `--workers 4` or multiple workers behind gunicorn |
| **`--reload` on** | Dev flag, overhead on every request. Off in production |
| **All on one seat** | Real traffic is spread across 100 seats — this is a deliberate worst-case |
| **All on one laptop** | DB, Redis, backend, and load generator — all on the same machine, same CPU |

These numbers are **worst-case**. And that is what you should say in an interview — you must know why the numbers are the way they are.

---

## Step 5 — Automated concurrency tests

Full code: [../backend/tests/test_concurrency.py](../../backend/tests/test_concurrency.py)

```bash
docker compose exec backend pytest tests/ -v
```

```
tests/test_concurrency.py::test_health                          PASSED
tests/test_concurrency.py::test_only_one_user_gets_the_lock     PASSED
tests/test_concurrency.py::test_no_double_booking               PASSED
tests/test_concurrency.py::test_lock_blocks_other_users_booking PASSED
tests/test_concurrency.py::test_cannot_release_someone_elses_lock PASSED
tests/test_concurrency.py::test_version_increments_on_change    PASSED

============================== 6 passed in 2.92s ===============================
```

**These send real HTTP requests, not mocks.** Race conditions only appear when the full stack (uvicorn + Redis + Postgres) is running together. Mocking would have missed the bug the load test caught.

```python
with ThreadPoolExecutor(max_workers=40) as pool:
    codes = list(pool.map(try_book, range(1, 41)))

assert codes.count(201) == 1
```

Locust is manual; these can run every time — even in CI.

Dev dependencies are in a separate file (`requirements-dev.txt`) so pytest is not included in the production image.

---

## Step 6 — `reset_state.py`

Needed repeatedly during testing:

```bash
docker compose exec backend python reset_state.py
```
```
✅ Removed 3 bookings, set 100 seats to available
✅ Cleared 0 Redis locks
```

> Not `flushall` in Redis, only `scan_iter("seat:*:lock")` — it only deletes its own keys. Future Redis usage (rate limiting, etc.) would be wiped by `flushall`.

---

## Common Problems

| Problem | Fix |
|---|---|
| `exec: "-f": executable file not found` | Need `ENTRYPOINT ["locust"]` in Dockerfile |
| Load test shows `404 User not found` | Users not seeded — `docker compose exec backend python seed.py` |
| All requests get 200 (no contention) | Same `user_id` being reused. Check `USER_POOL_SIZE` |
| `Connection refused` from locust | `--host http://backend:8000` (container network), not `localhost` |
| Response time very poor | Normal — dev mode, single worker, all on one machine |
| `verify_integrity.py` fails | **Good** — found a real bug. Look for the race condition |
| Tests fail "no available seat" | `docker compose exec backend python reset_state.py` |
| Locust web UI not opening | `docker compose --profile loadtest up locust` then http://localhost:8089 |

---

## Files created/modified in this phase

```
loadtest/                       ← new folder
├── Dockerfile                  (ENTRYPOINT required)
├── requirements.txt
└── locustfile.py               ⭐ two scenarios

backend/
├── verify_integrity.py         ← new  ⭐ real proof
├── reset_state.py              ← new
├── requirements-dev.txt        ← new
├── Dockerfile                  ← update (dev deps)
├── seed.py                     ← update (500 users)
├── tests/
│   └── test_concurrency.py     ← new  ⭐ 6 tests
└── routers/
    └── seats.py                ← update  ⭐ RACE FIX (guarded update)

docker-compose.yml              ← update (locust service, with profile)
```

---

## Commit

```bash
git add .
git commit -m "Phase 6: Locust load tests, integrity verification, concurrency test suite

- Fix race where lock_seat could overwrite a booked seat's status
- 500 concurrent users on one seat: exactly 1 booking, 0 integrity violations"
git push
```

---

## Related

- [Phase 4 — Redis Locking](04-redis-locking.md) — locking design
- [postgres-commands.md](../reference/postgres-commands.md) — DB queries
- [roadmap.md](../roadmap.md) — full plan

---

**Roadmap complete.** Optional next steps: JWT auth, rate limiting, multi-worker deploy (`--workers 4`), CI pipeline.
