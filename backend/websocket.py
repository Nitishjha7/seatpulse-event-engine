"""
WebSocket connections + real-time broadcasting.

⭐ Phase 5 ka core.

Problem jo ye solve karta hai:
  User A seat hold karta hai -> User B ko wo seat tab tak hari dikhti rehti
  hai jab tak wo refresh na kare. B click karta hai, 409 milta hai, bura
  experience. Ab B ko turant peeli dikhegi.

Architecture — Redis Pub/Sub kyu, seedha broadcast kyu nahi:

  Ek backend server ho to seedha broadcast kaafi hai. Par production me
  do-teen uvicorn workers chalte hain, aur har worker ke paas apne alag
  WebSocket connections hote hain:

      Worker 1: User A, User C ke sockets
      Worker 2: User B ka socket

  User A ka lock Worker 1 pe process hua. Agar wo sirf apne local sockets
  ko batayega to User B ko kabhi pata hi nahi chalega.

  Isliye: har worker Redis channel pe PUBLISH karta hai, aur har worker
  usi channel ko SUBSCRIBE karke apne local sockets ko bhejta hai.
  Redis message bus ban jata hai.

  Bonus: Redis pehle se hai (Phase 4 se) — koi nayi service nahi lagi.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import WebSocket

from config import settings
from redis_client import redis_client

logger = logging.getLogger(__name__)

# Har event ka apna channel — "seatpulse:event:1"
# Isse event 1 ke updates event 2 ke users tak nahi jaate.
CHANNEL_PREFIX = "seatpulse:event:"


def channel_for(event_id: int) -> str:
    return f"{CHANNEL_PREFIX}{event_id}"


class ConnectionManager:
    """
    Kaun sa socket kis event ko sun raha hai, iska hisaab rakhta hai.

    Structure: { event_id: {socket1, socket2, ...} }
    Set isliye (list nahi) — remove O(1) me ho jata hai aur duplicates nahi hote.
    """

    def __init__(self) -> None:
        self._rooms: dict[int, set[WebSocket]] = {}
        # Lock isliye: ek saath do connect/disconnect aayein to dict corrupt na ho
        self._lock = asyncio.Lock()

    async def connect(self, websocket: WebSocket, event_id: int) -> None:
        await websocket.accept()
        async with self._lock:
            self._rooms.setdefault(event_id, set()).add(websocket)
        logger.info("WS connected to event %s (total %s)", event_id, self.count(event_id))

    async def disconnect(self, websocket: WebSocket, event_id: int) -> None:
        async with self._lock:
            room = self._rooms.get(event_id)
            if room:
                room.discard(websocket)
                # Khali room dict me pada na rahe
                if not room:
                    self._rooms.pop(event_id, None)

    def count(self, event_id: int) -> int:
        return len(self._rooms.get(event_id, ()))

    def rooms(self) -> list[int]:
        """Kaunse events ke rooms abhi khule hain (admin stats ke liye)."""
        return list(self._rooms.keys())

    async def broadcast_local(self, event_id: int, message: dict) -> None:
        """
        Is worker ke sockets ko message bhejo.

        Dead connections ko jama karke baad me hatate hain — set ke upar
        loop chalate hue usme se remove karna error deta hai.
        """
        async with self._lock:
            sockets = list(self._rooms.get(event_id, ()))

        if not sockets:
            return

        dead = []
        for ws in sockets:
            try:
                await ws.send_json(message)
            except Exception:
                # Client ja chuka hai par disconnect handler nahi chala.
                # Aisa network tootne par hota hai.
                dead.append(ws)

        if dead:
            async with self._lock:
                room = self._rooms.get(event_id, set())
                for ws in dead:
                    room.discard(ws)


manager = ConnectionManager()


def publish(event_id: int, message: dict) -> None:
    """
    Message Redis channel pe bhejo — SYNC function.

    Sync isliye ki hamare routes bhi sync hain (`def`, `async def` nahi).
    Ye ek fire-and-forget hai, ~0.1ms lagta hai.

    Yahan se message seedha kisi socket pe nahi jata. Wo kaam
    _subscriber_loop karta hai, jo har worker me chal raha hota hai.
    """
    try:
        redis_client.publish(channel_for(event_id), json.dumps(message, default=str))
    except Exception as exc:
        # Broadcast fail hone se booking fail nahi honi chahiye.
        # Real-time update ek "nice to have" hai — booking "must have" hai.
        logger.warning("Broadcast publish fail: %s", exc)


async def _subscriber_loop() -> None:
    """
    Redis channels ko sunta rehta hai aur local sockets ko forward karta hai.

    App start hote hi ek background task ki tarah chalta hai aur band
    hone tak chalta rehta hai.
    """
    while True:
        try:
            conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = conn.pubsub()
            # psubscribe = pattern subscribe. Har event ka channel alag hai,
            # isliye "seatpulse:event:*" pattern se sab ek saath sun lete hain.
            await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")
            logger.info("Redis pub/sub subscriber ready")

            async for raw in pubsub.listen():
                if raw["type"] != "pmessage":
                    continue
                try:
                    event_id = int(str(raw["channel"]).removeprefix(CHANNEL_PREFIX))
                    await manager.broadcast_local(event_id, json.loads(raw["data"]))
                except Exception as exc:
                    logger.warning("Bad pubsub message: %s", exc)

        except asyncio.CancelledError:
            # App band ho raha hai — normal exit
            raise
        except Exception as exc:
            # Redis restart ho gaya ya network gira. 2 sec baad dobara try karo.
            logger.warning("Subscriber gira, 2s me retry: %s", exc)
            await asyncio.sleep(2)


def start_subscriber() -> asyncio.Task:
    return asyncio.create_task(_subscriber_loop())
