"""
Phase 16 ka proof — WebSocket broadcast alag worker processes ke paar jaata hai.

---- Ye test kyu zaroori hai ----

Phase 5 me maine broadcast ke liye Redis pub/sub use kiya tha, ek simple
Python dict ke bajaye. Wajah likhi thi: "multi-worker me har worker alag
process hai, in-memory dict share nahi hota."

Par wo daawa aaj tak KABHI test nahi hua — kyunki dev me hamesha ek hi
worker chalta raha. Ek argument jo kabhi verify na hua ho, wo sirf ek
umeed hai.

---- Test kya karta hai ----

1. Kai WebSocket clients connect karo. Har connection OS kisi bhi worker
   ko de sakta hai, isliye ye alag-alag processes me bant jaate hain.
2. `/api/health` bhi kai baar hit karo — `worker_pid` se SABIT karo ki
   sach me kai processes chal rahe hain (warna test kuch prove nahi karta).
3. EK seat book karo. Wo booking kisi EK worker me hoti hai.
4. Check karo ki SAARE clients ko update mila — chahe wo kisi bhi worker
   se juda ho.

Agar broadcast in-memory hota, to sirf usi worker ke clients ko message
milta jisme booking hui thi. Baaki chup rehte, aur unke seat grid me seat
hari dikhti rehti jabki wo bik chuki hai.

Chalao (prod stack chalte hue):
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


async def probe_workers(client, n=40):
    """Kitne alag worker processes jawab de rahe hain."""
    pids = set()
    for _ in range(n):
        r = await client.get("/api/health")
        pids.add(r.json().get("worker_pid"))
    return pids


async def listen(url, got, ready, idx):
    """Ek WebSocket client — seat_update aane tak sunta rahe."""
    async with websockets.connect(url) as ws:
        ready.set()
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=15))
                if msg.get("type") == "seat_update" and msg.get("action") == "booked":
                    got.add(idx)
                    return
        except asyncio.TimeoutError:
            # Ye failure hai — is client tak broadcast pahuncha hi nahi
            return


async def main():
    async with httpx.AsyncClient(base_url=BASE, timeout=30.0) as client:
        pids = await probe_workers(client)
        print(f"\nJawab dene wale worker processes: {len(pids)}  -> {sorted(pids)}")

        if len(pids) < 2:
            print("\n⚠️  Sirf ek worker mila. Ye test single-worker pe kuch")
            print("   prove nahi karta — prod stack se chalao:")
            print("   docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d")
            return 1

        # Har client ka alag user — ek hi user ke kai connections bhi chalte
        # hain, par alag users asli scenario ke kareeb hai
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
        # Sab connect ho jaayein, TAB booking karo — warna jo late juda
        # usse message miss hona normal hai aur test jhoothi fail hogi
        await asyncio.gather(*(r.wait() for r in readies))
        print(f"{CLIENTS} WebSocket clients connected")

        booker = tokens[0]
        res = await client.post(
            "/api/bookings",
            json={"seat_id": target["id"]},
            headers={"Authorization": f"Bearer {booker}"},
        )
        print(f"Seat {target['row_label']}-{target['seat_number']} booked "
              f"(HTTP {res.status_code}) — ek worker me")

        await asyncio.gather(*tasks)

        print(f"\nBroadcast mila: {len(got)} / {CLIENTS} clients")

        # Cleanup
        if res.status_code == 201:
            await client.delete(
                f"/api/bookings/{res.json()['id']}",
                headers={"Authorization": f"Bearer {booker}"},
            )

        if len(got) == CLIENTS:
            print(f"\n✅ PASS — {len(pids)} workers, sab {CLIENTS} clients tak "
                  f"broadcast pahuncha")
            print("   Redis pub/sub sach me process boundary paar kar raha hai.")
            return 0

        print(f"\n❌ FAIL — {CLIENTS - len(got)} clients ko update nahi mila")
        print("   Matlab broadcast worker ke andar hi atka reh gaya.")
        return 1


sys.exit(asyncio.run(main()))
