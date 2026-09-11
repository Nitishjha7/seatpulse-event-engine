# Phase 5 — WebSockets + Real-Time Broadcasting

[Phase 4 — Redis Locking](04-redis-locking.md) follow-up.

**Goal:** Hold a seat in one tab → **instantly turn yellow** in another tab, without refreshing.

---

## Problem Solved

After Phase 4, locking was correct, but the user experience was poor:

```
User A: holds seat B-5
User B: seat B-5 still looks GREEN on their screen
User B: clicks -> 409 "seat already held"
User B: 😠
```

User B was seeing stale data because their page was outdated. **Solution:** Notify everyone as soon as a seat status changes.

| | Phase 4 | Phase 5 |
|---|---|---|
| Other user's change | Refresh required | **Instant** update |
| Data flow | Client polls (pull) | Server pushes (push) |
| After every booking | Full seat list re-download | **Single seat** update |

---

## Architecture — Why Redis Pub/Sub?

A simple broadcast could look like this:

```python
for socket in connected_sockets:
    await socket.send_json(message)
```

**This works on a single server but breaks in production.**

In production, multiple Uvicorn workers run, each with **its own** WebSocket connections:

```
Worker 1: User A, User C sockets
Worker 2: User B socket
```

User A's lock request is processed by Worker 1. If it only notifies its local sockets, **User B will never know** because they are on a different worker.

**Solution — Redis message bus:**

```
                    ┌──────────────────────────┐
   User A ─ lock ──▶│  Worker 1                │
                    │  1. Acquire lock in Redis │
                    │  2. Update Postgres      │
                    │  3. PUBLISH to Redis     │
                    └────────────┬──────────────┘
                                 │
                    ┌────────────▼──────────────┐
                    │  Redis channel            │
                    │  seatpulse:event:1        │
                    └──┬──────────────────┬─────┘
                       │ SUBSCRIBE        │ SUBSCRIBE
              ┌────────▼──────┐   ┌───────▼────────┐
              │  Worker 1     │   │  Worker 2      │
              │  -> User A, C │   │  -> User B     │
              └───────────────┘   └────────────────┘
```

Every worker both **publishes** and **subscribes**. Messages from any worker reach all clients.

> **Bonus:** Redis is already in use (from Phase 4). No new services like RabbitMQ/Kafka are required.

---

## Step 1 — `websocket.py`

Full code: [../backend/websocket.py](../../backend/websocket.py)

### ConnectionManager

```python
self._rooms: dict[int, set[WebSocket]] = {}     # { event_id: {socket, ...} }
self._lock = asyncio.Lock()
```

| Item | Reason |
|---|---|
| Event-wise rooms | Updates for event 1 should not reach users of event 2 |
| `set` (not list) | O(1) removal, prevents duplicate socket additions |
| `asyncio.Lock` | Prevents dictionary corruption during concurrent connect/disconnect |

**Dead connections:**
```python
dead = []
for ws in sockets:
    try:
        await ws.send_json(message)
    except Exception:
        dead.append(ws)      # remove later
```

Connections may drop without triggering the disconnect handler (network failure). Modifying a set while iterating causes Python errors, so we collect and remove them later.

### Publish — sync function

```python
def publish(event_id: int, message: dict) -> None:
    try:
        redis_client.publish(channel_for(event_id), json.dumps(message, default=str))
    except Exception as exc:
        logger.warning("Broadcast publish fail: %s", exc)
```

| Decision | Reason |
|---|---|
| **Sync** (not `async def`) | Our routes are sync. This is fire-and-forget, taking ~0.1ms |
| **Exception swallow** | Broadcast failure **must not fail the booking**. Real-time updates are "nice to have," booking is "must have" |

> This is a design choice. If Redis Pub/Sub goes down, users won't get live updates, but their bookings will still succeed.

### Subscriber loop

```python
async def _subscriber_loop():
    while True:
        try:
            conn = aioredis.from_url(settings.REDIS_URL, decode_responses=True)
            pubsub = conn.pubsub()
            await pubsub.psubscribe(f"{CHANNEL_PREFIX}*")

            async for raw in pubsub.listen():
                ...
                await manager.broadcast_local(event_id, json.loads(raw["data"]))

        except asyncio.CancelledError:
            raise                    # app shutting down — normal
        except Exception:
            await asyncio.sleep(2)   # Redis down — retry in 2s
```

| Item | Reason |
|---|---|
| `redis.asyncio` | Runs in an async context. A sync client would block the event loop |
| `psubscribe` (pattern) | `seatpulse:event:*` — separate channels per event, but one subscription listens to all |
| `while True` + retry | Subscriber automatically reconnects if Redis restarts |
| `CancelledError` re-raise | Ensures the task stops during app shutdown |

### Lifespan startup

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = start_subscriber()
    yield
    task.cancel()
```

Subscriber starts with the app and stops on shutdown.

---

## Step 2 — WebSocket endpoint

```python
@app.websocket("/ws/events/{event_id}")
async def event_socket(websocket: WebSocket, event_id: int):
    await manager.connect(websocket, event_id)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await manager.disconnect(websocket, event_id)
```

> ⚠️ **`while True: await receive_text()` is required** — even if we don't expect input from the client.
>
> Without this, the function returns immediately and FastAPI closes the socket. This loop keeps the connection alive and detects disconnects.

> ⚠️ **CORS middleware does not apply to WebSockets** — it is for HTTP. You must check the origin manually in production.

---

## Step 3 — `events_broadcast.py`

Routers should not know WebSocket details. They only need one call:

```python
broadcast_seat_update(db, seat_id, "locked")
```

```python
def broadcast_seat_update(db, seat_id, action):
    seat = db.get(Seat, seat_id)
    if seat is None:
        return
    db.refresh(seat)          # <- REQUIRED
    publish(seat.event_id, {
        "type": "seat_update",
        "action": action,
        "seat": SeatOut.model_validate(seat).model_dump(mode="json"),
    })
```

> ⚠️ **Forgetting `db.refresh(seat)` is the most common bug.**
>
> In routers, we update seats using an `update()` statement with `synchronize_session=False` — meaning SQLAlchemy did not update the cached session object. Without refresh, the **stale status will be broadcast** (e.g., "available" when it is actually "locked").

**Broadcast locations:**

| Location | Action |
|---|---|
| `POST /seats/{id}/lock` | `locked` |
| `DELETE /seats/{id}/lock` | `released` |
| `release_expired_locks()` | `expired` |
| `POST /bookings` | `booked` |
| `DELETE /bookings/{id}` | `cancelled` |

---

## Step 4 — `useWebSocket` hook

Full code: [../frontend/src/hooks/useWebSocket.js](../../frontend/src/hooks/useWebSocket.js)

### Exponential backoff

```js
socket.onclose = () => {
  setStatus('closed')
  if (closedByUsRef.current) return

  const delay = Math.min(1000 * 2 ** retryRef.current, 15000)   // 1s,2s,4s,8s...15s
  retryRef.current += 1
  timerRef.current = setTimeout(connect, delay)
}
```

**Why not fixed 1s retry?** If the server is down, 100 clients hammering it every second will prevent it from recovering. Backoff prevents this. The counter resets on successful connection.

### Three refs, three problems

| Ref | Problem solved |
|---|---|
| `handlerRef` | Callbacks are recreated every render. Using them as dependencies would cause **reconnects on every render** |
| `closedByUsRef` | `onclose` triggers on component unmount — we shouldn't reconnect then |
| `timerRef` | Allows clearing the pending retry timer during cleanup |

### StrictMode

```js
return () => {
  closedByUsRef.current = true
  clearTimeout(timerRef.current)
  socketRef.current?.close()
}
```

In React StrictMode (dev), effects run **twice**. Without cleanup, two sockets open, causing duplicate messages.

### URL

```js
const wsUrl = `${API_URL.replace(/^http/, 'ws')}/ws/events/${eventId}`
```
`http://` → `ws://`, and `https://` → `wss://` (since `https` starts with `http`).

---

## Step 5 — Use in App.jsx

```js
const handleSeatUpdate = useCallback((updatedSeat) => {
  // Replace only that ONE seat, not the whole list
  setSeats((prev) => prev.map((s) => (s.id === updatedSeat.id ? updatedSeat : s)))

  // Did my hold get taken? Clear selection
  setSelectedSeat((prev) => {
    if (!prev || prev.id !== updatedSeat.id) return prev
    const stillMine = updatedSeat.status === 'locked'
      && updatedSeat.locked_by === userRef.current?.id
    if (stillMine) return updatedSeat
    setLockSecondsLeft(0)
    return null
  })
}, [])

const { status: wsStatus } = useWebSocket(event?.id ?? null, handleSeatUpdate)
```

### Counts are now derived

```js
const counts = seats.reduce(
  (acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }),
  {},
)
```

Previously, `event.available_seats` came from the server. Now, counts are derived from `seats` — **they update automatically when a WebSocket message arrives**, without server calls.

### Live badge in header

`DB · Redis · Live` — the third dot is for WebSockets. Pulses when open, yellow when connecting, red when offline.

---

## Step 6 — Restart

No new packages. `--reload` will pick it up:

```bash
docker compose restart backend
```

---

## ✅ Proof

### 1. Health
http://localhost:8000/api/health → `"version": "0.5.0"`

The **Live** dot should appear in the header (green, pulsing).

### 2. ⭐ Two-browser test — the real proof

Open two windows: **one normal, one incognito** (both http://localhost:5173)

| Action | In other window |
|---|---|
| Click green seat in Window A | Seat **instantly turns yellow** — no refresh |
| Release Hold in Window A | **Instantly green** |
| Confirm Booking in Window A | **Instantly red**, counts update |
| Cancel in Window A | **Instantly green** |

Phase 4 required a refresh. Now it doesn't.

### 3. Reconnect test

```bash
docker compose restart backend
```

Watch the browser — badge changes **Live → Offline → Connecting → Live**. No page refresh needed.

Backoff will be visible in the console (1s, 2s, 4s...).

### 4. Script test

```python
# backend/ws_test.py (delete after testing)
import asyncio, json, httpx, websockets

async def main():
    async with websockets.connect("ws://localhost:8000/ws/events/1") as ws:
        async with httpx.AsyncClient() as c:
            await c.post("http://localhost:8000/api/seats/30/lock", json={"user_id": 2})
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5))
        print(msg["action"], msg["seat"]["status"], msg["seat"]["locked_by"])

asyncio.run(main())
```

```bash
docker compose exec backend pip install websockets httpx
docker compose exec backend python ws_test.py
```

**Actual output:**
```
WS connected
lock -> 200
MSG 1: seat_update locked locked locked_by 2
book -> 201
MSG 2: seat_update booked booked
```

### 5. Watch Redis pub/sub live

```bash
docker compose exec redis redis-cli psubscribe "seatpulse:event:*"
```
Click a seat in the UI — raw JSON messages will stream in the terminal.

### 6. Browser DevTools
F12 → Network → **WS** tab → `/ws/events/1` → **Messages**. You will see frames for every seat change.

---

## Interview Questions

| Question | Answer |
|---|---|
| "Why not polling?" | 1000 clients × every 2s = 500 req/sec just to ask "did anything change?". WebSockets only send traffic when something actually changes |
| "How does it work across multiple servers?" | Redis Pub/Sub. Every worker publishes and subscribes, so changes from any worker reach everyone |
| "What if the connection drops?" | Reconnect via exponential backoff (1s→15s), and fetch the full seat list again upon reconnect |
| "What if a message is missed?" | This is at-most-once delivery. That's why we do a full refresh on reconnect — WebSockets are an **optimization**, not the source of truth |
| "What if broadcast fails?" | The booking still succeeds. `publish()` swallows exceptions — real-time is nice-to-have, booking is must-have |
| "How to authenticate WebSockets?" | Not implemented yet. Need to send JWT via query param or first message — CORS doesn't work on WS |

---

## Common Problems

| Problem | Fix |
|---|---|
| Badge stuck on "Connecting" | Is the backend running? `docker compose logs backend` |
| `WebSocket connection failed` | Wrong URL — must be `ws://`, not `http://` |
| Duplicate messages | Missing cleanup in hook (StrictMode opens two sockets) |
| Update arrives but status is old | `db.refresh(seat)` missing in `broadcast_seat_update` |
| Change in one tab, not the other | `docker compose exec redis redis-cli psubscribe "seatpulse:event:*"` — is the message arriving? |
| No reconnect after backend restart | Check browser console, backoff can go up to 15s — wait a moment |
| `RuntimeError: Event loop is closed` on shutdown | Is `task.cancel()` in lifespan? |

---

## Files created/modified

```
backend/
├── websocket.py            ← new  ⭐ ConnectionManager + Redis pub/sub
├── events_broadcast.py     ← new  (simple helper for routers)
├── main.py                 ← update (lifespan + /ws endpoint)
└── routers/
    ├── seats.py            ← update (broadcast on lock/unlock/expired)
    └── bookings.py         ← update (broadcast on book/cancel)

frontend/src/
├── hooks/
│   └── useWebSocket.js     ← new  ⭐ with reconnect
├── App.jsx                 ← update (live updates, derived counts)
└── components/
    └── BookingPanel.jsx    ← update (counts prop)
```

---

## Commit

```bash
git add .
git commit -m "Phase 5: WebSocket real-time seat updates via Redis pub/sub"
git push
```

---

## Related

- [Phase 4 — Redis Locking](04-redis-locking.md) — locking
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — what's next

---

**Next:** Phase 6 — Load testing (Locust). 500 concurrent users, one seat, and proof of exactly 1 booking. That number goes on the resume.
