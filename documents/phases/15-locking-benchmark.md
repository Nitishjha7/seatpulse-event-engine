# Phase 15 — Pessimistic vs Optimistic Locking Benchmark

> Poore project me maine optimistic locking use ki hai. Ye phase us faisle
> ko **maapne** ke liye hai — sahi sabit karne ke liye nahi.

---

## Sawaal

Phase 2 se hi maine `version` column wali optimistic locking use ki hai.
Interview me iska seedha counter-question hai:

> "`SELECT ... FOR UPDATE` kyu nahi? Wo to simple hai."

Ab tak mera jawab theory tha. Is phase me dono implement karke same load
pe chalaya, taaki jawab **numbers** ke saath ho.

**Aur agar numbers mere khilaf jaate, to wo bhi likhna tha.** Kuch had tak
gaye bhi — neeche hai.

---

## Do tareeke

```
OPTIMISTIC  — "koshish karo, takra gaye to haar maan lo"

    UPDATE seats SET status='booked', version = version + 1
    WHERE id = ? AND version = ? AND status IN ('available','locked')

    rowcount 0 -> koi aur jeet gaya -> TURANT 409


PESSIMISTIC — "pehle taala lagao, phir aaram se karo"

    SELECT * FROM seats WHERE id = ? FOR UPDATE   <- yahan BLOCK hota hai
    (ab row mere lock me hai, aaram se check karo)
    UPDATE ...
```

Farak correctness ka nahi hai — **dono overselling rokte hain**. Farak
behaviour ka hai:

| | Haarne wala kya karta hai |
|---|---|
| Optimistic | turant 409 leke chala jata hai |
| Pessimistic | **qataar me lagta hai**, apni baari par pata chalta hai seat ja chuki, phir 409 |

📁 [`backend/locking_strategies.py`](../../backend/locking_strategies.py)

### ⚠️ Benchmark asli code chalata hai, uski copy nahi

Sabse aasan galti ye hoti ki benchmark ke liye ek alag endpoint bana lete.
Tab hum us cheez ko maap rahe hote jo deploy hoti hi nahi.

Isliye dono strategies **usi `_perform_booking()`** se chalti hain jo
production me chalta hai. Sirf claim wala step badalta hai:

```python
if strategy == PESSIMISTIC:
    claim = claim_pessimistic(db, payload.seat_id)
else:
    claim = claim_optimistic(db, payload.seat_id, expected_version)
```

Knobs query params se aate hain, **par sirf tab jab `BENCHMARK_MODE=true` ho.**
Warna chupchaap ignore. Ek query param jo locking semantics badal de, wo
production me footgun hai — koi bhi `?redis_lock=off` bhej ke sabse mehngi
code path chala sakta hai.

---

## Round 1 — Locust, 300 users, ek seat

```bash
bash loadtest/run_benchmark.sh          # chaar scenarios
```

| Scenario | Total req | `/bookings` tak pahunche | req/s | p50 |
|---|---|---|---|---|
| optimistic, Redis **on** | 1733 | **1** | 59.0 | 3300 ms |
| pessimistic, Redis **on** | 1871 | **1** | 63.9 | 2900 ms |
| optimistic, Redis **off** | 1783 | 1483 | 63.1 | 3000 ms |
| pessimistic, Redis **off** | 1857 | 1557 | 63.4 | 3200 ms |

Chaaron me integrity check pass: **exactly 1 confirmed booking**, koi
overselling nahi.

Aur chaaron ke numbers... lagbhag ek jaise. Do wajah nikli, aur dono
apne aap me finding hain:

### ⭐ Finding 1 — Redis on ho to DB strategy tak load pahunchta hi nahi

1433 contended requests me se **1** `/api/bookings` tak pahunchi. Baaki
1432 Redis lock pe hi 409 leke lautt gayi.

Ye mere Phase 4 wale daawe ka seedha proof hai — par iska ek natija bhi
hai: **production config me DB strategy badalne se kuch farak pad hi nahi
sakta**, kyunki wo code chalta hi nahi.

Isliye baaki benchmark Redis off karke chalana pada. Wo "cheating" nahi
hai — wo hi ek tareeka hai DB layer ko akela dekhne ka.

### Finding 2 — 300 users pe admission control bottleneck ban jata hai

Redis off karne ke baad bhi chaaron ~63 req/s aur p50 ~3s pe atke rahe.

Wajah: [Phase 7](07-auth-google-oauth.md) me maine admission control lagayi
thi — ek semaphore jo sirf 30 requests andar aane deta hai. 300 users me
har request ~3 second **queue me** khadi rehti hai.

Us 3 second ke saamne database ka kaam (~milliseconds) dikhta hi nahi.
Locust poore system ko maap raha tha, us ek line ko nahi jo maine badli thi.

---

## Round 2 — Micro-benchmark

Locust galat tool nahi tha, galat **sawaal** ka jawab de raha tha. To ek
focused benchmark likha:

- Redis layer **off** (warna DB tak kuch aata hi nahi)
- concurrency **25**, admission limit (30) se **neeche** — queue wait
  numbers me na ghule
- login **pehle ek baar** — bcrypt (~400ms) sab kuch daba deta hai
- har round me seat wapas free karke **asli contention dubara** paida —
  ek hi contention event maapna sirf shor hota hai
- 40 rounds × 25 = **1000 requests per strategy**

```bash
docker compose exec backend python /loadtest/micro_benchmark.py
```

📁 [`loadtest/micro_benchmark.py`](../../loadtest/micro_benchmark.py)

### Nateeja — 4 runs

| Run | Order | opt p50 | pess p50 | opt p99 | pess p99 | p50 ratio | p99 ratio |
|---|---|---|---|---|---|---|---|
| A | opt first | 319.6 | 322.6 | 858.4 | 645.6 | 1.01× | 0.75× |
| B | opt first | 291.7 | 271.2 | 625.0 | 499.2 | 0.93× | 0.80× |
| C | opt first | 285.2 | 273.2 | 517.0 | 510.9 | 0.96× | 0.99× |
| D | **pess first** | 310.2 | 291.8 | 642.0 | 514.5 | 0.94× | 0.80× |

Har run me 40/40 wins, 960 conflicts, **0 errors** — matlab comparison
valid hai.

### ⭐ Finding 3 — pessimistic *thoda tez* nikla, dhima nahi

Ye mere expectation ke **ulta** hai, aur likhna zaroori hai.

Pehla shak ordering bias ka tha (jo pehle chale wo thandi machine pe
chale). Isliye run D me order ulta kiya — **wahi nateeja**. To ye bias
nahi hai.

Wajah code me hai. Seat book hone ke BAAD aane wale losers ke liye:

```
optimistic  -> UPDATE ... WHERE version=? (0 rows match) -> rollback
                ^^^ write statement, phir bhi chalti hai

pessimistic -> SELECT ... FOR UPDATE (lock free hai, turant milta hai)
               status check -> 'booked' -> return, koi UPDATE hi nahi
```

Yaani ek hi seat pe 25 me se 24 losers ke liye pessimistic path me **kam
kaam** hota hai. Blocking hoti hi nahi kyunki jeetne wala millisecond me
commit kar chuka hota hai.

**Par farak 5-7% ka hai, aur run-to-run variance bhi utna hi hai.** Isliye
imaandar nateeja ye hai: *is scale pe dono ka farak measurable nahi hai.*

---

## ⭐ Finding 4 — claim step request ka 1/33 hissa hai

"Farak kyu nahi dikha" ka asli jawab yahan hai. Postgres pe statement
logging on karke ek booking request gini:

```bash
docker compose exec db psql -U seatpulse -d seatpulse \
  -c "ALTER SYSTEM SET log_statement='all';" -c "SELECT pg_reload_conf();"
```

**Ek booking = 33 SQL statements.** Breakdown:

| Kitni | Kya |
|---|---|
| 4 | `SELECT 1` — pool pre-ping health checks |
| 4 / 2 / 2 | BEGIN / COMMIT / ROLLBACK |
| 3 | `SELECT seats` |
| 2 | `SELECT users` (auth) |
| 2 | `SELECT events` |
| 2 | `count(seats)` |
| 2 | `count(bookings)` |
| 2 | `min(seats.price)` |
| … | ticket worker ka `UPDATE bookings SET qr_token=…` |
| **1** | **asli claim** — `UPDATE seats SET status='booked'` |

Locking strategy badalne se **33 me se 1 statement** badalta hai. Baaki 32
bilkul same rehte hain. Isliye 5% ka farak bilkul expected hai — aur wo
bhi noise me doob jata hai.

Baseline bhi yahi kehta hai: concurrency 1 pe (koi contention hi nahi) ek
booking ~50-70ms leti hai. Claim step usme se do-teen millisecond hai.

### Ek aur cheez jo ginti ne pakdi

`count(seats)`, `count(bookings)` aur `min(price)` — teeno **do-do baar**
chal rahe hain. Wajah: `pricing_state()` ek baar `price_now()` me chalta
hai aur dobara `broadcast_seat_update()` me.

Matlab har booking me **6 queries faaltu** hain.

Maine ye abhi fix **nahi** kiya, jaan-boojh ke — fix karne se upar wale
saare numbers badal jaate aur benchmark dobara chalana padta. Ye [roadmap](../roadmap.md)
me follow-up ke roop me likha hai. Par ye is phase ka sabse practical
faayda hai: **maapne se ek asli inefficiency mil gayi**, jo locking se
koi lena-dena nahi rakhti.

---

## To optimistic hi kyu rakha?

Numbers ne throughput ka farak nahi dikhaya. Phir bhi optimistic hi
default hai, aur wajah **failure mode** hai, speed nahi:

| | Load badhne par |
|---|---|
| **Optimistic** | loser turant nikal jata hai. Connection turant free. |
| **Pessimistic** | loser **DB connection pakde** qataar me khada rehta hai |

Pool me 40 connections hain. 500 log ek seat pe hon aur har haarne wala
apna connection pakde rakhe, to pool minton me nahi — seconds me khatam
ho jata hai. Bilkul wahi bimari jo [Phase 7](07-auth-google-oauth.md) me
`idle in transaction` ke roop me pakdi thi.

**Ye is benchmark me nahi dikha, aur maine dikhane ka daawa bhi nahi kiya.**
Nahi dikha kyunki jeetne wali transaction millisecond me commit kar deti
hai — koi rukta hi nahi. Pessimistic ka kharcha us waqt ke saath badhta
hai jitni der lock pakda jata hai. Aaj wo waqt ~2ms hai.

Khatra ye hai ki wo waqt **badh sakta hai**: transaction me ek external
call, ek slow query, ek badi report — aur pessimistic path seedha pool
exhaustion me badal jayega, jabki optimistic ka behaviour waisa hi rahega.

> Ek line me: **maine optimistic isliye nahi choose kiya ki wo aaj tez hai
> (wo nahi hai). Isliye choose kiya ki wo kal bura nahi hoga.**

---

## Kya toota (aur kya seekha)

### 1. Pehla micro-benchmark run poora jhootha tha

```
strategy       reqs  won   409   err
optimistic     1000   33   463   504     <- 504 errors!
pessimistic    1000   33   392   575
```

Rate limit buckets clear karne wale code me prefix galat likha tha —
`ratelimit:*`, jabki asli prefix `rl:` hai ([`rate_limit.py`](../../backend/rate_limit.py)).
Buckets clear hote hi nahi the, aur chauthe round se har request 429 khaane
lagti thi. Wo 429 latency numbers me ghul rahe the.

**Ise `errors` column ne pakda** — jo maine sirf sanity ke liye rakha tha.
Agar bas p50/p99 print karta, to numbers *bilkul theek dikhte* aur main
poori tarah galat conclusion nikaal ke doc likh deta.

Sabak: benchmark me hamesha ek **invariant** check rakho ("har round me
theek 1 booking jeetni chahiye"), sirf timing mat chhapo.

### 2. Pehla proof-of-concept me 0 WebSocket messages (Phase 14 wala sabak dohraya)

Locust ke chaar runs "sab barabar" dikha rahe the aur pehla reflex tha ki
strategy switch kaam hi nahi kar raha. Asal me switch theek chal raha tha —
Redis 1432/1433 requests rok raha tha.

Per-endpoint CSV dekhe bina ye pata nahi chalta. Aggregate numbers ne
sach chhupa liya tha.

### 3. "Benchmark ne mera daawa confirm nahi kiya" — aur wahi likha

Sabse aasan hota ki numbers ko aise ghuma dete ki optimistic jeetta hua
dikhe. Numbers ne wo nahi kaha. Doc me wahi likha hai jo mila: *farak
measurable nahi tha, aur jo thoda tha wo pessimistic ke haq me tha.*

Interview me "maine measure kiya aur mera andaza galat nikla" bolna
"maine measure kiya aur main sahi tha" se **zyada** bharosa deta hai.

---

## Kaise dobara chalao

```bash
# 1. Benchmark mode on karo
echo "BENCHMARK_MODE=true" >> backend/.env
docker compose up -d backend

# 2. Locust — poora system, chaar scenarios
bash loadtest/run_benchmark.sh

# 3. Micro-benchmark — sirf DB claim step
docker compose exec backend python /loadtest/micro_benchmark.py

# Order bias check
docker compose exec -e BENCH_ORDER=pessimistic,optimistic backend \
    python /loadtest/micro_benchmark.py

# 4. Wapas off karo — production me ye knobs nahi hone chahiye
sed -i '/BENCHMARK_MODE=true/d' backend/.env
docker compose up -d backend
```

Tests dono modes me pass hote hain (**66/66**) — ye jaan-boojh ke hai,
taki benchmark mode galti se on chhut jaye to bhi suite meaningful rahe.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `backend/locking_strategies.py` | Dono claim strategies, ek jagah |
| `loadtest/run_benchmark.sh` | Chaar Locust scenarios + integrity check |
| `loadtest/micro_benchmark.py` | Focused DB-only measurement |

**Badle:**
| File | Kya |
|---|---|
| `backend/config.py` | `BENCHMARK_MODE` (default false) |
| `backend/routers/bookings.py` | Claim step strategies me nikala; benchmark knobs |
| `loadtest/locustfile.py` | `BOOKING_STRATEGY` / `USE_REDIS_LOCK` env |
| `docker-compose.yml`, `backend/.env.example` | `BENCHMARK_MODE` |
| `backend/tests/test_concurrency.py` | 3 naye tests |

---

## Related

- [Phase 2 — Postgres + Models](02-postgres-models.md) — `version` column kahan se aaya
- [Phase 4 — Redis Locking](04-redis-locking.md) — wo layer jo 1432/1433 rok deti hai
- [Phase 6 — Load Testing](06-load-testing.md) — Locust setup
- [Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) — admission control aur pool exhaustion
- [Interview Prep](../interview-prep.md) — `FOR UPDATE` wala sawaal
- [testing.md](../reference/testing.md) — chalane ke commands
