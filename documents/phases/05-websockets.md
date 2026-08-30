# Phase 5 — WebSockets + Real-Time Broadcasting

[Phase 4 — Redis Locking](04-redis-locking.md) ke baad ka kaam.

**Kya banega:** ek tab me seat hold karo → **dusre tab me turant peeli** ho jaye, bina refresh ke.

---

## Problem jo ye solve karta hai

Phase 4 ke baad locking to sahi thi, par experience kharab tha:

```
User A: seat B-5 hold kar li
User B: uske screen pe B-5 abhi bhi HARI dikh rahi hai
User B: click karta hai -> 409 "kisi aur ne hold kar li"
User B: 😠
```

User B ko galat data dikh raha tha kyunki uska page purana tha. **Sahi tarika:** seat ki haalat badalte hi sabko bata do.

| | Phase 4 | Phase 5 |
|---|---|---|
| Dusre user ka change | Refresh karo tab pata chalega | **Turant** dikhta hai |
| Data flow | Client poochhta hai (pull) | Server bhejta hai (push) |
| Har booking ke baad | Poora seat list dubara download | Sirf **ek seat** ka update |

---

## Architecture — Redis Pub/Sub kyu?

Seedha broadcast bhi ho sakta tha:

```python
for socket in connected_sockets:
    await socket.send_json(message)
```

**Ye ek server pe theek hai. Production me toot jata hai.**

Production me do-teen uvicorn workers chalte hain, aur har worker ke paas **apne alag** WebSocket connections hote hain:

```
Worker 1: User A, User C ke sockets
Worker 2: User B ka socket
```

User A ka lock request Worker 1 pe process hua. Agar wo sirf apne local sockets ko batayega to **User B ko kabhi pata hi nahi chalega** — wo dusre worker pe hai.

**Hal — Redis message bus:**

```
                    ┌──────────────────────────┐
   User A ─ lock ──▶│  Worker 1                │
                    │  1. Redis me lock lo      │
                    │  2. Postgres update karo  │
                    │  3. Redis pe PUBLISH karo │
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

Har worker **publish** bhi karta hai aur **subscribe** bhi. Message kisi bhi worker se aaye, sabke clients tak pahunchta hai.

> **Bonus:** Redis pehle se hai (Phase 4 se). Koi nayi service nahi lagi — RabbitMQ/Kafka jaisa kuch add nahi karna pada.

---

## Step 1 — `websocket.py`

Poora code: [../backend/websocket.py](../../backend/websocket.py)

### ConnectionManager

```python
self._rooms: dict[int, set[WebSocket]] = {}     # { event_id: {socket, ...} }
self._lock = asyncio.Lock()
```

| Cheez | Kyu |
|---|---|
| Event-wise rooms | Event 1 ke updates event 2 ke users tak nahi jaane chahiye |
| `set` (list nahi) | Remove O(1) me, aur duplicate socket add nahi hota |
| `asyncio.Lock` | Ek saath do connect/disconnect aayein to dict corrupt na ho |

**Dead connections:**
```python
dead = []
for ws in sockets:
    try:
        await ws.send_json(message)
    except Exception:
        dead.append(ws)      # baad me hatayenge
```

Client ja chuka ho par disconnect handler na chala ho (network toot gaya) — aisa hota hai. Loop ke **andar** set se remove karoge to Python error dega, isliye jama karke baad me hatate hain.

### Publish — sync function

```python
def publish(event_id: int, message: dict) -> None:
    try:
        redis_client.publish(channel_for(event_id), json.dumps(message, default=str))
    except Exception as exc:
        logger.warning("Broadcast publish fail: %s", exc)
```

| Decision | Kyu |
|---|---|
| **Sync** (`def`, `async def` nahi) | Hamare routes bhi sync hain. Ye fire-and-forget hai, ~0.1ms lagta hai |
| **Exception swallow** | Broadcast fail hone se **booking fail nahi honi chahiye**. Real-time update "nice to have" hai, booking "must have" |

> Ye dusri baat design ka faisla hai. Redis pub/sub gir jaye to users ko live update nahi milega — par unki booking phir bhi ho jayegi.

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
            raise                    # app band ho raha hai — normal
        except Exception:
            await asyncio.sleep(2)   # Redis gira — 2s baad retry
```

| Cheez | Kyu |
|---|---|
| `redis.asyncio` | Ye async context me chal raha hai. Sync client yahan event loop block kar deta |
| `psubscribe` (pattern) | `seatpulse:event:*` — har event ka channel alag, par ek hi subscription se sab sun lete hain |
| `while True` + retry | Redis restart ho jaye to subscriber apne aap wapas jud jata hai |
| `CancelledError` re-raise | Warna app shutdown ke waqt task marta hi nahi |

### Lifespan me start

```python
@asynccontextmanager
async def lifespan(app: FastAPI):
    task = start_subscriber()
    yield
    task.cancel()
```

App start hote hi subscriber chalu, band hote hi ruk jata hai.

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

> ⚠️ **`while True: await receive_text()` zaroori hai** — bhale hi hum client se kuch expect nahi kar rahe.
>
> Bina iske function turant return kar jayega aur FastAPI socket band kar dega. Ye loop connection ko zinda rakhta hai aur disconnect ka pata bhi deta hai.

> ⚠️ **CORS middleware WebSockets par lagu nahi hota** — wo HTTP ke liye hai. Production me yahan khud origin check karna chahiye.

---

## Step 3 — `events_broadcast.py`

Routers ko WebSocket ki detail nahi pata honi chahiye. Unhe bas ek call karni hai:

```python
broadcast_seat_update(db, seat_id, "locked")
```

```python
def broadcast_seat_update(db, seat_id, action):
    seat = db.get(Seat, seat_id)
    if seat is None:
        return
    db.refresh(seat)          # <- ZAROORI
    publish(seat.event_id, {
        "type": "seat_update",
        "action": action,
        "seat": SeatOut.model_validate(seat).model_dump(mode="json"),
    })
```

> ⚠️ **`db.refresh(seat)` bhoolna sabse common bug hai.**
>
> Routers me humne `update()` statement se seat badli hai, aur `synchronize_session=False` diya hai — matlab SQLAlchemy ne session ka cached object update **nahi** kiya. Refresh ke bina **purana status broadcast ho jayega** (jaise "available" jabki wo abhi "locked" hui hai).

**Kahan-kahan broadcast lagaya:**

| Jagah | Action |
|---|---|
| `POST /seats/{id}/lock` | `locked` |
| `DELETE /seats/{id}/lock` | `released` |
| `release_expired_locks()` | `expired` |
| `POST /bookings` | `booked` |
| `DELETE /bookings/{id}` | `cancelled` |

---

## Step 4 — `useWebSocket` hook

Poora code: [../frontend/src/hooks/useWebSocket.js](../../frontend/src/hooks/useWebSocket.js)

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

**Fixed 1s retry kyu nahi:** server down ho to 100 clients har second hammer karenge, aur wo uthne hi nahi payega. Backoff usse bachata hai. Successful connect pe counter reset ho jata hai.

### Teen refs, teen alag problems

| Ref | Problem jo solve karta hai |
|---|---|
| `handlerRef` | Callback har render pe naya banta hai. Use dependency banate to **har render pe reconnect** hota |
| `closedByUsRef` | Component unmount hone par `onclose` chalta hai — tab reconnect **nahi** karna |
| `timerRef` | Pending retry timer cleanup me clear karna hai |

### StrictMode

```js
return () => {
  closedByUsRef.current = true
  clearTimeout(timerRef.current)
  socketRef.current?.close()
}
```

React StrictMode (dev) me effects **do baar** chalte hain. Cleanup na ho to do sockets khul jaate hain aur har message do baar aata hai.

### URL

```js
const wsUrl = `${API_URL.replace(/^http/, 'ws')}/ws/events/${eventId}`
```
`http://` → `ws://`, aur `https://` → `wss://` (kyunki `https` bhi `http` se shuru hota hai).

---

## Step 5 — App.jsx me use karo

```js
const handleSeatUpdate = useCallback((updatedSeat) => {
  // Sirf wo EK seat replace karo, poori list nahi
  setSeats((prev) => prev.map((s) => (s.id === updatedSeat.id ? updatedSeat : s)))

  // Meri hold kisi aur ke paas chali gayi? Selection saaf karo
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

### Counts ab derive hote hain

```js
const counts = seats.reduce(
  (acc, s) => ({ ...acc, [s.status]: (acc[s.status] || 0) + 1 }),
  {},
)
```

Pehle `event.available_seats` server se aata tha. Ab counts `seats` se nikalte hain — **WebSocket update aate hi apne aap sahi ho jaate hain**, server call ke bina.

### Header me live badge

`DB · Redis · Live` — teesra dot WebSocket ka hai. Open ho to pulse karta hai, connecting pe peela, offline pe laal.

---

## Step 6 — Restart

Naya package koi nahi. `--reload` khud pick kar lega:

```bash
docker compose restart backend
```

---

## ✅ Proof

### 1. Health
http://localhost:8000/api/health → `"version": "0.5.0"`

Header me **Live** dot dikhna chahiye (hara, pulse karta hua).

### 2. ⭐ Do browser test — asli proof

Do window kholo: **ek normal, ek incognito** (dono http://localhost:5173)

| Karo | Dusri window me |
|---|---|
| Window A me hari seat click | Wo seat **turant peeli** — bina refresh |
| Window A me Release Hold | **Turant hari** |
| Window A me Confirm Booking | **Turant laal**, counts badal jaate hain |
| Window A me Cancel | **Turant hari** |

Ye Phase 4 me refresh maangta tha. Ab nahi.

### 3. Reconnect test

```bash
docker compose restart backend
```

Browser me dekho — badge **Live → Offline → Connecting → Live**. Page refresh nahi karna pada.

Console me backoff bhi dikhega (1s, 2s, 4s...).

### 4. Script se test

```python
# backend/ws_test.py (test ke baad delete kar dena)
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

### 5. Redis pub/sub live dekho

```bash
docker compose exec redis redis-cli psubscribe "seatpulse:event:*"
```
Ab UI me seat click karo — raw JSON messages terminal me behte dikhenge.

### 6. Browser DevTools
F12 → Network → **WS** tab → `/ws/events/1` → **Messages**. Har seat change pe frame aata dikhega.

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Polling kyu nahi kiya?" | 1000 clients × har 2 sec = 500 req/sec sirf "kuch badla?" poochhne ke liye. WebSocket me sirf tab traffic hota hai jab actually kuch badle |
| "Multiple servers pe kaise kaam karega?" | Redis pub/sub. Har worker publish aur subscribe dono karta hai, isliye kisi bhi worker ka change sab tak pahunchta hai |
| "Connection toot jaye to?" | Exponential backoff se reconnect (1s→15s), aur reconnect ke baad poora seat list dubara fetch hota hai |
| "Message miss ho gaya to?" | Ye at-most-once delivery hai. Isliye reconnect pe full refresh karte hain — WebSocket **optimization** hai, source of truth nahi |
| "Broadcast fail ho jaye to booking ka kya?" | Booking ho jayegi. `publish()` exception swallow karta hai — real-time nice-to-have hai, booking must-have |
| "WebSocket authenticate kaise karoge?" | Abhi nahi kiya. Query param ya first-message me JWT bhejna hoga — CORS WS pe kaam nahi karta |

---

## Common Problems

| Problem | Fix |
|---|---|
| Badge hamesha "Connecting" | Backend chal raha hai? `docker compose logs backend` |
| `WebSocket connection failed` | URL galat — `ws://` hona chahiye, `http://` nahi |
| Message do baar aa raha hai | Hook me cleanup missing (StrictMode do sockets khol deta hai) |
| Update aa raha hai par purana status | `db.refresh(seat)` missing hai `broadcast_seat_update` me |
| Ek tab me change, dusre me nahi | `docker compose exec redis redis-cli psubscribe "seatpulse:event:*"` — message ja raha hai? |
| Backend restart ke baad reconnect nahi | Browser console dekho, backoff 15s tak ja sakta hai — thoda ruko |
| `RuntimeError: Event loop is closed` shutdown pe | `task.cancel()` lifespan me hai? |

---

## Files jo is phase me bane/badle

```
backend/
├── websocket.py            ← naya  ⭐ ConnectionManager + Redis pub/sub
├── events_broadcast.py     ← naya  (routers ke liye simple helper)
├── main.py                 ← update (lifespan + /ws endpoint)
└── routers/
    ├── seats.py            ← update (lock/unlock/expired pe broadcast)
    └── bookings.py         ← update (book/cancel pe broadcast)

frontend/src/
├── hooks/
│   └── useWebSocket.js     ← naya  ⭐ reconnect ke saath
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
- [roadmap.md](../roadmap.md) — aage kya

---

**Agla:** Phase 6 — Load testing (Locust). 500 concurrent users, ek seat, aur proof ki exactly 1 booking hui. Wahi number resume pe jayega.
