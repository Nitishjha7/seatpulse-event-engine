# Phase 4 — Redis Distributed Seat Locking

[Phase 3 — API + Seat Grid](03-api-and-seat-grid.md) follow-up.

**Objective:** Implement the "select → 5-minute hold → pay" flow with a live countdown.

> ⭐ **This phase is critical for interviews.** Understand the logic thoroughly; do not just copy-paste.

---

## Recap of Phase 3 (and why it was sufficient)

At the end of Phase 3, the compose setup had **3 services** (db, backend, frontend), and `requirements.txt` did not include `redis`.

Concurrency protection was handled entirely at the database level:
- `version` column (optimistic locking)
- Partial unique index

This **worked correctly**—the 20-parallel-request test passed, ensuring exactly one booking per seat in the DB.

**Why add Redis?**

| | Phase 3 (DB only) | Phase 4 (Redis + DB) |
|---|---|---|
| Flow | Select seat → book immediately | Select seat → **5-min hold** → pay/confirm |
| Load | Every request hits the DB | 4999 out of 5000 requests are blocked by Redis |
| Layers | 2 (version + constraint) | 3 — Redis acts as a **top layer**; DB layers remain |
| Abandoned cart | Not supported | TTL automatically releases the lock |

> **Key takeaway:** Redis does not change correctness—that was already achieved. Redis provides **speed** and the "hold" functionality. Interviewers test this distinction.

---

## Concept — Core Locking Logic

```python
ok = r.set(f"seat:{seat_id}:lock", user_id, nx=True, ex=300)
```

| Flag | Purpose |
|---|---|
| `nx=True` | Set only if the key **does not exist**. This is an **atomic operation**—the check and set occur simultaneously, preventing race conditions. |
| `ex=300` | Auto-release after 5 minutes. If the user abandons the cart, the seat becomes available automatically—**no cleanup job required**. |

If `ok` is False, the seat is held by someone else → **409 Conflict**.

### Release via Lua script, not `DEL`

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

**Risk of using `DEL` directly:**

```
1. User A's lock expires after 5 minutes.
2. User B immediately acquires the lock.
3. User A's "release" request arrives and executes DEL.
   -> User B's lock is deleted, even though User B did nothing wrong.
```

Therefore, verify ownership before deleting. Writing this in Python as two steps (`GET` then `DEL`) would introduce a race condition. **Lua scripts execute atomically within Redis**—no other operation can intervene.

---

## Step 1 — Add Redis to Compose

```yaml
  redis:
    image: redis:7-alpine
    container_name: seatpulse_redis
    ports:
      - "${REDIS_PORT}:6379"
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 5s
      timeout: 3s
      retries: 5

  backend:
    environment:
      DATABASE_URL: postgresql+psycopg2://...
      REDIS_URL: redis://redis:6379/0
    depends_on:
      db:
        condition: service_healthy
      redis:
        condition: service_healthy
```

In root `.env`:
```
REDIS_PORT=6379
```

> ⚠️ If another Redis instance is running on 6379, set `REDIS_PORT=6380`. The backend remains unaffected as it uses `redis:6379` within the container network.

### Why no volume for Redis?

**By design.** Redis only stores temporary seat locks (5-minute TTL). If they are lost on restart, it is acceptable—seats will simply revert to available in the DB.

**Financial and booking data reside in Postgres.** Redis is never the source of truth. This is a deliberate design decision, not an oversight—state this clearly in interviews.

---

## Step 2 — `redis_client.py`

Full code: [../backend/redis_client.py](../../backend/redis_client.py)

```python
redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,      # str instead of bytes
    socket_connect_timeout=3,
    socket_timeout=3,
)
```

| Setting | Reason |
|---|---|
| `decode_responses=True` | Avoids manual `b"123".decode()` calls. |
| `socket_timeout=3` | Prevents requests from hanging indefinitely if Redis is unresponsive. |

**Functions:**

| Function | Purpose |
|---|---|
| `acquire_seat_lock(seat_id, user_id)` | `SET ... NX EX` — Returns True/False |
| `release_seat_lock(seat_id, user_id)` | Safe release via Lua script |
| `get_lock_owner(seat_id)` | Check current owner |
| `get_lock_ttl(seat_id)` | Remaining seconds |
| `ping()` | Health check |

**Key naming:** `seat:42:lock` — Namespacing keeps Redis organized and allows `KEYS seat:*` to list all locks.

---

## Step 3 — Lock Endpoints

| Method | Route | Purpose |
|---|---|---|
| POST | `/api/seats/{id}/lock` | Hold seat (409 if held by another) |
| DELETE | `/api/seats/{id}/lock?user_id=` | Release hold |
| GET | `/api/seats/{id}/lock` | Check owner + TTL (debugging) |

**After acquiring the lock, update the DB:**
```python
update(Seat).values(
    status=SEAT_LOCKED,
    locked_by=user_id,
    locked_until=utcnow() + timedelta(seconds=ttl),
    version=Seat.version + 1,
)
```

**Why, if the lock is in Redis?** To show the seat as yellow to **other users** in the grid. The actual lock is in Redis; the DB holds a copy for display purposes.

### ⚠️ Expired locks (and the solution)

Redis deletes keys silently upon TTL expiry—it does not notify Postgres. Consequently, the DB might show a seat as `locked` when it is actually free.

**Solution — Lazy cleanup.** Perform a lightweight UPDATE before reading seats:

```python
def release_expired_locks(db, event_id):
    db.execute(
        update(Seat)
        .where(
            Seat.event_id == event_id,
            Seat.status == SEAT_LOCKED,
            Seat.locked_until < utcnow(),
        )
        .values(status=SEAT_AVAILABLE, locked_by=None, locked_until=None,
                version=Seat.version + 1)
    )
    db.commit()
```

No background job or cron is needed; the cleanup occurs whenever a user views the grid.

---

## Step 4 — Booking Lock Check

`POST /api/bookings` now passes through three layers:

```python
# LAYER 1 — Redis
lock_owner = get_lock_owner(seat_id)
if lock_owner is None:
    if not acquire_seat_lock(seat_id, user_id):
        raise HTTPException(409, "Seat already held by someone else")
    lock_taken_here = True
elif lock_owner != user_id:
    raise HTTPException(409, "Seat held by someone else")

# LAYER 2 — Optimistic locking (Phase 3, unchanged)
result = db.execute(update(Seat).where(..., Seat.version == expected_version, ...))
if result.rowcount == 0:
    raise HTTPException(409, ...)

# LAYER 3 — DB constraint (Phase 2, unchanged)
try:
    db.commit()
except IntegrityError:
    raise HTTPException(409, ...)

# Booking successful — release lock
release_seat_lock(seat_id, user_id)
```

**Handles two scenarios:**
- **a)** User selected via UI → lock already exists.
- **b)** Direct API call → acquire lock here.

Both paths require the Redis lock to proceed.

> **Layers 2 and 3 remain unchanged.** This is crucial—Redis acts as a filter sitting on top, not a replacement.

**Always release the lock on error paths** (`lock_taken_here` flag) to prevent unnecessary 5-minute blocks.

---

## Step 5 — Frontend: Hold + Countdown

**Seat click is now a server call:**
```js
async function handleSelect(seat) {
  if (selectedSeat) await unlockSeat(selectedSeat.id, user.id)  // Release old
  const lock = await lockSeat(seat.id, user.id)                 // Acquire new
  setSelectedSeat(seat)
  setLockSecondsLeft(lock.expires_in)                           // Start countdown
}
```

In Phase 3, this only updated local state. Now, it can return **409** if someone else grabbed the seat.

**Countdown:**
```js
useEffect(() => {
  if (lockSecondsLeft <= 0) return
  const id = setInterval(() => {
    setLockSecondsLeft((s) => {
      if (s <= 1) { setSelectedSeat(null); refresh(...); return 0 }
      return s - 1
    })
  }, 1000)
  return () => clearInterval(id)
}, [lockSecondsLeft, ...])
```

> ⚠️ **The timer is for display only.** The actual expiry happens in **Redis**. If the browser crashes or the laptop shuts down, the seat will still be freed in 5 minutes. The timer only informs the user.
>
> Interview question: *"What if the user closes the browser?"* — Answer: TTL. Nothing depends on the client.

**Release lock on tab close** (to avoid waiting for TTL):
```js
window.addEventListener('beforeunload', () => {
  fetch(`${API_URL}/api/seats/${seat.id}/lock?user_id=${u.id}`, {
    method: 'DELETE',
    keepalive: true,     // Ensures request completes even if page closes
  })
})
```
This is an **optimization**. If it fails, TTL handles it.

**Grid colors:**

| Color | Meaning |
|---|---|
| 🟢 Green | Available |
| 🔵 Blue | **My** hold |
| 🟡 Yellow | **Someone else's** hold |
| 🔴 Red | Booked |

Decided by `seat.locked_by === currentUserId` — requires adding `locked_by` to the `SeatOut` schema.

---

## Step 6 — Rebuild

`requirements.txt` has changed:

```bash
docker compose up -d --build backend redis
```

---

## ✅ Proof

### 1. Health Check
http://localhost:8000/api/health
```json
{ "status": "healthy", "database": "connected", "redis": "connected", "version": "0.4.0" }
```

### 2. ⭐ Lock contention — 6 users, 1 seat

```bash
for u in 1 2 3 4 5 6; do
  curl -s -o /dev/null -w "user$u -> %{http_code}\n" \
    -X POST http://localhost:8000/api/seats/10/lock \
    -H "Content-Type: application/json" -d "{\"user_id\":$u}" &
done; wait
```

**Actual output:**
```
user2 -> 409
user3 -> 409
user1 -> 409
user4 -> 409
user5 -> 200      <- winner
user6 -> 409
```

```bash
docker compose exec redis redis-cli get "seat:10:lock"     # -> 5
```

### 3. Lock ownership enforcement

```bash
# user1 attempts to book, but user5 holds the lock
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":10,"user_id":1}'
# -> {"detail":"Seat held by someone else"}
```

### 4. ⭐ Lua script prevents unauthorized release

```bash
curl -X DELETE "http://localhost:8000/api/seats/10/lock?user_id=1"
# -> {"released": false}          <- user1 did not hold the lock

docker compose exec redis redis-cli get "seat:10:lock"
# -> 5                            <- user5's lock remains
```

**This proves the Lua script's effectiveness.** A direct `DEL` would have deleted user5's lock.

### 5. TTL expiry

```bash
docker compose exec redis redis-cli set "seat:99:lock" "3" EX 5
docker compose exec redis redis-cli ttl "seat:99:lock"      # -> 4
sleep 6
docker compose exec redis redis-cli exists "seat:99:lock"   # -> 0
```

### 6. Browser behavior
- Click green seat → turns **blue** + **5:00 countdown** starts in right panel.
- Click another seat → first turns green, new one turns blue (old lock released).
- **Release Hold** → reverts to green.
- **Confirm Booking** → turns red, lock cleared.
- Countdown < 1:00 → turns red.

**Two-browser test** (normal + incognito): Requires refresh for now — **live updates arrive in Phase 5**.

### 7. Inspecting Redis
```bash
docker compose exec redis redis-cli
> KEYS seat:*
> GET seat:10:lock
> TTL seat:10:lock
> MONITOR          # Watch live commands, then click a seat in the UI
```

Running `MONITOR` while clicking a seat in the UI will show `SET seat:12:lock 1 EX 300 NX` in real-time.

### 8. Reset
```bash
docker compose exec redis redis-cli flushall
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "DELETE FROM bookings; UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL, version=0;"
```

---

## Interview Questions

| Question | Answer |
|---|---|
| "Why not just use Redis?" | Redis locks are lost on restart. DB constraints ensure data integrity during those windows. |
| "Why not just use the DB?" | It works (Phase 3), but every request hits the DB. Redis rejects 99% of requests before they reach the DB. |
| "What if the user closes the browser?" | TTL. Nothing depends on the client; the lock expires in 5 minutes. |
| "Why Lua for release?" | GET and DEL as separate steps allow a race condition where a lock expires and is re-acquired by someone else before you delete it. Lua is atomic. |
| "Why no persistence in Redis?" | It only stores temporary locks. Financial data is in Postgres. Redis is not the source of truth. |
| "What if two backend servers run?" | Redis is shared, making it a "distributed" lock. DB constraints act as the final safety net. |

---

## Common Problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'redis'` | `docker compose up -d --build backend` |
| `Error 111 connecting to redis:6379` | Check if Redis is running: `docker compose ps` (should be `healthy`). |
| `port is already allocated` (6379) | Set `REDIS_PORT=6380` in root `.env`. |
| Seat stuck yellow, no one using it | Redis key expired, but DB still shows `locked`. Refresh grid — `release_expired_locks` will clear it. |
| 404 "User not found" on lock | Seed test users: `docker compose exec backend python seed.py` |
| Countdown running but seat booked | Two tabs open; one booked. **Phase 5 (WebSockets) solves this.** |
| Locks jammed after testing | `docker compose exec redis redis-cli flushall` |

---

## Files Modified/Created

```
docker-compose.yml              ← update (redis service)
.env / .env.example             ← update (REDIS_PORT)

backend/
├── redis_client.py             ← new  ⭐ lock logic + Lua script
├── config.py                   ← update (REDIS_URL, SEAT_LOCK_TTL)
├── requirements.txt            ← update (redis)
├── schemas.py                  ← update (SeatLockRequest/Out, locked_by)
├── seed.py                     ← update (5 test users)
├── main.py                     ← update (health check)
└── routers/
    ├── seats.py                ← update  ⭐ lock/unlock endpoints
    └── bookings.py             ← update (layer 1 add)

frontend/src/
├── api.js                      ← update (lock/unlock)
├── App.jsx                     ← update (lock flow + countdown)
└── components/
    ├── SeatGrid.jsx            ← update (4 colors, "my hold")
    └── BookingPanel.jsx        ← update (countdown, Release Hold)
```

---

## Commit

```bash
git add .
git commit -m "Phase 4: Redis distributed seat locking with TTL and Lua-based safe release"
git push
```

---

## Related

- [postgres-commands.md](../reference/postgres-commands.md) — DB queries and reset
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — next steps

---

**Next:** Phase 5 — WebSockets. Currently, users must refresh to see seat status changes; Phase 5 enables **instant** updates.
