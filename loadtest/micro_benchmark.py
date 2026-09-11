"""
Optimistic vs pessimistic — isolate and measure only the DB claim step.

---- Why this differs from Locust ----

Locust measured the entire system, where two factors obscure the DB strategy:

  1. Redis lock — prevents 1432 out of 1433 requests from reaching the DB.
  2. Admission control (30 slots) — with 300 users, each request queues for ~3s. The 5ms DB time is invisible against that 3s delay.

Therefore, here:
  - Redis layer OFF (`redis_lock=off`)
  - Concurrency is BELOW the admission limit (default 25 vs 30 slots) to prevent queue wait times from skewing results.
  - Login once beforehand, otherwise bcrypt (~400ms) dominates the measurements.
  - Reset the seat each round to recreate REAL contention — measuring a single contention event is just noise.

Run:
    docker compose exec backend python /loadtest/micro_benchmark.py

⚠️ Backend must be running with BENCHMARK_MODE=true.
"""

import asyncio
import os
import statistics
import time

import httpx

BASE = os.getenv("BENCH_HOST", "http://localhost:8000")
CONCURRENCY = int(os.getenv("BENCH_CONCURRENCY", "25"))
ROUNDS = int(os.getenv("BENCH_ROUNDS", "40"))
# Execution order.
#
# This knob is necessary: the first strategy runs on a 'colder' machine (pool, page cache, query plans). If results are consistent regardless of order, the difference is genuine.
ORDER = os.getenv("BENCH_ORDER", "optimistic,pessimistic").split(",")
PASSWORD = os.getenv("SEED_PASSWORD", "demo1234")


async def login_all(client, n):
    """Get all user tokens beforehand — keep bcrypt out of the measurement."""
    async def one(i):
        r = await client.post(
            "/api/auth/login",
            json={"email": f"user{i}@seatpulse.dev", "password": PASSWORD},
        )
        r.raise_for_status()
        return r.json()["access_token"]

    return await asyncio.gather(*(one(i) for i in range(1, n + 1)))


def free_seat(seat_id):
    """
    Reset seat availability — via SQL, not API.

    Using the API would introduce cancel endpoint latency into the round, causing subsequent rounds to be affected by previous ones.
    """
    from sqlalchemy import delete, update

    from database import SessionLocal
    from models import SEAT_AVAILABLE, Booking, Seat

    db = SessionLocal()
    try:
        db.execute(delete(Booking).where(Booking.seat_id == seat_id))
        db.execute(
            update(Seat)
            .where(Seat.id == seat_id)
            .values(
                status=SEAT_AVAILABLE,
                locked_by=None,
                locked_until=None,
                held_price=None,
                version=Seat.version + 1,
            )
        )
        db.commit()
    finally:
        db.close()


async def one_round(client, tokens, seat_id, strategy):
    """Fire CONCURRENCY requests simultaneously at the same seat."""
    url = f"/api/bookings?strategy={strategy}&redis_lock=off"

    async def attempt(token):
        t0 = time.perf_counter()
        try:
            r = await client.post(
                url,
                json={"seat_id": seat_id},
                headers={"Authorization": f"Bearer {token}"},
            )
            code = r.status_code
        except Exception:
            code = 0
        return (time.perf_counter() - t0) * 1000, code

    # asyncio.gather = all requests fire concurrently, not sequentially
    return await asyncio.gather(*(attempt(t) for t in tokens[:CONCURRENCY]))


async def measure(client, tokens, seat_id, strategy):
    latencies, wins, conflicts, errors = [], 0, 0, 0

    for _ in range(ROUNDS):
        free_seat(seat_id)
        # Clear rate limiter buckets, otherwise 429 errors will occur from the 4th round onwards
        _clear_buckets()

        for ms, code in await one_round(client, tokens, seat_id, strategy):
            latencies.append(ms)
            if code == 201:
                wins += 1
            elif code == 409:
                conflicts += 1
            else:
                errors += 1

    latencies.sort()

    def pct(p):
        return latencies[min(len(latencies) - 1, int(len(latencies) * p))]

    return {
        "strategy": strategy,
        "requests": len(latencies),
        "wins": wins,
        "conflicts": conflicts,
        "errors": errors,
        "p50": pct(0.50),
        "p95": pct(0.95),
        "p99": pct(0.99),
        "max": latencies[-1],
        "mean": statistics.mean(latencies),
    }


def _clear_buckets():
    """
    Clear rate limit buckets.

    ⚠️ The prefix is `rl:` (in rate_limit.py), not `ratelimit:`. Previously, the wrong prefix prevented buckets from clearing, causing 429 errors from the 4th round onwards. These 429s were skewing latency numbers, which the "errors" column helped identify.
    """
    from redis_client import redis_client

    for key in redis_client.scan_iter("rl:*", count=500):
        redis_client.delete(key)


async def main():
    print(f"\nconcurrency={CONCURRENCY}  rounds={ROUNDS}  "
          f"(= {CONCURRENCY * ROUNDS} requests per strategy)\n")

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        tokens = await login_all(client, CONCURRENCY)

        # Find an available seat
        seats = (await client.get("/api/events/1/seats")).json()
        seat_id = next(s["id"] for s in seats if s["status"] == "available")
        print(f"target seat id = {seat_id}\n")

        results = []
        for strategy in ORDER:
            # Warm-up round — the first request populates the connection pool and query plan cache. Measuring it yields inaccurate results.
            free_seat(seat_id)
            _clear_buckets()
            await one_round(client, tokens, seat_id, strategy)

            results.append(await measure(client, tokens, seat_id, strategy))

        free_seat(seat_id)

    print(f"{'strategy':<14}{'reqs':>7}{'won':>6}{'409':>7}{'err':>6}"
          f"{'p50':>9}{'p95':>9}{'p99':>9}{'max':>9}")
    print("-" * 76)
    for r in results:
        print(f"{r['strategy']:<14}{r['requests']:>7}{r['wins']:>6}"
              f"{r['conflicts']:>7}{r['errors']:>6}"
              f"{r['p50']:>8.1f}{r['p95']:>8.1f}{r['p99']:>8.1f}{r['max']:>8.1f}")

    by = {r["strategy"]: r for r in results}
    o, p = by["optimistic"], by["pessimistic"]
    print(f"\np50: pessimistic {p['p50'] / o['p50']:.2f}x optimistic")
    print(f"p99: pessimistic {p['p99'] / o['p99']:.2f}x optimistic")

    # Exactly one win is expected per round — otherwise, regardless of the numbers, the comparison is invalid
    expected = ROUNDS
    for r in results:
        ok = "OK" if r["wins"] == expected else f"BAD (expected {expected})"
        print(f"{r['strategy']:<14} wins={r['wins']}  {ok}")


asyncio.run(main())
