# Phase 15 — Pessimistic vs Optimistic Locking Benchmark

> I have used optimistic locking throughout this project. This phase is to **measure** that decision — not to justify it.

---

## The Question

Since Phase 2, I have used optimistic locking with a `version` column. The direct counter-question in interviews is:

> "Why not `SELECT ... FOR UPDATE`? It is simpler."

Until now, my answer was theoretical. In this phase, I implemented both and ran them under the same load to provide an answer backed by **numbers**.

**And if the numbers went against me, I had to document that too.** They did, to some extent — details below.

---

## Two Approaches

```
OPTIMISTIC  — "Try it, if you collide, give up"

    UPDATE seats SET status='booked', version = version + 1
    WHERE id = ? AND version = ? AND status IN ('available','locked')

    rowcount 0 -> someone else won -> IMMEDIATELY 409


PESSIMISTIC — "Lock it first, then proceed at leisure"

    SELECT * FROM seats WHERE id = ? FOR UPDATE   <- BLOCKS here
    (now the row is locked by me, check at leisure)
    UPDATE ...
```

The difference is not in correctness — **both prevent overselling**. The difference is in behavior:

| | What the loser does |
|---|---|
| Optimistic | Takes a 409 immediately and leaves |
| Pessimistic | **Joins a queue**, finds out the seat is gone only when their turn comes, then 409 |

📁 [`backend/locking_strategies.py`](../../backend/locking_strategies.py)

### ⚠️ The benchmark runs real code, not a copy

The easiest mistake is creating a separate endpoint for benchmarking. Then you would be measuring something that is never deployed.

Therefore, both strategies run using the **same `_perform_booking()`** used in production. Only the claim step changes:

```python
if strategy == PESSIMISTIC:
    claim = claim_pessimistic(db, payload.seat_id)
else:
    claim = claim_optimistic(db, payload.seat_id, expected_version)
```

Knobs come from query parameters, **but only if `BENCHMARK_MODE=true`**. Otherwise, they are silently ignored. A query parameter that changes locking semantics is a production footgun — anyone could send `?redis_lock=off` and trigger the most expensive code path.

---

## Round 1 — Locust, 300 users, one seat

```bash
bash loadtest/run_benchmark.sh          # four scenarios
```

| Scenario | Total req | Reached `/bookings` | req/s | p50 |
|---|---|---|---|---|
| optimistic, Redis **on** | 1733 | **1** | 59.0 | 3300 ms |
| pessimistic, Redis **on** | 1871 | **1** | 63.9 | 2900 ms |
| optimistic, Redis **off** | 1783 | 1483 | 63.1 | 3000 ms |
| pessimistic, Redis **off** | 1857 | 1557 | 63.4 | 3200 ms |

Integrity check passed in all four: **exactly 1 confirmed booking**, no overselling.

And the numbers for all four... were nearly identical. Two reasons emerged, both findings in their own right:

### ⭐ Finding 1 — If Redis is on, load doesn't reach the DB strategy

Out of 1433 contended requests, only **1** reached `/api/bookings`. The other 1432 returned 409 at the Redis lock level.

This is direct proof of my claim in Phase 4 — but it has a consequence: **changing the DB strategy in production config cannot make a difference**, because that code never runs.

Therefore, the rest of the benchmark had to be run with Redis off. That is not "cheating" — it is the only way to observe the DB layer in isolation.

### Finding 2 — Admission control becomes a bottleneck at 300 users

Even with Redis off, all four scenarios were stuck at ~63 req/s and p50 ~3s.

Reason: In [Phase 7](07-auth-google-oauth.md), I implemented admission control — a semaphore that only allows 30 requests inside. With 300 users, every request waits in the **queue** for ~3 seconds.

Against that 3-second wait, the database work (~milliseconds) is invisible. Locust was measuring the entire system, not the single line I changed.

---

## Round 2 — Micro-benchmark

Locust wasn't the wrong tool; it was answering the wrong **question**. So I wrote a focused benchmark:

- Redis layer **off** (otherwise nothing reaches the DB)
- Concurrency **25**, below the admission limit (30) — so queue wait times don't pollute the numbers
- Login **once beforehand** — bcrypt (~400ms) masks everything
- Free the seat after every round to recreate **real contention** — measuring a single contention event is just noise
- 40 rounds × 25 = **1000 requests per strategy**

```bash
docker compose exec backend python /loadtest/micro_benchmark.py
```

📁 [`loadtest/micro_benchmark.py`](../../loadtest/micro_benchmark.py)

### Result — 4 runs

| Run | Order | opt p50 | pess p50 | opt p99 | pess p99 | p50 ratio | p99 ratio |
|---|---|---|---|---|---|---|---|
| A | opt first | 319.6 | 322.6 | 858.4 | 645.6 | 1.01× | 0.75× |
| B | opt first | 291.7 | 271.2 | 625.0 | 499.2 | 0.93× | 0.80× |
| C | opt first | 285.2 | 273.2 | 517.0 | 510.9 | 0.96× | 0.99× |
| D | **pess first** | 310.2 | 291.8 | 642.0 | 514.5 | 0.94× | 0.80× |

40/40 wins, 960 conflicts, **0 errors** in every run — meaning the comparison is valid.

### ⭐ Finding 3 — pessimistic was *slightly faster*, not slower

This is the **opposite** of my expectation, and it is important to document.

My first suspicion was ordering bias (the first to run benefits from a cold machine). So in run D, I reversed the order — **same result**. So it is not bias.

The reason is in the code. For the losers arriving AFTER the seat is booked:

```
optimistic  -> UPDATE ... WHERE version=? (0 rows match) -> rollback
                ^^^ write statement, still executes

pessimistic -> SELECT ... FOR UPDATE (lock is free, acquired immediately)
               status check -> 'booked' -> return, no UPDATE executed
```

Meaning, for 24 out of 25 losers on the same seat, the pessimistic path does **less work**. Blocking doesn't happen because the winner has already committed in milliseconds.

**But the difference is 5-7%, and the run-to-run variance is similar.** Therefore, the honest conclusion is: *at this scale, the difference between the two is not measurable.*

---

## ⭐ Finding 4 — the claim step is 1/33 of the request

The real answer to "why was there no difference" is here. After enabling statement logging on Postgres, I counted the SQL statements for one booking request:

```bash
docker compose exec db psql -U seatpulse -d seatpulse \
  -c "ALTER SYSTEM SET log_statement='all';" -c "SELECT pg_reload_conf();"
```

**One booking = 33 SQL statements.** Breakdown:

| Count | What |
|---|---|
| 4 | `SELECT 1` — pool pre-ping health checks |
| 4 / 2 / 2 | BEGIN / COMMIT / ROLLBACK |
| 3 | `SELECT seats` |
| 2 | `SELECT users` (auth) |
| 2 | `SELECT events` |
| 2 | `count(seats)` |
| 2 | `count(bookings)` |
| 2 | `min(seats.price)` |
| … | ticket worker's `UPDATE bookings SET qr_token=…` |
| **1** | **actual claim** — `UPDATE seats SET status='booked'` |

Changing the locking strategy changes **1 out of 33 statements**. The other 32 remain exactly the same. That is why a 5% difference is expected — and even that gets lost in the noise.

The baseline confirms this: at concurrency 1 (no contention), a booking takes ~50-70ms. The claim step is only two or three milliseconds of that.

### One more thing the count caught

`count(seats)`, `count(bookings)`, and `min(price)` — all three are running **twice**. Reason: `pricing_state()` runs once in `price_now()` and again in `broadcast_seat_update()`.

Meaning there are **6 redundant queries** in every booking.

I have **not** fixed this yet, intentionally — fixing it would change all the numbers above and require re-running the benchmark. This is noted as a follow-up in the [roadmap](../roadmap.md). But this is the most practical benefit of this phase: **measuring revealed a real inefficiency** that has nothing to do with locking.

---

## So why keep optimistic?

The numbers did not show a throughput difference. Yet, optimistic remains the default, and the reason is the **failure mode**, not speed:

| | Under high load |
|---|---|
| **Optimistic** | Loser exits immediately. Connection freed immediately. |
| **Pessimistic** | Loser stays in queue **holding the DB connection** |

There are 40 connections in the pool. If 500 people are after one seat and every loser holds their connection, the pool is exhausted not in minutes — but in seconds. The exact same ailment identified as `idle in transaction` in [Phase 7](07-auth-google-oauth.md).

**This did not show up in this benchmark, and I did not claim it would.** It didn't show up because the winning transaction commits in milliseconds — no one waits. The cost of pessimistic grows with the time the lock is held. Today that time is ~2ms.

The danger is that the time **could increase**: a transaction with an external call, a slow query, a large report — and the pessimistic path will turn into immediate pool exhaustion, while the optimistic behavior remains the same.

> In one line: **I didn't choose optimistic because it is faster today (it isn't). I chose it so it won't be bad tomorrow.**

---

## What broke (and what I learned)

### 1. The first micro-benchmark run was completely false

```
strategy       reqs  won   409   err
optimistic     1000   33   463   504     <- 504 errors!
pessimistic    1000   33   392   575
```

The prefix in the code clearing rate limit buckets was wrong — `ratelimit:*`, while the actual prefix is `rl:` ([`rate_limit.py`](../../backend/rate_limit.py)). The buckets were never cleared, and from the fourth round, every request started hitting 429s. Those 429 latencies were polluting the numbers.

**This was caught by the `errors` column** — which I kept only for sanity. If I had only printed p50/p99, the numbers would have *looked perfectly fine* and I would have written the doc with a completely wrong conclusion.

Lesson: Always keep an **invariant** check in benchmarks ("exactly 1 booking should win every round"), don't just print timings.

### 2. The first proof-of-concept had 0 WebSocket messages (Lesson from Phase 14 repeated)

Locust's four runs looked "all equal" and my first reflex was that the strategy switch wasn't working. In reality, the switch was working fine — Redis was blocking 1432/1433 requests.

Without looking at per-endpoint CSVs, this is impossible to know. Aggregate numbers hid the truth.

### 3. "The benchmark did not confirm my claim" — and I wrote exactly that

It would have been easiest to spin the numbers to make optimistic look like the winner. The numbers didn't say that. I wrote exactly what I found: *the difference was not measurable, and what little there was favored pessimistic.*

In an interview, saying "I measured it and my assumption was wrong" builds **more** trust than "I measured it and I was right."

---

## How to run it again

```bash
# 1. Turn on benchmark mode
echo "BENCHMARK_MODE=true" >> backend/.env
docker compose up -d backend

# 2. Locust — full system, four scenarios
bash loadtest/run_benchmark.sh

# 3. Micro-benchmark — DB claim step only
docker compose exec backend python /loadtest/micro_benchmark.py

# Order bias check
docker compose exec -e BENCH_ORDER=pessimistic,optimistic backend \
    python /loadtest/micro_benchmark.py

# 4. Turn it off — these knobs should not exist in production
sed -i '/BENCHMARK_MODE=true/d' backend/.env
docker compose up -d backend
```

Tests pass in both modes (**66/66**) — this is intentional, so that if benchmark mode is accidentally left on, the suite remains meaningful.

---

## Files

**New:**
| File | What |
|---|---|
| `backend/locking_strategies.py` | Both claim strategies, in one place |
| `loadtest/run_benchmark.sh` | Four Locust scenarios + integrity check |
| `loadtest/micro_benchmark.py` | Focused DB-only measurement |

**Modified:**
| File | What |
|---|---|
| `backend/config.py` | `BENCHMARK_MODE` (default false) |
| `backend/routers/bookings.py` | Extracted claim step strategies; benchmark knobs |
| `loadtest/locustfile.py` | `BOOKING_STRATEGY` / `USE_REDIS_LOCK` env |
| `docker-compose.yml`, `backend/.env.example` | `BENCHMARK_MODE` |
| `backend/tests/test_concurrency.py` | 3 new tests |

---

## Related

- [Phase 2 — Postgres + Models](02-postgres-models.md) — where the `version` column came from
- [Phase 4 — Redis Locking](04-redis-locking.md) — the layer that stops 1432/1433 requests
- [Phase 6 — Load Testing](06-load-testing.md) — Locust setup
- [Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) — admission control and pool exhaustion
- [Interview Prep](../interview-prep.md) — the `FOR UPDATE` question
- [testing.md](../reference/testing.md) — commands to run
