# Phase 9 — Rate Limiting + Idempotency

[Phase 8 — Dashboard UI](08-dashboard-ui.md) ke baad ka kaam.

**Kya bana:** bot protection aur double-click protection. Dono Redis pe, koi nayi service nahi.

> ⭐ Ye phase project ki **kahani complete** karta hai. Poora project is premise pe khada hai ki "flash sale me bots aate hain" — par ab tak unhe rokne ka koi intezaam tha hi nahi. Interviewer ye gap pakad sakta tha.

---

## Part 1 — Rate Limiting

### Algorithm — token bucket kyu

| Algorithm | Problem |
|---|---|
| **Fixed window** (60 req/minute) | Boundary pe 2x burst nikal jata hai — 59th second me 60, aur 61st second me 60 aur. Ek second me 120 |
| **Sliding window log** (har request ka timestamp) | Bilkul accurate, par har request ka timestamp store karna padta hai — memory khaata hai |
| **Token bucket** ✅ | Bucket me `capacity` tokens, `refill` tokens/second bharte rehte hain. Har request ek token khaati hai |

**Token bucket kyu jeeta:** user ka natural behaviour allow hota hai — 4-5 seats jaldi-jaldi click karna theek hai (burst) — par ek script jo 100 req/s maar raha hai wo refill rate pe aake atak jata hai.

### ⭐ Lua me kyu, Python me kyu nahi

```python
# GALAT — race condition
tokens = redis.get(key)          # 1. padho
tokens = calculate(tokens)       # 2. hisaab lagao
redis.set(key, tokens)           # 3. likho
```

Un teen steps ke beech dusra request bhi **wahi purane tokens padh leta** hai, aur dono ko permission mil jaati hai. Classic read-modify-write race.

Lua script Redis ke andar **ek unit** me chalti hai — beech me kuch nahi ghus sakta. Wahi wajah jo Phase 4 me lock release ke liye thi.

```lua
local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])

if tokens == nil then tokens = capacity; ts = now end    -- pehli baar: bucket full

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)   -- refill

if tokens >= needed then
    tokens = tokens - cost
    allowed = 1
end
```

**TTL bhi set karte hain** — `capacity / refill + 60` second. Bucket poora bharne ke baad key ka koi matlab nahi (wo waise bhi full bucket hoti). Redis khud purani keys saaf karta rehta hai.

### ⭐ Limit KIS PAR — ye sabse important design decision hai

**Per user / per email. Per IP nahi.**

| Kyu IP nahi | |
|---|---|
| Proxy ke peeche | App ko har request **ek hi IP** se aati dikhti hai (load balancer ki). `X-Forwarded-For` set karo to wo **spoof** ho sakta hai |
| NAT | Poora office/college ek IP share karta hai. Ek bot ki wajah se 200 log block — galat |
| Attacker | IP badalna aasan hai. Jis account ko todna hai uska **email badalna nahi** |

> **Per-IP limiting edge par honi chahiye** — nginx, Cloudflare, API gateway. App identity par limit lagata hai, jo zyada targeted hai.
>
> Ye interview me achha jawab hai: "Maine IP par limit nahi lagayi kyunki app proxy ke peeche hoti hai aur wahan IP bharosemand nahi. IP limiting edge ka kaam hai."

**Bonus:** isi design ki wajah se **load test bina badle pass ho jata hai** — har Locust user ka apna account hai, to har ek ka apna bucket.

### Limits aur unka logic

```python
SEAT_LOCK  = Limit(capacity=15, refill=5)        # 15 burst, phir 5/s
BOOKING    = Limit(capacity=5,  refill=1)
LOGIN_FAIL = Limit(capacity=5,  refill=1/60)     # 5 galtiyan, phir 1/minute
REGISTER   = Limit(capacity=5,  refill=1/120)
```

| Limit | Kyu ye number |
|---|---|
| `SEAT_LOCK` | User 4-5 seats jaldi try kar sakta hai. Sustained 5/s se zyada matlab script hai |
| `BOOKING` | Booking soch ke hoti hai, itni tez nahi |
| `LOGIN_FAIL` | Credential stuffing yahin marti hai |
| `REGISTER` | Ek IP se account farm banane se rokta hai |

### ⭐ Login limit sirf GALAT password pe kharch hoti hai

```python
# Pehle sirf jhaanko — token kharch mat karo
allowed, _, retry_after = check(bucket, LOGIN_FAIL, cost=0)
if not allowed:
    raise HTTPException(429, ...)

user = db.scalar(...)

if user is None or not verify_password(...):
    check(bucket, LOGIN_FAIL, cost=1)     # <- ab kharch karo
    raise HTTPException(401, "Email ya password galat hai")
```

Jo user roz sahi password se login karta hai wo **kabhi rate limit me nahi phasta**. Sirf galat guesses count hote hain.

> Agar har login attempt count karte, to ek user jo din me 20 baar login karta hai (multiple devices, tabs) wo block ho jata — jabki usne kuch galat nahi kiya.

### ⚠️ Fail-open, fail-closed nahi

```python
try:
    allowed, remaining, retry_after = _bucket(...)
except Exception:
    return True, limit.capacity, 0     # Redis down -> ALLOW
```

Redis girte hi poori site band ho jaati agar fail-closed karte. Rate limiting ek **protection** hai, correctness nahi — aur booking ki correctness ki teen alag layers pehle se hain.

### Response headers

```
X-RateLimit-Limit: 15
X-RateLimit-Remaining: 3
Retry-After: 2          (sirf 429 pe)
```

Ye **hamesha** bhejte hain, sirf 429 pe nahi — client dekh sakta hai ki wo limit ke kitna paas hai aur khud slow ho sakta hai.

---

## Part 2 — Idempotency Keys

### Problem

User "Confirm Booking" pe **double-click** karta hai. Ya network glitch pe browser request retry kar deta hai.

**Abhi kya hota tha:** dusri request ko 409 milta — kyunki seat tab tak `booked` ho chuki hoti.

Nateeja to sahi tha. **Par wo sanyog se sahi tha, design se nahi.** Aur user ko ek confusing error dikhta jabki uski booking ho chuki hai.

Aur jab payments aayenge, ye sanyog kaafi nahi hoga — "paisa kat gaya par booking nahi hui" wala case yahin se aata hai.

### Solution

Client har booking attempt ke saath ek unique `Idempotency-Key` bhejta hai:

```
Pehli request  -> kaam karo, jawab STORE karo, jawab do
Wahi key phir  -> kaam MAT karo, stored jawab wapas do
```

Ye Stripe, Razorpay, aur har payment API ka standard pattern hai.

### Flow

```python
idem = Idempotency(request, user.id, "booking", payload.model_dump())

cached = idem.begin()          # SET NX se slot claim
if cached:
    return idem.replay(response, cached)

try:
    booking = _perform_booking(payload, db, user)
except Exception:
    idem.abort()               # claim chhod do
    raise

idem.complete(result, status_code=201)
return result
```

### Chaar cheezein jo detail me sahi karni padti hain

**1. Claim `SET NX` se hoti hai**

```python
redis_client.set(key, '{"state":"processing",...}', nx=True, ex=60)
```

Do parallel requests me se ek hi jeetega — wahi atomic pattern jo seat lock me hai.

**2. Body ka fingerprint bhi store hota hai**

```python
raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:32]
```

Agar koi **wahi key ALAG body** ke saath bheje, to wo bug hai (ya attack). Chupchap purana jawab lauta dena galat hoga → **422**.

`sort_keys=True` zaroori hai — `{"a":1,"b":2}` aur `{"b":2,"a":1}` ka hash same aana chahiye.

**3. "Processing" state pe 409**

Pehli request abhi chal rahi hai (double-click ka asli case) → 409, client thodi der baad retry kar sakta hai.

Uski TTL sirf **60 second** hai — server beech me crash ho jaye to key hamesha ke liye atki na rahe.

**4. Fail pe `abort()` zaroori hai**

```python
except Exception:
    idem.abort()      # claim delete
    raise
```

Bina iske 500 ke baad user usi key se retry hi nahi kar paata — 60 second tak "already processing" milta rehta.

### Key me `user_id` kyu

```python
f"idem:{user_id}:{scope}:{idem_key}"
```

Do users galti se same UUID bhej dein to ek ko dusre ki booking na dikh jaye.

### Result TTL — 24 ghante

Stripe bhi yahi use karta hai. Retry aur double-click isse kahin pehle ho jaate hain.

### Frontend

```js
export const createBooking = (seatId, idempotencyKey = crypto.randomUUID()) =>
  request("/api/bookings", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ seat_id: seatId }),
  });
```

`crypto.randomUUID()` browser me built-in hai — koi uuid package nahi chahiye.

> Key har **attempt** ke liye nayi banti hai, har seat ke liye nahi. Matlab ek confirm-click ka retry safe hai, par user jaan-boojh ke dubara book karna chahe to wo alag request hai.

**Header optional hai** — na bheja to normal behaviour. Purane clients tootte nahi.

---

## ⭐ Test ne ek bug pakda — `cost=0` wala peek

Pehla run me brute-force test fail hua:

```
AssertionError: Brute force nahi ruka: [401]
```

12 galat passwords ke baad bhi 429 nahi aaya.

**Wajah:** login me pehle `cost=0` se "peek" karte hain (token kharch kiye bina check). Lua me tha:

```lua
if tokens >= cost then    -- cost = 0
```

Bucket khali ho gaya (`tokens = 0`), par `0 >= 0` **true** hai — to peek hamesha allow kar deta tha!

**Fix:**

```lua
local needed = cost
if cost == 0 then
    needed = 1        -- peek me bhi kam se kam 1 token hona chahiye
end

if tokens >= needed then
    tokens = tokens - cost    -- peek me cost 0, to kuch ghata nahi
    allowed = 1
end
```

> Chhota bug hai, par **poori brute-force protection bekaar kar raha tha** — aur manually test karte to shayad kabhi na pakda jata. Automated test ne pehle hi run me pakad liya.

---

## ✅ Proof

### 1. Rate limit — burst

```bash
TOKEN=$(curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"demo1234"}' \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

for i in $(seq 1 25); do
  curl -s -o /dev/null -w "%{http_code} " -X POST \
    -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/seats/5/lock
done
```

**Actual output:**
```
200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 200 429 429 200 429
```

Beech me ek 200 dikha? **Wahi token bucket ka refill hai** — ek second beeta, 5 tokens wapas aaye.

### 2. Headers

```bash
curl -D - -o /dev/null -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock
```
```
HTTP/1.1 429 Too Many Requests
retry-after: 1
x-ratelimit-limit: 15
x-ratelimit-remaining: 0
```

### 3. ⭐ Idempotency

```bash
KEY="test-$(date +%s)"

curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Idempotency-Key: $KEY" -H "Content-Type: application/json" \
  -d '{"seat_id":7}' http://localhost:8000/api/bookings
# {"id":146, ...}                       HTTP 201

# WAHI key dubara
curl -X POST ... -H "Idempotency-Key: $KEY" -d '{"seat_id":7}' ...
# {"id":146, ...}                       HTTP 201 + x-idempotent-replay: true

# Wahi key, ALAG body
curl -X POST ... -H "Idempotency-Key: $KEY" -d '{"seat_id":8}' ...
# {"detail":"Ye Idempotency-Key pehle alag data ke saath use ho chuki hai"}   422
```

**Database:**
```sql
SELECT count(*) FROM bookings WHERE seat_id=7;
-- 1
```

Do requests, wahi booking id, **ek hi row**.

### 4. Test suite

```
20 passed in 22.74s
```

7 naye tests: burst blocking, headers, **per-user isolation**, brute force, idempotent replay, fingerprint mismatch, aur "header ke bina bhi kaam kare".

Sabse important `test_rate_limit_is_per_user_not_global` hai — ek user ka bucket khatam karke check karta hai ki **dusra user affected nahi hua**. Global limiter poore system ko ek bot ki wajah se band kar deta.

### 5. Load test — rate limiting ne toda to nahi?

```
Total requests   : 7,351
Failures         : 0
Requests/sec     : 124.0
p50 / p99        : 1,200 ms / 1,700 ms
```
```
✅ SAB PASS — koi overselling nahi hui
Seats: available=99, booked=1 · Bookings: confirmed=1
```

**Zero 429s** load test me — kyunki limits per-user hain aur har Locust user ka apna account hai.

Throughput 137 → 124 rps (~9% neeche). Wo har request pe ek extra Redis roundtrip ka kharcha hai. **Ye trade-off worth hai** — 9% throughput dekar bot protection mila.

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Kaunsa algorithm aur kyu?" | Token bucket. Fixed window boundary pe 2x burst deta hai; sliding log memory khaata hai. Token bucket natural bursts allow karta hai par sustained abuse rokta hai |
| "IP par kyu nahi lagaya?" | App proxy ke peeche hoti hai — IP bharosemand nahi, aur `X-Forwarded-For` spoof ho sakta hai. NAT ke peeche poora office ek IP share karta hai. IP limiting edge (nginx/Cloudflare) ka kaam hai; app identity par limit lagata hai |
| "Lua kyu?" | Read-modify-write race. Python me GET → calculate → SET ke beech dusra request purane tokens padh leta |
| "Redis down ho jaye to?" | Fail-open — allow kar dete hain. Fail-closed karte to Redis girte hi site band. Rate limiting protection hai, correctness nahi |
| "Idempotency ki zaroorat kya, 409 to mil hi raha tha?" | Wo sanyog se sahi tha, design se nahi. Aur user ko error dikhta tha jabki uski booking ho chuki thi. Payments ke saath ye sanyog kaafi nahi hoga |
| "Same key alag body aaye to?" | 422. Chupchap purana jawab dena galat hoga — wo bug ya attack hai, isliye fingerprint compare karte hain |
| "Load test toota nahi?" | Nahi — limits per-user hain, har Locust user ka apna account. Throughput 9% giri, wo extra Redis roundtrip ka kharcha hai |

---

## Common Problems

| Problem | Fix |
|---|---|
| Sab requests 429 aa rahi | `reset_state.py` chalao — purane buckets saaf ho jayenge |
| Rate limit lag hi nahi raha | `.env` me `RATE_LIMIT_ENABLED=True` hai? |
| Load test me 429 aa rahe | Users seed nahi hue — sab ek hi account use kar rahe honge |
| Idempotency kaam nahi kar rahi | Header ka naam exactly `Idempotency-Key` hona chahiye |
| "Yahi request abhi process ho rahi hai" atka hua | Pichhla request crash hua tha. 60 second me apne aap chhut jayega, ya `reset_state.py` |
| Test ke baad login block | `reset_state.py` ab `rl:*` bhi saaf karta hai |
| **pytest ke baad load test fail** | Brute-force test ne `user9` ka login bucket khali kar diya hai. Beech me `reset_state.py` chalao — warna Locust ka `on_start` login 429 khata hai aur poora run ruk jata hai |

---

## Files

```
backend/
├── rate_limit.py               ← naya ⭐ token bucket (Lua) + dependencies
├── idempotency.py              ← naya ⭐ SET NX claim + fingerprint + replay
├── config.py                   ← RATE_LIMIT_ENABLED
├── reset_state.py              ← ab rl:* aur idem:* bhi saaf karta hai
├── routers/
│   ├── seats.py                ← lock pe SEAT_LOCK limit
│   ├── bookings.py             ← BOOKING limit + idempotency wrapper
│   └── auth.py                 ← login (per email, sirf fail pe) + register
└── tests/test_concurrency.py   ← 7 naye tests (13 → 20)

frontend/src/
├── api.js                      ← Idempotency-Key header, Retry-After parse
└── booking/BookingContext.jsx  ← 429 ka friendly message
```

---

## Commit

```bash
git add .
git commit -m "Phase 9: Redis token-bucket rate limiting and idempotency keys

- Per-user/per-email limits (not per-IP — that belongs at the edge)
- Login budget only consumed on failed attempts
- Idempotent POST /api/bookings with body fingerprinting
- Fix: cost=0 peek always passed on an empty bucket"
```

---

## Related

- [Phase 4 — Redis Locking](04-redis-locking.md) — wahi Lua atomicity ka pattern
- [Phase 6 — Load Testing](06-load-testing.md) — load test
- [testing.md](../reference/testing.md) — saare test commands
- [roadmap.md](../roadmap.md) — aage kya
