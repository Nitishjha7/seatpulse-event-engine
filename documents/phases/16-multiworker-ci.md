# Phase 16 — Multi-Worker Deploy + CI

> Phase 5 me maine likha tha: "Redis pub/sub isliye use kiya kyunki
> multi-worker me in-memory dict share nahi hota."
>
> Wo baat aaj tak **kabhi test nahi hui thi** — dev me hamesha ek hi
> worker chala. Ye phase usi ko sach me chala ke dekhne ka hai.

---

## Do maqsad

1. **Production config** — 4 uvicorn workers, built frontend, non-root
   containers. Aur us config me sab kuch chalta hai ye sabit karna.
2. **CI** — har push par poora stack chale aur 66 tests chalein.

Dono ne mil ke **teen asli bugs** nikale, jo dev me mahino se chhupe hue
the. Wo hi is phase ka sabse kaam ka hissa hai.

---

## Multi-stage Dockerfiles

Ek hi Dockerfile me do targets — `dev` aur `prod`:

```
base ──┬── dev   (+ requirements-dev, --reload, root)
       └── prod  (non-root user, no dev tools, --workers N)
```

**Do alag files kyu nahi:** base layers (python version, requirements)
dheere-dheere alag ho jaati, aur wahin se "mere laptop pe to chalta tha"
paida hota hai. Ek file me dono targets hon to base share hota hai.

| Image | Dev | Prod |
|---|---|---|
| backend | 372 MB | 354 MB |
| frontend | 407 MB | **74 MB** |

Frontend ka farak bada hai kyunki prod me `node_modules` aur source hai hi
nahi — sirf built assets aur nginx.

📁 [`backend/Dockerfile`](../../backend/Dockerfile) · [`frontend/Dockerfile`](../../frontend/Dockerfile)

### Prod image me do cheezein jaan-boojh ke hain

```dockerfile
RUN useradd --create-home --uid 1000 appuser
USER appuser
```

Container escape ho bhi jaye to attacker ko root nahi milna chahiye. Ek
line, bada faayda.

Aur dev tools install hi nahi hote — pytest, locust prod image me nahi
hain. Kam size, kam attack surface.

> Ye sirf comment nahi hai, CI dono ko **check** karta hai (neeche).
> Aur ye hardening asli hai — beech me maine prod container me ek file
> likhne ki koshish ki aur `PermissionError` aaya. Wahi to chahiye tha.

---

## nginx + SPA fallback

```nginx
location / {
    try_files $uri $uri/ /index.html;
}
```

Ye line na ho to `/events/3` par **refresh karne se 404** aata hai.
Routing React Router ke paas hai, nginx ke paas nahi — nginx `events/3`
naam ki file dhoondhta hai, milti nahi.

Caching me bhi ek jaal hai:

| Path | Cache | Kyu |
|---|---|---|
| `/assets/*` | 1 saal, immutable | filename me content hash hai (`index-DTJXVmPu.js`). Content badla to naam badal jayega. |
| `/index.html` | **kabhi nahi** | Yahi file naye asset names batati hai. Isko cache kiya to user deploy ke baad purane assets maangta rahega — jo ab exist hi nahi karte. Nateeja: **blank page**. |

📁 [`frontend/nginx.conf`](../../frontend/nginx.conf)

---

## ⭐ Connection pool — multi-worker ki pehli chot

Ye wo galti hai jo single worker pe dikhti hi nahi:

```
Har uvicorn worker ek ALAG PROCESS hai — apna pool, apni memory.

Dev  (1 worker):   1 x (20 + 20) =  40 connections
Prod (4 workers):  4 x (20 + 20) = 160 connections
                                    ^^^ Postgres ka default max hai 100
```

Yaani jo pool config dev me sahi tha, wo 4 workers pe seedha
`FATAL: sorry, too many clients already` deta.

Isliye pool ab env se aata hai, hardcoded nahi:

```yaml
# docker-compose.prod.yml
DB_POOL_SIZE: 5
DB_MAX_OVERFLOW: 5
MAX_CONCURRENT_REQUESTS: 8    # invariant: < pool + overflow
```

`4 × (5 + 5) = 40` — 100 ki limit ke andar aaram se.

Admission control bhi **per-worker** hai, isliye wo bhi ghatana pada.
Invariant wahi rehta hai jo [Phase 7](07-auth-google-oauth.md) me tha:
`MAX_CONCURRENT_REQUESTS < pool_size + max_overflow`.

Measured, 4 workers ke saath: **5 / 100 connections**.

---

## ⭐⭐ Asli proof — broadcast process boundary paar karta hai?

Ye is phase ka dil hai. Phase 5 ka argument aaj tak sirf ek **umeed** tha.

Test:
1. 12 WebSocket clients connect karo — har connection OS kisi bhi worker
   ko de sakta hai, to ye alag processes me bant jaate hain
2. `/api/health` ke `worker_pid` se **sabit karo** ki sach me kai
   processes chal rahe hain (warna test kuch prove nahi karta)
3. EK seat book karo — wo booking kisi EK worker me hoti hai
4. Check karo sab 12 clients ko update mila

```
Jawab dene wale worker processes: 3  -> [9, 10, 12]
12 WebSocket clients connected
Seat A-1 booked (HTTP 201) — ek worker me

Broadcast mila: 12 / 12 clients

✅ PASS — 3 workers, sab 12 clients tak broadcast pahuncha
   Redis pub/sub sach me process boundary paar kar raha hai.
```

Agar broadcast in-memory dict se hota, to sirf usi worker ke clients ko
message milta jisme booking hui thi. Baaki 8-9 clients chup rehte, aur
unke grid me seat **hari dikhti rehti jabki wo bik chuki hai**.

📁 [`loadtest/verify_multiworker.py`](../../loadtest/verify_multiworker.py)

### Is proof ko likhte waqt ek galti hui

Pehla run:

```
Jawab dene wale worker processes: 1  -> [9]
```

4 workers chal rahe the, phir bhi. Wajah: maine ek hi `httpx` client se
40 health requests bheji thi — sabko wahi ek **keep-alive TCP connection**
mila, aur wo connection ek hi worker se juda tha.

**Worker distribution connection level pe hoti hai, request level pe
nahi.** Har probe ke liye naya client banate hi 3 alag PIDs dikhe.

---

## CI

📁 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)

Teen jobs:

| Job | Kya |
|---|---|
| `test` | Poora stack up → migrate → seed → 66 tests → integrity check |
| `build-prod` | Prod images build + **assertions** (dev tools nahi, root nahi) |
| `frontend` | `npm ci` + build |

### GitHub ka `services:` block use nahi kiya

Wo sirf DB/Redis containers deta hai, aur app ko runner par alag se
chalana padta — matlab CI wo cheez test karta jo deploy hoti hi nahi.

Yahan CI **wahi `docker compose`** chalata hai jo laptop pe chalti hai.
Compose file toot jaye to CI pakdega.

### Assertions, sirf build nahi

```yaml
- name: Prod image me dev tools nahi hone chahiye
  run: |
    ... 'if python -c "import pytest" 2>/dev/null; then
           echo "FAIL: prod image me pytest hai"; exit 1
         fi'

- name: Prod image root se nahi chalni chahiye
  run: |
    USER_ID=$(... --entrypoint id backend -u 2>/dev/null | tail -1)
    if [ "$USER_ID" = "0" ]; then exit 1; fi
```

"Build ho gayi" kaafi nahi hai. Kal koi `requirements-dev.txt` ko prod
stage me copy kar de, to build fir bhi pass hogi — par image bhaari aur
kam surakshit ho jayegi. Ye do checks usse rokte hain.

### `sleep 30` ke bajaye poll

```bash
for i in $(seq 1 60); do
  curl -sf http://localhost:8000/api/health && exit 0
  sleep 1
done
docker compose logs backend      # fail hua to logs ke saath
exit 1
```

Fixed sleep dheeme runner pe flaky hota hai aur tez runner pe waqt khaata
hai. Aur fail hone par logs ke bina CI failure debug karna namumkin hai.

---

## ⭐⭐⭐ Teen bugs jo CI-jaisi clean state ne pakde

Ye is phase ka sabse zaroori section hai. Teeno bugs **mahino se code me
the**, aur teeno sirf isliye chhupe rahe ki meri dev DB purani thi.

### Bug 1 — `docker compose down -v` ke baad 35 tests SKIP hue

```
31 passed, 35 skipped in 6.84s        <- CI me ye GREEN dikhta
```

`tokens` fixture `user1@seatpulse.dev` se login karti hai. Login fail hua
to fixture `pytest.skip()` karti hai. Aur skipped tests CI me **pass jaise
hi** dikhte hain.

Wajah `seed.py` me thi:

```python
existing = 3                                  # demo, organizer, admin
for i in range(existing, existing + to_create)   # user3, user4, ...
```

Numbering users ki **ginti** se bandhi thi. Named accounts banne ke baad
counter 3 pe pahunch jata tha, to **`user1` aur `user2` kabhi bante hi
nahi the.**

Purani DB me ye chhupa tha kyunki wo tab seed hui thi jab numbering alag
padi thi.

Fix: numbering ab fixed hai (`user1..userN`) aur sirf missing accounts
bante hain — jisse seed idempotent bhi ho gaya.

> **Sabak:** "66 passed" aur "31 passed, 35 skipped" — dono CI me hare
> dikhte hain. Skip count par nazar rakhna zaroori hai.

### Bug 2 — seeded event ka organizer NULL tha

Fix ke baad 62 pass, **4 fail** — saare gate check-in wale:

```
KeyError: 'ok'
```

Endpoint `{"ok": ...}` ke bajaye kuch aur laut raha tha. Seedha call
karke dekha:

```
HTTP 403
{"detail":"Ye ticket tumhare event ka nahi hai"}
```

`seed.py` event banata tha par `organizer_id` set hi nahi karta tha — wo
NULL rehta tha. Check-in ka ownership check `event.organizer_id == user.id`
dekhta hai, jo NULL ke saath kabhi match nahi karta.

Iska matlab sirf test failure nahi tha — **demo data hi toota hua tha**:
seeded event organizer portal me dikhta hi nahi, aur uske tickets gate pe
scan nahi hote.

Purani DB me chhupa tha kyunki wahan event portal se banaya gaya tha.

Fix: seed ab event ko organizer account se jodta hai, aur purani DB ke
liye backfill bhi karta hai.

### Bug 3 — idempotency test agle run me fail hoti thi

Prod stack pe pehli baar suite chalayi to ek test fail hui:

```
assert 0 == 1    # "ek booking honi thi, mili 0"
```

Pehla shak multi-worker par gaya. Galat tha. Test ki idempotency key
**fixed** thi:

```python
"Idempotency-Key": f"test-{seat_id}-once"
```

Wo key Redis me TTL tak zinda rehti hai. Agla test run usi key par
**replay** le aata: 201 milta tha, par nayi booking banti hi nahi thi.

Test `reset_state.py` par nirbhar thi, jo chhoot sakta hai (aur CI me
chalta hi nahi).

Fix: har run ka apna `RUN_ID` suffix. Ab suite lagatar do baar bina kisi
reset ke pass hoti hai — jo pehle nahi hoti thi.

> **Sabak:** "multi-worker me fail ho raha hai" ka pehla matlab "multi-worker
> ka bug hai" nahi hota. Yahan teeno baar asli wajah kuch aur thi.

---

## Chalane ke commands

```bash
# ---- Production stack ----
docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build

# Multi-worker broadcast proof
docker compose cp loadtest/verify_multiworker.py backend:/tmp/vmw.py
docker compose exec backend python /tmp/vmw.py

# Connections check — 4 workers, 100 ki limit
docker compose exec db psql -U seatpulse -d seatpulse \
  -c "SELECT count(*) FROM pg_stat_activity WHERE datname='seatpulse';"

# Wapas dev pe
# ⚠️ --build zaroori hai: prod build wahi image tag overwrite karta hai,
#    to bina rebuild ke dev container prod image se chalta hai (aur usme
#    pytest nahi hota).
docker compose -f docker-compose.yml -f docker-compose.prod.yml down
docker compose up -d --build backend worker
```

⚠️ **Prod image me pytest nahi hai.** Prod stack ke against tests chalane
ke liye dev image se ek throwaway container chalao:

```bash
docker build --target dev -t seatpulse-test:dev ./backend
docker run --rm --network seatpulse-event-engine_default \
  -e TEST_BASE_URL=http://backend:8000 \
  -e DATABASE_URL="postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse" \
  -e REDIS_URL=redis://redis:6379/0 \
  seatpulse-test:dev python -m pytest tests/ -q
```

### CI jaisa clean run locally

Ye har bade change ke baad chalana chahiye — teeno upar wale bugs isi se
mile:

```bash
docker compose down -v          # ⚠️ poora database uda deta hai
docker compose up -d --build
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
docker compose exec backend python -m pytest tests/ -q
```

**Skip count zaroor dekhna** — `66 passed` chahiye, `31 passed, 35 skipped`
nahi.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `.github/workflows/ci.yml` | Teen jobs — tests, prod build + assertions, frontend |
| `docker-compose.prod.yml` | 4 workers, chhota pool, nginx frontend |
| `frontend/nginx.conf` | SPA fallback + cache headers |
| `frontend/package-lock.json` | Tha hi nahi — `npm ci` ke bina reproducible build namumkin |
| `loadtest/verify_multiworker.py` | Cross-worker broadcast proof |

**Badle:**
| File | Kya |
|---|---|
| `backend/Dockerfile` | `dev` / `prod` targets, non-root prod |
| `frontend/Dockerfile` | `dev` / `build` / `prod` (nginx) |
| `backend/config.py` | `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` |
| `backend/database.py` | Pool ab config se |
| `backend/main.py` | `/api/health` me `worker_pid` |
| `backend/seed.py` | **Bug 1 + Bug 2 fix** |
| `backend/tests/test_concurrency.py` | **Bug 3 fix** — per-run idempotency keys |
| `docker-compose.yml` | `target: dev` explicit |

---

## Related

- [Phase 5 — WebSockets](05-websockets.md) — wo daawa jo yahan verify hua
- [Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) — admission control aur pool ka invariant
- [Phase 15 — Locking Benchmark](15-locking-benchmark.md) — pichla measurement phase
- [testing.md](../reference/testing.md) — commands
- [docker-commands.md](../reference/docker-commands.md) — compose reference
