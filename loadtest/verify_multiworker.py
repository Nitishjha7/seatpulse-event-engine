"""
Phase 16 proof — WebSocket broadcast crosses worker process boundaries.

---- Why this test is necessary ----

In Phase 5, I used Redis pub/sub for broadcasting instead of a simple Python dict. The reason: "In multi-worker setups, each worker is a separate process; in-memory dicts are not shared."

However, that claim was NEVER tested because dev environments only run one worker. An unverified argument is just a hope.

---- What the test does ----

1. Connect multiple WebSocket clients. The OS distributes connections across workers, spreading them across different processes.
2. Hit `/api/health` multiple times — use `worker_pid` to PROVE multiple processes are running (otherwise the test proves nothing).
3. Book ONE seat. The booking occurs in exactly ONE worker.
4. Verify that ALL clients receive the update, regardless of which worker they are connected to.

If the broadcast were in-memory, only clients connected to the booking worker would receive the message. Others would remain silent, showing the seat as available even though it is sold.

Run (with prod stack active):
    docker compose exec backend python /loadtest/verify_multiworker.py
"""

import asyncio
import json
import os
import sys

import httpx
import websockets

BASE = os.getenv("BENCH_HOST", "http://localhost:8000")
WS_BASE = BASE.replace("http", "ws")
CLIENTS = int(os.getenv("WS_CLIENTS", "12"))
PASSWORD = os.getenv("SEED_PASSWORD", "demo1234")


async def probe_workers(n=40):
    """
    Check how many distinct worker processes are responding.

    ⚠️ Each probe requires a NEW connection.

    Sending 40 requests via a single httpx client reuses the same keep-alive
    TCP connection, which is pinned to one worker. Result: Even with 4 workers,
    it reports "1 worker". (This was observed during test development.)

    Worker distribution occurs at the CONNECTION level, not the request level.
    Therefore, a new connection = a new (potentially different) worker.
    """
    pids = set()
    for _ in range(n):
        async with httpx.AsyncClient(base_url=BASE, timeout=10.0) as c:
            r = await c.get("/api/health")
            pids.add(r.json().get("worker_pid"))
    return pids


async def listen(url, got, ready, idx):
    """A WebSocket client — listens until a seat_update is received."""
    async with websockets.connect(url) as ws:
        ready.set()
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if msg.get("type") == "seat_update" and msg.get("action") == "booked":
                    got.add(idx)
                    return
        except asyncio.TimeoutError:
            # This is a failure — the broadcast did not reach this client
            return


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        pids = await probe_workers()
        print(f"\nResponding worker processes: {len(pids)}  -> {sorted(pids)}")

        if len(pids) < 2:
            print("\n⚠️  Only one worker found. This test proves nothing")
            print("   on a single-worker setup — run with the prod stack:")
            print("   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d")
            return 1

        # Each client is a unique user — while one user can have multiple
        # connections, distinct users better simulate a real-world scenario.
        tokens = []
        for i in range(1, CLIENTS + 1):
            r = await client.post(
                "/api/auth/login",
                json={"email": f"user{i}@seatpulse.dev", "password": PASSWORD},
            )
            r.raise_for_status()
            tokens.append(r.json()["access_token"])

        seats = (await client.get("/api/events/1/seats")).json()
        target = next(s for s in seats if s["status"] == "available")

        got = set()
        readies = [asyncio.Event() for _ in range(CLIENTS)]
        tasks = [
            asyncio.create_task(
                listen(f"{WS_BASE}/ws/events/1?token={tokens[i]}", got, readies[i], i)
            )
            for i in range(CLIENTS)
        ]
        # Wait for all connections to establish before booking — otherwise,
        # late joiners might miss the message, causing a false test failure.
        await asyncio.gather(*(r.wait() for r in readies))
        print(f"{CLIENTS} WebSocket clients connected")

        booker = tokens[0]
        res = await client.post(
            "/api/bookings",
            json={"seat_id": target["id"]},
            headers={"Authorization": f"Bearer {booker}"},
        )
        print(f"Seat {target['row_label']}-{target['seat_number']} booked "
              f"(HTTP {res.status_code}) — in one worker")

        await asyncio.gather(*tasks)

        print(f"\nBroadcast received: {len(got)} / {CLIENTS} clients")

        # Cleanup
        if res.status_code == 201:
            await client.delete(
                f"/api/bookings/{res.json()['id']}",
                headers={"Authorization": f"Bearer {booker}"},
            )

        if len(got) == CLIENTS:
            print(f"\n✅ PASS — {len(pids)} workers, broadcast reached all "
                  f"{CLIENTS} clients")
            print("   Redis pub/sub is successfully crossing process boundaries.")
            return 0

        print(f"\n❌ FAIL — {CLIENTS - len(got)} clients did not receive the update")
        print("   Meaning the broadcast remained trapped within the worker.")
        return 1


sys.exit(asyncio.run(main()))
