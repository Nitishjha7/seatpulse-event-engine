"""
Phase 14 proof — surge live badhta hai, aur locked quote nahi badalta.

Sab kuch asli HTTP se, koi mocking nahi.
"""

import asyncio
import json

import httpx
import websockets

BASE = "http://localhost:8000"


def token(c, email):
    r = c.post("/api/auth/login", json={"email": email, "password": "demo1234"})
    r.raise_for_status()
    return r.json()["access_token"]


def H(t):
    return {"Authorization": f"Bearer {t}"}


async def watch(event_id, tok, out, ready):
    url = f"ws://localhost:8000/ws/events/{event_id}?token={tok}"
    async with websockets.connect(url) as ws:
        ready.set()
        try:
            while True:
                msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=12))
                if msg["type"] == "pricing_update":
                    out.append(msg["pricing"])
        except (asyncio.TimeoutError, Exception):
            pass


async def main():
    c = httpx.Client(base_url=BASE, timeout=30.0)
    org = token(c, "organizer@seatpulse.dev")
    a = token(c, "demo@seatpulse.dev")
    b = token(c, "user1@seatpulse.dev")

    ev = c.post(
        "/api/organizer/events",
        headers=H(org),
        json={
            "name": "Surge Proof",
            "venue": "Proof Hall",
            "starts_at": "2027-09-09T18:00:00Z",
            "seats_per_row": 5,
            "price_tiers": [{"rows": 2, "price": 1000}],
            "dynamic_pricing": True,
            "demand_factor": 1.0,
            "max_surge": 2.0,
        },
    ).json()
    eid = ev["id"]
    print(f"Event {eid}: 10 seats @ base Rs.1000, demand_factor=1.0\n")

    pricing_msgs = []
    ready = asyncio.Event()
    task = asyncio.create_task(watch(eid, a, pricing_msgs, ready))
    await ready.wait()

    seats = c.get(f"/api/events/{eid}/seats").json()

    # --- A hold karta hai: quote lock ---
    lock = c.post(f"/api/seats/{seats[0]['id']}/lock", headers=H(a)).json()
    print(f"A ne {seats[0]['row_label']}-{seats[0]['seat_number']} hold ki")
    print(f"  quoted price = Rs.{lock['price']}\n")

    # --- B taabadtod 4 seats khareedta hai ---
    print("B 4 seats khareedta hai:")
    for s in seats[1:5]:
        r = c.post("/api/bookings", headers=H(b), json={"seat_id": s["id"]})
        fresh = c.get(f"/api/events/{eid}").json()["pricing"]
        avail = [x for x in c.get(f"/api/events/{eid}/seats").json()
                 if x["status"] == "available"]
        print(f"  {s['row_label']}-{s['seat_number']} booked @ Rs.{r.json()['amount']:>6.0f}"
              f"  -> multiplier {fresh['multiplier']:.2f}"
              f"  | baaki seats ab Rs.{avail[0]['current_price']:.0f}")

    print(f"\nWebSocket pe aaye pricing_update messages: {len(pricing_msgs)}")
    for m in pricing_msgs:
        print(f"  +{m['surge_percent']}%  sold {m['sold']}/{m['total']}"
              f"  next increase in {m['seats_until_increase']} seat(s)")

    # --- A ab bhi purane price par ---
    held = next(x for x in c.get(f"/api/events/{eid}/seats").json()
                if x["id"] == seats[0]["id"])
    print(f"\nA ki held seat: held_price=Rs.{held['held_price']:.0f}"
          f"  (market ab Rs.{held['current_price']:.0f})")

    booked = c.post("/api/bookings", headers=H(a), json={"seat_id": seats[0]["id"]}).json()
    print(f"A ne book ki -> charged Rs.{booked['amount']:.0f}")
    print("MATCH" if booked["amount"] == lock["price"] else "MISMATCH — BUG")

    task.cancel()

    # cleanup
    for t in (a, b):
        for bk in c.get("/api/bookings", headers=H(t)).json():
            if bk["event_id"] == eid and bk["status"] == "confirmed":
                c.delete(f"/api/bookings/{bk['id']}", headers=H(t))
    c.delete(f"/api/organizer/events/{eid}", headers=H(org))
    print(f"\ncleanup: event {eid} deleted")


asyncio.run(main())
