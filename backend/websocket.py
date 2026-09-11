"""
WebSocket connections + real-time broadcasting.

⭐ Core of Phase 5.

Problem solved:
  When User A holds a seat, User B sees it as available until they refresh.
  If B clicks, they receive a 409 error, resulting in a poor experience.
  Now, B will see the seat status update immediately.

Architecture — Why Redis Pub/Sub instead of direct broadcasting:

  Direct broadcasting suffices for a single backend server. However, in
  production, multiple Uvicorn workers run, each maintaining its own
  WebSocket connections:

      Worker 1: User A, User C sockets
      Worker 2: User B socket

  User A's lock is processed on Worker 1. If it only notifies local sockets,
  User B will never be informed.

  Therefore: Each worker PUBLISHES to a Redis channel, and every worker
  SUBSCRIBES to that channel to notify its local sockets. Redis acts as
  the message bus.

  Bonus: Redis is already in use (since Phase 4) — no new services required.
"""

import asyncio
import json
import logging

import redis.asyncio as aioredis
from fastapi import WebSocket

from config import settings
from redis_client import redis_client

logger = logging.getLogger(__name__)

# Each event has a dedicated channel — "seatpulse:event:1"
# This ensures updates for event 1 do not reach users of event 2.
CHANNEL_PREFIX = "seatpulse:event:"


def channel_for(event_id: int) -> str:
    return f"{CHANNEL_PREFIX}{event_id}"


class ConnectionManager:
    """
    Tracks which sockets are listening to which event.

    Structure: { event_id: {socket1, socket2, ...} }
    Uses a set instead of a list for O(1) removal and to prevent duplicates.
    """

    def __init__(self) -> None:
        self._rooms: dict[int, set[WebSocket]] = {}
        # Lock prevents dictionary corruption during concurrent connect/disconnect events.
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
                # Remove empty rooms to keep the dictionary clean.
                if not room:
                    self._rooms.pop(event_id, None)

    def count(self, event_id: int) -> int:
        return len(self._rooms.get(event_id, ()))

    def rooms(self) -> list[int]:
        """Returns active event rooms (for admin stats)."""
        return list(self._rooms.keys())

    async def broadcast_local(self, event_id: int, message: dict) -> None:
        """
        Sends a message to sockets connected to this worker.

        Collects dead connections to remove later, as modifying a set while
        iterating over it raises an error.
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
                # Client disconnected without triggering the disconnect handler,
                # usually due to a network failure.
                dead.append(ws)

        if dead:
            async with self._lock:
                room = self._rooms.get(event_id, set())
                for ws in dead:
                    room.discard(ws)


manager = ConnectionManager()


def publish(event_id: int, message: dict) -> None:
    """
    Publishes a message to the Redis channel — SYNC function.

    Synchronous because routes are defined as `def` (not `async def`).
    This is a fire-and-forget operation taking ~0.1ms.

    This does not send messages directly to sockets; that is handled by
    the _subscriber_loop running in each worker.
    """
    try:
        redis_client.publish(channel_for(event_id), json.dumps(message, default=str))
    except Exception as exc:
        # Broadcast failure should not block booking.
        # Real-time updates are "nice to have"; booking is "must have".
        logger.warning("Broadcast publish fail: %s", exc)


async def _subscriber_loop() -> None:
    """
    Listens to Redis channels and forwards messages to local sockets.

    Runs as a background task for the duration of the application lifecycle.
    """
    while True:
        try:
            conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = conn.pubsub()
            # psubscribe = pattern subscribe. Since each event has a unique channel,
            # "seatpulse:event:*" allows listening to all events simultaneously.
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
            # Normal application shutdown.
            raise
        except Exception as exc:
            # Redis restart or network failure. Retry after 2 seconds.
            logger.warning("Subscriber failed, retrying in 2s: %s", exc)
            await asyncio.sleep(2)


def start_subscriber() -> asyncio.Task:
    return asyncio.create_task(_subscriber_loop())
