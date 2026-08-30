# Phase 6 — Load Testing + Proof

[Phase 5 — WebSockets](05-websockets.md) ke baad ka aakhri phase.

> **Bina iske Phase 2-4 sirf daawa hai.** Ye phase unhe **number** me badalta hai — aur wahi number resume pe jata hai.

---

## Do alag cheezein measure karni hain

| Kya | Kis se | Sawaal |
|---|---|---|
| **Correctness** | `verify_integrity.py` | Overselling hui ya nahi? |
| **Performance** | Locust | Kitni tez? |

**Correctness zyada important hai.** Locust "0 failures" dikha sakta hai aur phir bhi database me do bookings ho sakti hain — kyunki dono requests ko `201` mila hoga. Isliye load test ke baad **hamesha** DB check karo.

---

## Step 1 — Locust setup

```
loadtest/
├── Dockerfile
├── requirements.txt      (locust)
└── locustfile.py
```

**Compose me profile ke saath:**
```yaml
  locust:
    build: ./loadtest
    profiles: ["loadtest"]      # normal `up` pe start NAHI hoga
    volumes:
      - ./loadtest:/loadtest
    ports:
      - "8089:8089"
    environment:
      LOCUST_HOST: http://backend:8000
    depends_on:
      - backend
```

> `profiles: ["loadtest"]` — roz ke `docker compose up` me ye container start nahi hoga. Sirf `--profile loadtest` dene par chalega.

### ⚠️ Dockerfile me ENTRYPOINT chahiye, sirf CMD nahi

```dockerfile
ENTRYPOINT ["locust"]
CMD ["-f", "locustfile.py"]
```

**Kyu:** `docker compose run --rm locust -f locustfile.py --headless ...` ke arguments **CMD ko poora replace** kar dete hain. Sirf CMD hota to Docker `-f` ko hi executable samajhta:

```
exec: "-f": executable file not found in $PATH
```

ENTRYPOINT fix rehta hai, arguments uske aage judte hain.

---

## Step 2 — `locustfile.py`

Poora code: [../loadtest/locustfile.py](../../loadtest/locustfile.py)

### Do scenarios

| Class | Kya karta hai | Kya prove karta hai |
|---|---|---|
| `FlashSaleUser` | Sab **ek hi seat** ke peeche | Overselling nahi hoti |
| `BrowsingUser` | Grid dekhna + random booking | Asli response times |

### ⚠️ Har user ka alag `user_id` — ye zaroori hai

```python
_user_ids = itertools.cycle(range(1, USER_POOL_SIZE + 1))

def on_start(self):
    self.user_id = next(_user_ids)
```

Agar do Locust users same `user_id` bhejein, to dusre ko **`already_owned` wala 200** mil jayega — kyunki lock uske paas pehle se hai. Success count phool jayega aur test jhootha ho jayega.

Isliye `seed.py` ab **500 users** banata hai:
```python
SEED_USERS = int(os.getenv("SEED_USERS", "500"))
```

### 409 ko success maana hai

```python
elif res.status_code == 409:
    res.success()      # ye FAILURE nahi hai — yahi sahi jawab hai
```

Flash sale me 499 logon ko 409 milna **expected** hai. Failure mark karte to Locust "99% failure rate" dikhata — dekhne wale ko lagta app toota hua hai, jabki wo bilkul sahi kaam kar raha hai.

Asli failure sirf `500` ya timeout hai.

---

## Step 3 — `verify_integrity.py`

Poora code: [../backend/verify_integrity.py](../../backend/verify_integrity.py)

**Chaar checks:**

| # | Check | Kya pakadta hai |
|---|---|---|
| 1 | Koi seat 2+ confirmed bookings ke saath? | **Overselling** |
| 2 | `booked` seats == confirmed bookings | State mismatch |
| 3 | Koi `booked` seat bina booking ke? | Orphan seat |
| 4 | Koi confirmed booking bina `booked` seat ke? | Orphan booking |

Check #1 sabse important hai:
```sql
SELECT seat_id, count(*) FROM bookings
WHERE status = 'confirmed'
GROUP BY seat_id HAVING count(*) > 1
```
Ek bhi row aa gayi = overselling hui.

---

## Step 4 — Test chalao

```bash
# 1. Users seed karo (ek baar)
docker compose exec backend python seed.py

# 2. Clean state
docker compose exec backend python reset_state.py

# 3. Flash sale — 500 users, ek seat
docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 500 -r 100 -t 30s \
    --host http://backend:8000

# 4. ⭐ Verify
docker compose exec backend python verify_integrity.py
```

**Flags:**

| Flag | Matlab |
|---|---|
| `-u 500` | 500 concurrent users |
| `-r 100` | 100 users/second ki speed se badhao |
| `-t 30s` | 30 second chalao |
| `--headless` | Web UI ke bina, terminal me |

**Web UI chahiye** (graphs ke saath, screenshot ke liye achha):
```bash
docker compose --profile loadtest up locust
```
Phir http://localhost:8089

---

## ⭐ Load test ne ek asli bug pakda

Ye is phase ki sabse badi seekh hai — **isliye load testing sirf "number nikalne" ke liye nahi hoti.**

Pehla run:

```
✅ Koi seat do baar nahi biki
❌ Seat status aur bookings match karte hain  — 0 booked seats, 1 confirmed bookings
❌ Koi booking bina booked seat ke nahi       — 1 mismatched bookings
   Seats:    locked=1, available=99
   Bookings: confirmed=1
```

Seat `locked` thi, par uski **confirmed booking bhi thi**. Overselling nahi hui, par state galat thi.

### Race kya thi

```
User B: seat padhi (status = locked by A)  -> upar wala check pass ho gaya
User A: book kar li -> status = booked, Redis lock release
User B: Redis lock mil gaya (ab free tha)
User B: DB me likh diya -> status = locked     ← 'booked' overwrite ho gaya
```

`lock_seat` ka DB update bina guard ke tha:
```python
update(Seat).where(Seat.id == seat_id).values(status=SEAT_LOCKED, ...)
```
Seat ki current haalat check kiye bina blindly `locked` likh raha tha.

### Fix — wahi optimistic locking pattern

```python
result = db.execute(
    update(Seat)
    .where(
        Seat.id == seat_id,
        Seat.status.in_((SEAT_AVAILABLE, SEAT_LOCKED)),   # <- guard
    )
    .values(status=SEAT_LOCKED, ...)
)

if result.rowcount == 0:
    db.rollback()
    release_seat_lock(seat_id, payload.user_id)    # apna Redis lock wapas do
    raise HTTPException(409, "Seat abhi abhi book ho gayi")
```

> **Seekhne wali baat:** 20-request test (Phase 3) me ye bug **nahi pakda gaya tha**. 500 users pe hi wo timing window khuli. Isiliye load testing zaroori hai — sirf metrics ke liye nahi, **bugs ke liye**.
>
> Interview me ye bataने layak cheez hai: "load test ne ek race pakdi jo chhote test me nahi dikhti thi, aur maine wahi guarded-update pattern lagakar theek ki."

---

## ✅ Results (actual, is machine pe)

### Test A — Flash sale: 500 users, ek seat

```
Total requests   : 4446
Failures         : 0
Requests/sec     : 150.0
Median (p50)     : 2400 ms
p95              : 5700 ms
p99              : 11000 ms
```

```
==============================================================
INTEGRITY CHECK
==============================================================
  ✅ Koi seat do baar nahi biki
  ✅ Seat status aur bookings match karte hain  — 1 booked seats, 1 confirmed bookings
  ✅ Koi booked seat bina booking ke nahi  — 0 orphan seats
  ✅ Koi booking bina booked seat ke nahi  — 0 mismatched bookings
--------------------------------------------------------------
  Seats:    available=99, booked=1
  Bookings: confirmed=1
==============================================================
  ✅ SAB PASS — koi overselling nahi hui
==============================================================
```

**4446 requests, 500 concurrent users, ek seat → exactly 1 booking.** Yahi asli proof hai.

### Test B — Realistic browsing: 50 users

```
Endpoint                    p50     p75     p95     p99
GET  /events/{id}/seats     13ms    19ms    93ms    180ms
GET  /events/{id}           10ms    13ms    74ms    160ms
POST /seats/{id}/lock       19ms    23ms    46ms     83ms
POST /bookings              26ms    33ms    42ms     47ms
--------------------------------------------------------
Aggregated                  13ms    21ms    85ms    160ms
```

---

## ⚠️ Resume pe ye numbers kaise likhne hain

**Ye mat likhna:** "sub-50ms API response times" — bina context ke.

Kyunki 500 users pe p50 **2400ms** hai. Interviewer ne poocha "kis load pe?" aur jawab na diya to poori credibility jati hai.

**Ye likhna:**

> Sustained **p50 13ms / p95 85ms** at 50 concurrent users; verified **zero overselling** across 4,400+ requests from 500 concurrent users contending for a single seat.

Ye sach hai, specific hai, aur exactly wahi hai jo maapa gaya.

### 500 users pe itna slow kyu?

Honest wajah — aur ye samajhna zaroori hai:

| Wajah | Production me kya hota |
|---|---|
| **Ek hi uvicorn worker** | `--workers 4` ya gunicorn ke peeche multiple workers |
| **`--reload` on** | Dev flag hai, har request pe overhead. Production me off |
| **Sab ek hi seat pe** | Asli traffic 100 seats pe faila hota hai — ye jaan-boojh ke worst case hai |
| **Sab ek laptop pe** | DB, Redis, backend, aur load generator — sab same machine, same CPU |

Ye numbers **worst case** hain. Aur wahi baat interview me kehni hai — pata hona chahiye ki number kis wajah se aisa hai.

---

## Step 5 — Automated concurrency tests

Poora code: [../backend/tests/test_concurrency.py](../../backend/tests/test_concurrency.py)

```bash
docker compose exec backend pytest tests/ -v
```

```
tests/test_concurrency.py::test_health                          PASSED
tests/test_concurrency.py::test_only_one_user_gets_the_lock     PASSED
tests/test_concurrency.py::test_no_double_booking               PASSED
tests/test_concurrency.py::test_lock_blocks_other_users_booking PASSED
tests/test_concurrency.py::test_cannot_release_someone_elses_lock PASSED
tests/test_concurrency.py::test_version_increments_on_change    PASSED

============================== 6 passed in 2.92s ===============================
```

**Ye asli HTTP requests bhejte hain, mock nahi.** Race conditions sirf tab dikhti hain jab poora stack (uvicorn + Redis + Postgres) saath chal raha ho. Mock kar dete to wo bug pakda hi na jata jo load test ne pakda.

```python
with ThreadPoolExecutor(max_workers=40) as pool:
    codes = list(pool.map(try_book, range(1, 41)))

assert codes.count(201) == 1
```

Locust manual hai, ye har baar chal sakte hain — CI me bhi.

Dev dependencies alag file me hain (`requirements-dev.txt`) taki production image me pytest na jaye.

---

## Step 6 — `reset_state.py`

Testing ke beech me bar-bar chahiye:

```bash
docker compose exec backend python reset_state.py
```
```
✅ 3 bookings hataye, 100 seats available ki
✅ 0 Redis locks saaf kiye
```

> Redis me `flushall` nahi, sirf `scan_iter("seat:*:lock")` — apni keys hi delete karta hai. Aage Redis me aur cheezein aayengi (rate limiting waqerah), tab `flushall` unhe bhi uda deta.

---

## Common Problems

| Problem | Fix |
|---|---|
| `exec: "-f": executable file not found` | Dockerfile me `ENTRYPOINT ["locust"]` chahiye |
| Load test me sab `404 User nahi mila` | Users seed nahi hue — `docker compose exec backend python seed.py` |
| Sab requests ko 200 mil raha (contention hi nahi) | Same `user_id` reuse ho raha. `USER_POOL_SIZE` check karo |
| `Connection refused` locust se | `--host http://backend:8000` (container network), `localhost` nahi |
| Response time bahut kharab | Normal hai — dev mode, ek worker, sab ek machine pe |
| `verify_integrity.py` fail | **Achhi baat hai** — asli bug mila. Race dhoondho |
| Tests fail "koi available seat nahi" | `docker compose exec backend python reset_state.py` |
| Locust web UI nahi khul raha | `docker compose --profile loadtest up locust` phir http://localhost:8089 |

---

## Files jo is phase me bane/badle

```
loadtest/                       ← naya folder
├── Dockerfile                  (ENTRYPOINT zaroori)
├── requirements.txt
└── locustfile.py               ⭐ do scenarios

backend/
├── verify_integrity.py         ← naya  ⭐ asli proof
├── reset_state.py              ← naya
├── requirements-dev.txt        ← naya
├── Dockerfile                  ← update (dev deps)
├── seed.py                     ← update (500 users)
├── tests/
│   └── test_concurrency.py     ← naya  ⭐ 6 tests
└── routers/
    └── seats.py                ← update  ⭐ RACE FIX (guarded update)

docker-compose.yml              ← update (locust service, profile ke saath)
```

---

## Commit

```bash
git add .
git commit -m "Phase 6: Locust load tests, integrity verification, concurrency test suite

- Fix race where lock_seat could overwrite a booked seat's status
- 500 concurrent users on one seat: exactly 1 booking, 0 integrity violations"
git push
```

---

## Related

- [Phase 4 — Redis Locking](04-redis-locking.md) — locking ka design
- [postgres-commands.md](../reference/postgres-commands.md) — DB queries
- [roadmap.md](../roadmap.md) — poora plan

---

**Roadmap complete.** Aage optional: JWT auth, rate limiting, multi-worker deploy (`--workers 4`), CI pipeline.
