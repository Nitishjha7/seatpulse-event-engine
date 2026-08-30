# Phase 4 — Redis Distributed Seat Locking

[Phase 3 — API + Seat Grid](03-api-and-seat-grid.md) ke baad ka kaam.

**Kya banega:** "select karo → 5 min ke liye hold → phir pay karo" wala asli flow, live countdown ke saath.

> ⭐ **Interview me sabse zyada sawaal isi phase par aayenge.** Ye samajh ke likhna, copy-paste mat karna.

---

## Phase 3 tak kya tha (aur wo galat nahi tha)

Phase 3 ke end me compose me sirf **3 services** thi (db, backend, frontend), aur `requirements.txt` me `redis` package tha hi nahi.

Concurrency protection **poori database-level** thi:
- `version` column (optimistic locking)
- partial unique index

Aur wo **sahi kaam kar rahi thi** — 20-parallel-request test pass ho raha tha, DB me exactly 1 booking aati thi.

**To phir Redis kyu?**

| | Phase 3 (sirf DB) | Phase 4 (Redis + DB) |
|---|---|---|
| Flow | Seat select karo → seedha book | Select karo → **5 min hold** → phir pay/confirm |
| Load | Har request DB tak jaati hai | 5000 me se 4999 Redis pe hi ruk jaati hain |
| Layers | 2 (version + constraint) | 3 — Redis **upar** aata hai, DB layers waise hi rehti hain |
| Abandoned cart | Concept hi nahi tha | TTL khud release kar deta hai |

> **Ye line yaad rakhna:** Redis ne correctness nahi badli — wo **pehle se hi sahi thi**. Redis ne sirf **speed** di aur "hold" ka concept diya. Interviewer isi farak ko test karta hai.

---

## Concept — lock lene ka core

```python
ok = r.set(f"seat:{seat_id}:lock", user_id, nx=True, ex=300)
```

| Flag | Kaam |
|---|---|
| `nx=True` | Sirf tab set karo jab key **exist na kare**. **Ye ek atomic operation hai** — check aur set alag-alag steps nahi hain, isliye do log ek saath lock nahi le sakte |
| `ex=300` | 5 min me apne aap release. User cart chhod ke chala gaya? Seat khud wapas available — **koi cleanup job nahi chahiye** |

`ok` False mila → seat kisi aur ke paas hai → **409 Conflict**

### Release Lua script se, seedha `DEL` nahi

```lua
if redis.call("get", KEYS[1]) == ARGV[1] then
    return redis.call("del", KEYS[1])
else
    return 0
end
```

**Seedha `DEL` karne me kya risk hai:**

```
1. User A ka lock hai, wo 5 min me expire ho gaya
2. User B ne turant lock le liya
3. User A ka "release" request ab aata hai aur DEL kar deta hai
   -> B ka lock uda diya, jabki B ne kuch galat nahi kiya
```

Isliye pehle check karo "lock mera hi hai?", **tabhi** delete karo.

Python me do steps (`GET` phir `DEL`) likhte to unke beech me bhi wahi race reh jati. **Lua script Redis ke andar atomic chalti hai** — beech me kuch nahi ghus sakta.

---

## Step 1 — Compose me Redis add karo

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

Root `.env` me:
```
REDIS_PORT=6379
```

> ⚠️ Koi aur Redis already 6379 pe chal raha ho (dusra project) to `REDIS_PORT=6380` kar do. Backend pe koi asar nahi — wo `redis:6379` use karta hai, container network ke andar.

### Redis ka volume kyu nahi hai?

**Jaan-boojh ke.** Redis me sirf temporary seat locks hain (5 min TTL wale). Restart pe wo chale bhi jayein to koi nuksan nahi — seats DB me available ho jaayengi.

**Paisa aur booking ka data hamesha Postgres me hai.** Redis kabhi source of truth nahi hai. Ye design decision hai, laparwahi nahi — aur interview me poocha jaye to yahi jawab hai.

---

## Step 2 — `redis_client.py`

Poora code: [../backend/redis_client.py](../../backend/redis_client.py)

```python
redis_client = redis.Redis.from_url(
    settings.REDIS_URL,
    decode_responses=True,      # bytes ki jagah str
    socket_connect_timeout=3,
    socket_timeout=3,
)
```

| Setting | Kyu |
|---|---|
| `decode_responses=True` | Bina iske har jagah `b"123".decode()` likhna padta |
| `socket_timeout=3` | Redis hang ho jaye to request hamesha ke liye atki na rahe |

**Functions:**

| Function | Kaam |
|---|---|
| `acquire_seat_lock(seat_id, user_id)` | `SET ... NX EX` — True/False |
| `release_seat_lock(seat_id, user_id)` | Lua script se safe release |
| `get_lock_owner(seat_id)` | Kiske paas hai |
| `get_lock_ttl(seat_id)` | Kitne second bache |
| `ping()` | Health check |

**Key ka naam:** `seat:42:lock` — namespace rakhne se Redis me cheezein saaf rehti hain aur `KEYS seat:*` se sab dikh jaate hain.

---

## Step 3 — Lock endpoints

| Method | Route | Kaam |
|---|---|---|
| POST | `/api/seats/{id}/lock` | Seat hold karo (409 = kisi aur ke paas) |
| DELETE | `/api/seats/{id}/lock?user_id=` | Apna hold chhodo |
| GET | `/api/seats/{id}/lock` | Kiske paas hai + TTL (debugging) |

**Lock lene ke baad DB me bhi likhte hain:**
```python
update(Seat).values(
    status=SEAT_LOCKED,
    locked_by=user_id,
    locked_until=utcnow() + timedelta(seconds=ttl),
    version=Seat.version + 1,
)
```

**Kyu, jab lock Redis me hai hi?** Taki **dusre users** ko grid me wo seat peeli dikhe. Asli lock Redis me hi hai — DB me sirf "dikhane" ke liye copy hai.

### ⚠️ Expired locks ka problem (aur uska hal)

Redis key TTL par **chupchap** delete ho jaati hai — wo Postgres ko batane nahi aati. To DB me seat `locked` padi reh jati hai jabki asal me free ho chuki hai.

**Hal — lazy cleanup.** Seats padhne se pehle ek sasta UPDATE:

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

Background job ya cron ki zaroorat nahi — jab koi grid dekhega, tab saaf ho jayega.

---

## Step 4 — Booking me lock check

Ab `POST /api/bookings` teeno layers se guzarta hai:

```python
# LAYER 1 — Redis
lock_owner = get_lock_owner(seat_id)
if lock_owner is None:
    # Koi seedha book kar raha hai (UI flow ke bina) — yahin lock le lo
    if not acquire_seat_lock(seat_id, user_id):
        raise HTTPException(409, "Seat abhi kisi aur ne hold kar li")
    lock_taken_here = True
elif lock_owner != user_id:
    raise HTTPException(409, "Ye seat kisi aur ke paas hold hai")

# LAYER 2 — optimistic locking (Phase 3 wala, unchanged)
result = db.execute(update(Seat).where(..., Seat.version == expected_version, ...))
if result.rowcount == 0:
    raise HTTPException(409, ...)

# LAYER 3 — DB constraint (Phase 2 wala, unchanged)
try:
    db.commit()
except IntegrityError:
    raise HTTPException(409, ...)

# Booking ho gayi — lock ki ab zaroorat nahi
release_seat_lock(seat_id, user_id)
```

**Do raaste handle kiye hain:**
- **a)** User ne UI se seat select ki thi → lock uske paas hai
- **b)** Koi seedha `POST /api/bookings` maar raha hai → yahin lock lete hain

Dono me booking Redis lock ke bina aage nahi badhti.

> **Layer 2 aur 3 ka code bilkul nahi badla.** Yahi baat sabse important hai — Redis ek filter hai jo unke upar baitha hai, unki jagah nahi liya.

**Har error path pe lock release karna zaroori hai** (`lock_taken_here` flag) — warna seat 5 min ke liye bekaar me atki rahegi.

---

## Step 5 — Frontend: hold + countdown

**Seat click ab server call hai:**
```js
async function handleSelect(seat) {
  if (selectedSeat) await unlockSeat(selectedSeat.id, user.id)  // purana chhodo
  const lock = await lockSeat(seat.id, user.id)                 // naya lo
  setSelectedSeat(seat)
  setLockSecondsLeft(lock.expires_in)                           // countdown shuru
}
```

Phase 3 me ye sirf local state set karta tha. Ab **409 mil sakta hai** — matlab koi aur pehle le gaya.

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

> ⚠️ **Ye timer sirf dikhane ke liye hai.** Asli expiry **Redis me** hoti hai. Browser band kar do, tab crash ho jaye, laptop band ho jaye — seat phir bhi 5 min me apne aap free hogi. Timer sirf user ko batata hai kitna time bacha hai.
>
> Ye distinction interview me poocha jata hai: *"agar user browser band kar de to?"* — jawab: TTL. Client par kuch bhi depend nahi karta.

**Tab band karte waqt lock chhodna** (TTL ka wait na karna pade):
```js
window.addEventListener('beforeunload', () => {
  fetch(`${API_URL}/api/seats/${seat.id}/lock?user_id=${u.id}`, {
    method: 'DELETE',
    keepalive: true,     // page band hote waqt bhi request nikal jati hai
  })
})
```
Ye sirf **optimization** hai. Na chale to TTL sambhal lega.

**Grid me ab 4 colors:**

| Color | Matlab |
|---|---|
| 🟢 Hara | Available |
| 🔵 Neela | **Meri** hold |
| 🟡 Peela | **Kisi aur** ki hold |
| 🔴 Laal | Booked |

`seat.locked_by === currentUserId` se decide hota hai — isliye `SeatOut` schema me `locked_by` add karna pada.

---

## Step 6 — Rebuild

`requirements.txt` badla hai:

```bash
docker compose up -d --build backend redis
```

---

## ✅ Proof

### 1. Health me Redis
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
user5 -> 200      <- sirf ek jeeta
user6 -> 409
```

```bash
docker compose exec redis redis-cli get "seat:10:lock"     # -> 5
```

### 3. Lock ownership enforce hoti hai

```bash
# user1 book kare, lock user5 ke paas hai
curl -X POST http://localhost:8000/api/bookings -H "Content-Type: application/json" -d '{"seat_id":10,"user_id":1}'
# -> {"detail":"Ye seat kisi aur ke paas hold hai"}
```

### 4. ⭐ Lua script dusre ka lock nahi hatata

```bash
curl -X DELETE "http://localhost:8000/api/seats/10/lock?user_id=1"
# -> {"released": false}          <- user1 ka lock tha hi nahi

docker compose exec redis redis-cli get "seat:10:lock"
# -> 5                            <- user5 ka lock salamat
```

**Ye Lua script ka asli proof hai.** Seedha `DEL` hota to user5 ka lock ud jata.

### 5. TTL apne aap expire hota hai

```bash
docker compose exec redis redis-cli set "seat:99:lock" "3" EX 5
docker compose exec redis redis-cli ttl "seat:99:lock"      # -> 4
sleep 6
docker compose exec redis redis-cli exists "seat:99:lock"   # -> 0
```

### 6. Browser me
- Hari seat click → turant **neeli** + right panel me **5:00 countdown** shuru
- Dusri seat click → pehli wapas hari, nayi neeli (purana lock chhut gaya)
- **Release Hold** → wapas hari
- **Confirm Booking** → laal, lock saaf
- Countdown 1:00 se neeche → laal ho jata hai

**Do browser me test** (ek normal, ek incognito): abhi dono ko refresh karna padega — **live update Phase 5 me aayega**.

### 7. Redis andar se dekho
```bash
docker compose exec redis redis-cli
> KEYS seat:*
> GET seat:10:lock
> TTL seat:10:lock
> MONITOR          # live commands dekho, phir dusre tab me seat click karo
```

`MONITOR` chala ke UI me seat click karna — `SET seat:12:lock 1 EX 300 NX` live dikhega. Demo ke liye badhiya hai.

### 8. Reset
```bash
docker compose exec redis redis-cli flushall
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "DELETE FROM bookings; UPDATE seats SET status='available', locked_by=NULL, locked_until=NULL, version=0;"
```

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Sirf Redis se kaam nahi chalta?" | Redis restart hone par saare locks chale jaate hain. Us window me overselling ho sakti thi. DB constraints hamesha rehti hain |
| "Sirf DB se kaam nahi chalta?" | Chalta hai — Phase 3 me chal raha tha. Par har request DB pe load daalti. Redis 99% ko pehle hi reject kar deta hai |
| "User browser band kar de to?" | TTL. Client par kuch depend nahi karta — 5 min me lock apne aap chhut jata hai |
| "Lock release me Lua kyu?" | GET aur DEL alag steps me karo to beech me lock expire ho ke kisi aur ko mil sakta hai, aur tum uska lock delete kar doge. Lua atomic hai |
| "Redis me data persist kyu nahi kiya?" | Usme sirf temporary locks hain. Paisa/booking Postgres me hai. Redis kabhi source of truth nahi |
| "Do backend server chalein to?" | Redis dono ke liye ek hi hai — isliye "distributed" lock hai. Aur DB constraint dono ke neeche hai |

---

## Common Problems

| Problem | Fix |
|---|---|
| `ModuleNotFoundError: No module named 'redis'` | `docker compose up -d --build backend` |
| `Error 111 connecting to redis:6379` | Redis chal raha hai? `docker compose ps` me `healthy` dekho |
| `port is already allocated` (6379) | Root `.env` me `REDIS_PORT=6380` |
| Seat peeli atki hai, koi use nahi kar raha | Redis key expire ho gayi par DB me `locked` pada hai. Grid refresh karo — `release_expired_locks` saaf kar dega |
| Lock lene par 404 "User nahi mila" | Test users nahi hain — `docker compose exec backend python seed.py` |
| Countdown chal raha hai par seat already booked | Do tab khule hain aur ek me book ho gaya. **Phase 5 (WebSocket) yahi solve karega** |
| Locks jam gaye testing ke baad | `docker compose exec redis redis-cli flushall` |

---

## Files jo is phase me bane/badle

```
docker-compose.yml              ← update (redis service)
.env / .env.example             ← update (REDIS_PORT)

backend/
├── redis_client.py             ← naya  ⭐ lock logic + Lua script
├── config.py                   ← update (REDIS_URL, SEAT_LOCK_TTL)
├── requirements.txt            ← update (redis)
├── schemas.py                  ← update (SeatLockRequest/Out, locked_by)
├── seed.py                     ← update (5 test users)
├── main.py                     ← update (health me redis)
└── routers/
    ├── seats.py                ← update  ⭐ lock/unlock endpoints
    └── bookings.py             ← update (layer 1 add)

frontend/src/
├── api.js                      ← update (lock/unlock)
├── App.jsx                     ← update (lock flow + countdown)
└── components/
    ├── SeatGrid.jsx            ← update (4 colors, "meri hold")
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

- [postgres-commands.md](../reference/postgres-commands.md) — DB queries aur reset
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — aage kya

---

**Agla:** Phase 5 — WebSockets. Abhi dusre user ko seat peeli dikhne ke liye refresh karna padta hai; Phase 5 me wo **turant** dikhega.
