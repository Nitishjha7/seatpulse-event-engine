"""
Optimistic vs pessimistic — sirf DB claim step ko isolate karke maapna.

---- Locust se ye alag kyu hai ----

Locust ne poore system ko maapa, aur wahan do cheezein DB strategy ko
dhak deti hain:

  1. Redis lock  — 1433 me se 1432 requests ko DB tak aane hi nahi deta
  2. Admission control (30 slots) — 300 users me har request ~3s queue me
     khadi rehti hai. Us 3 second ke saamne DB ka 5ms dikhta hi nahi.

Isliye yahan:
  - Redis layer OFF (`redis_lock=off`)
  - concurrency admission limit se NEECHE (default 25 vs 30 slots), taki
    queue wait numbers me na ghule
  - login pehle ek baar, warna bcrypt (~400ms) sab kuch dabaa deta hai
  - har round me seat wapas free karke ASLI contention dubara paida karte
    hain — kyunki ek hi contention event maapna shor (noise) hota hai

Chalao:
    docker compose exec backend python /loadtest/micro_benchmark.py

⚠️ Backend BENCHMARK_MODE=true ke saath chal raha hona chahiye.
"""

import asyncio
import os
import statistics
import time

import httpx

BASE = os.getenv("BENCH_HOST", "http://localhost:8000")
CONCURRENCY = int(os.getenv("BENCH_CONCURRENCY", "25"))
ROUNDS = int(os.getenv("BENCH_ROUNDS", "40"))
# Kaun pehle chale.
#
# Ye knob zaroori hai: jo strategy pehle chalti hai wo thodi thandi machine
# par chalti hai (pool, page cache, query plans). Agar dono order me result
# ek jaisa aata hai, tabhi maan sakte hain ki farak asli hai.
ORDER = os.getenv("BENCH_ORDER", "optimistic,pessimistic").split(",")
PASSWORD = os.getenv("SEED_PASSWORD", "demo1234")


async def login_all(client, n):
    """Sab users ka token pehle hi le lo — bcrypt measurement se bahar rahe."""
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
    Seat ko wapas available karo — SQL se, API se nahi.

    API se karte to cancel endpoint ki latency bhi round ke beech aa jati
    aur agla round pichle ke asar me chalta.
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
    """CONCURRENCY requests ek saath, ek hi seat par."""
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

    # asyncio.gather = sab ek saath nikalte hain, ek ke baad ek nahi
    return await asyncio.gather(*(attempt(t) for t in tokens[:CONCURRENCY]))


async def measure(client, tokens, seat_id, strategy):
    latencies, wins, conflicts, errors = [], 0, 0, 0

    for _ in range(ROUNDS):
        free_seat(seat_id)
        # Rate limiter buckets bhi saaf, warna 4th round se 429 milne lagega
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
    Rate limit buckets saaf karo.

    ⚠️ Prefix `rl:` hai (rate_limit.py me), `ratelimit:` nahi. Pehle galat
    prefix likha tha — buckets clear hote hi nahi the, aur 4th round se
    har request 429 khaane lagti thi. Numbers me wo 429 latency ke roop me
    ghul rahe the, aur "errors" column ne hi ye pakda.
    """
    from redis_client import redis_client

    for key in redis_client.scan_iter("rl:*", count=500):
        redis_client.delete(key)


async def main():
    print(f"\nconcurrency={CONCURRENCY}  rounds={ROUNDS}  "
          f"(= {CONCURRENCY * ROUNDS} requests per strategy)\n")

    async with httpx.AsyncClient(base_url=BASE, timeout=60.0) as client:
        tokens = await login_all(client, CONCURRENCY)

        # Ek available seat dhoondo
        seats = (await client.get("/api/events/1/seats")).json()
        seat_id = next(s["id"] for s in seats if s["status"] == "available")
        print(f"target seat id = {seat_id}\n")

        results = []
        for strategy in ORDER:
            # Warm-up round — pehli request me connection pool aur query
            # plan cache bhar jate hain. Usse maapna galat number deta hai.
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

    # Har round me theek ek jeetna chahiye — warna number kaise bhi hon,
    # comparison bekaar hai
    expected = ROUNDS
    for r in results:
        ok = "OK" if r["wins"] == expected else f"BAD (expected {expected})"
        print(f"{r['strategy']:<14} wins={r['wins']}  {ok}")


asyncio.run(main())
