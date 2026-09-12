# Phase 9 — Rate Limiting + Idempotency

Follows [Phase 8 — Dashboard UI](08-dashboard-ui.md).

**Implemented:** Bot protection and double-click protection. Both use Redis; no new services required.

> ⭐ This phase completes the project's **narrative**. The project is built on the premise that "bots attack flash sales," yet until now, there was no mechanism to stop them. An interviewer could easily have identified this gap.

---

## Part 1 — Rate Limiting

### Algorithm — Why Token Bucket

| Algorithm | Problem |
|---|---|
| **Fixed window** (60 req/minute) | Allows 2x burst at the boundary — 60 requests in the 59th second, and another 60 in the 61st. 120 requests in one second. |
| **Sliding window log** (timestamp per request) | Highly accurate, but requires storing a timestamp for every request — memory intensive. |
| **Token bucket** ✅ | Bucket holds `capacity` tokens, `refill` tokens/second. Each request consumes one token. |

**Why Token Bucket won:** It allows natural user behavior — clicking 4-5 seats in quick succession is acceptable (burst) — but a script firing 100 req/s will be throttled to the refill rate.

### ⭐ Why Lua, not Python

```python
# WRONG — race condition
tokens = redis.get(key)          # 1. read
tokens = calculate(tokens)       # 2. calculate
redis.set(key, tokens)           # 3. write
```

Between those three steps, a second request could **read the same old token count**, and both would be granted permission. This is a classic read-modify-write race.

A Lua script runs as **one atomic unit** inside Redis—nothing can interrupt it. We used this same approach for lock release in Phase 4.

```lua
local bucket = redis.call("HMGET", key, "tokens", "ts")
local tokens = tonumber(bucket[1])

if tokens == nil then tokens = capacity; ts = now end    -- first time: bucket full

local elapsed = math.max(0, now - ts)
tokens = math.min(capacity, tokens + elapsed * refill)   -- refill

if tokens >= needed then
    tokens = tokens - cost
    allowed = 1
end
```

**TTL is also set** — `capacity / refill + 60` seconds. Once the bucket is full, the key is redundant. Redis automatically cleans up expired keys.

### ⭐ What to limit—the most important design decision

**Per user/email, not per IP.**

| Why not IP | |
|---|---|
| Behind a proxy | The app sees every request from the **same IP** (the load balancer). `X-Forwarded-For` can be **spoofed**. |
| NAT | An entire office/campus shares one IP. One bot could incorrectly block 200 legitimate users. |
| Attacker | Changing an IP is trivial. Changing the **email** of the account they want to compromise is difficult. |

> **Per-IP limiting should be at the edge** — nginx, Cloudflare, API gateway. The app limits by identity, which is more targeted.
>
> This is a strong interview answer: "I didn't implement IP-based limiting because the app sits behind a proxy where IPs are unreliable. IP limiting is an edge-layer responsibility."

**Bonus:** This design allows **load tests to pass without modification** — each Locust user has their own account, so each has their own bucket.

### Limits and Logic

```python
SEAT_LOCK  = Limit(capacity=15, refill=5)        # 15 burst, then 5/s
BOOKING    = Limit(capacity=5,  refill=1)
LOGIN_FAIL = Limit(capacity=5,  refill=1/60)     # 5 errors, then 1/minute
REGISTER   = Limit(capacity=5,  refill=1/120)
```

| Limit | Rationale |
|---|---|
| `SEAT_LOCK` | Users may try 4-5 seats quickly. Sustained >5/s indicates a script. |
| `BOOKING` | Booking is a deliberate action, not high-frequency. |
| `LOGIN_FAIL` | Stops credential stuffing. |
| `REGISTER` | Prevents account farming from a single IP. |

### ⭐ Login limit only consumes on WRONG passwords

```python
# Peek first — do not consume a token
allowed, _, retry_after = check(bucket, LOGIN_FAIL, cost=0)
if not allowed:
    raise HTTPException(429, ...)

user = db.scalar(...)

if user is None or not verify_password(...):
    check(bucket, LOGIN_FAIL, cost=1)     # <- consume now
    raise HTTPException(401, "Incorrect email or password")
```

Users who log in correctly every day **never hit the rate limit**; only incorrect guesses are counted.

> If every attempt were counted, a user logging in 20 times a day (e.g., multiple devices or tabs) would be blocked despite doing nothing wrong.

### ⚠️ Fail-open, not fail-closed

```python
try:
    allowed, remaining, retry_after = _bucket(...)
except Exception:
    return True, limit.capacity, 0     # Redis down -> ALLOW
```

If we used fail-closed, the entire site would go down if Redis failed. Rate limiting is a **protection** layer, not a correctness layer; booking correctness is already handled by three other layers.

### Response headers

```
X-RateLimit-Limit: 15
X-RateLimit-Remaining: 3
Retry-After: 2          (only on 429)
```

These are sent **always**, not just on 429, allowing the client to see how close they are to the limit and throttle themselves.

---

## Part 2 — Idempotency Keys

### Problem

A user **double-clicks** "Confirm Booking," or a network glitch causes the browser to retry the request.

**Previous behavior:** The second request received a 409 because the seat was already `booked`.

The result was correct, but **by coincidence, not by design.** The user saw a confusing error despite their booking being successful.

When payments are integrated, this coincidence will not suffice; the "money deducted but no booking" scenario stems from this.

### Solution

The client sends a unique `Idempotency-Key` with every booking attempt:

```
First request  -> process, STORE response, return it
Same key again -> do NOT process, return stored response
```

This is the standard pattern for Stripe, Razorpay, and other payment APIs.

### Flow

```python
idem = Idempotency(request, user.id, "booking", payload.model_dump())

cached = idem.begin()          # Claim slot via SET NX
if cached:
    return idem.replay(response, cached)

try:
    booking = _perform_booking(payload, db, user)
except Exception:
    idem.abort()               # Release claim
    raise

idem.complete(result, status_code=201)
return result
```

### Four critical implementation details

**1. Claim via `SET NX`**

```python
redis_client.set(key, '{"state":"processing",...}', nx=True, ex=60)
```

Only one of two parallel requests will win — the same atomic pattern used for seat locking.

**2. Body fingerprinting**

```python
raw = json.dumps(payload, sort_keys=True, separators=(",", ":"))
fingerprint = hashlib.sha256(raw.encode()).hexdigest()[:32]
```

If someone sends the **same key with a DIFFERENT body**, it is a bug (or an attack). Returning the old response would be incorrect (returns **422**).

`sort_keys=True` is mandatory — `{"a":1,"b":2}` and `{"b":2,"a":1}` must produce the same hash.

**3. "Processing" state returns 409**

If the first request is still running (the double-click case), it returns 409, allowing the client to retry after a short delay.

The TTL is only **60 seconds** — if the server crashes, the key won't be stuck forever.

**4. `abort()` on failure is mandatory**

```python
except Exception:
    idem.abort()      # delete claim
    raise
```

Without this, the user cannot retry with the same key after a 500 error; they would receive "already processing" for 60 seconds.

### Why include `user_id` in the key

```python
f"idem:{user_id}:{scope}:{idem_key}"
```

Prevents one user from seeing another's booking if they accidentally use the same UUID.

### Result TTL — 24 hours

Stripe uses this duration. Retries and double-clicks happen well within this window.

### Frontend

```js
export const createBooking = (seatId, idempotencyKey = crypto.randomUUID()) =>
  request("/api/bookings", {
    method: "POST",
    headers: { "Idempotency-Key": idempotencyKey },
    body: JSON.stringify({ seat_id: seatId }),
  });
```

`crypto.randomUUID()` is built into the browser — no external package needed.

> The key is generated per **attempt**, not per seat. Retrying a confirm-click is safe, but a user intentionally trying to book again is a separate request.

**Header is optional** — if omitted, it defaults to standard behavior. Legacy clients won't break.

---

## ⭐ Test caught a bug — `cost=0` peek

The brute-force test failed on the first run:

```
AssertionError: Brute force not stopped: [401]
```

Even after 12 incorrect passwords, no 429 was triggered.

**Reason:** Login uses a `cost=0` "peek" (check without consuming a token). The Lua script had:

```lua
if tokens >= cost then    -- cost = 0
```

The bucket was empty (`tokens = 0`), but `0 >= 0` is **true** — so the peek always allowed the request!

**Fix:**

```lua
local needed = cost
if cost == 0 then
    needed = 1        -- peek must also require at least 1 token
end

if tokens >= needed then
    tokens = tokens - cost    -- cost is 0, so nothing is subtracted
    allowed = 1
end
```

> A small bug, but it **rendered the entire brute-force protection useless**; manual testing might never have caught it. Automated tests caught it on the first run.

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

See the 200 in the middle? **That is the token bucket refill** — one second passed, 5 tokens were replenished.

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

# Same key again
curl -X POST ... -H "Idempotency-Key: $KEY" -d '{"seat_id":7}' ...
# {"id":146, ...}                       HTTP 201 + x-idempotent-replay: true

# Same key, DIFFERENT body
curl -X POST ... -H "Idempotency-Key: $KEY" -d '{"seat_id":8}' ...
# {"detail":"This Idempotency-Key was already used with different data"}   422
```

**Database:**
```sql
SELECT count(*) FROM bookings WHERE seat_id=7;
-- 1
```

Two requests, same booking ID, **one row**.

### 4. Test suite

```
20 passed in 22.74s
```

7 new tests: burst blocking, headers, **per-user isolation**, brute force, idempotent replay, fingerprint mismatch, and "works without header".

The most important is `test_rate_limit_is_per_user_not_global` — it drains one user's bucket to ensure **another user is unaffected**. A global limiter would shut down the entire system due to one bot.

### 5. Load test — did rate limiting break it?

```
Total requests   : 7,351
Failures         : 0
Requests/sec     : 124.0
p50 / p99        : 1,200 ms / 1,700 ms
```
```
✅ ALL PASS — no overselling
Seats: available=99, booked=1 · Bookings: confirmed=1
```

**Zero 429s** in the load test — because limits are per-user and every Locust user has their own account.

Throughput dropped from 137 to 124 rps (~9%). That is the cost of an extra Redis roundtrip per request. **This trade-off is worth it** — 9% throughput for bot protection.

---

## Interview Questions

| Question | Answer |
|---|---|
| "Which algorithm and why?" | Token bucket. Fixed window allows 2x bursts at boundaries; sliding log is memory-intensive. Token bucket allows natural bursts but stops sustained abuse. |
| "Why not limit by IP?" | The app is behind a proxy — IPs are unreliable, and `X-Forwarded-For` can be spoofed. NAT shares IPs across offices. IP limiting is an edge (nginx/Cloudflare) task; the app limits by identity. |
| "Why Lua?" | Read-modify-write race. In Python, a second request could read old tokens between GET and SET. |
| "What if Redis goes down?" | Fail-open — we allow requests. Fail-closed would take the site down. Rate limiting is protection, not correctness. |
| "Why idempotency if 409 was already working?" | 409 was a coincidence, not design. Users saw errors for successful bookings. This is insufficient for payments. |
| "What if the same key has a different body?" | 422. Silently returning the old response is wrong — it's a bug or attack, so we compare fingerprints. |
| "Did the load test break?" | No — limits are per-user, and each Locust user has their own account. Throughput dropped 9% due to the extra Redis roundtrip. |

---

## Common Problems

| Problem | Fix |
|---|---|
| All requests returning 429 | Run `reset_state.py` — clears old buckets. |
| Rate limit not working | Is `RATE_LIMIT_ENABLED=True` in `.env`? |
| 429s in load test | Users not seeded — they might all be using the same account. |
| Idempotency not working | Header name must be exactly `Idempotency-Key`. |
| "Request currently processing" stuck | Previous request crashed. It will clear in 60s, or run `reset_state.py`. |
| Login blocked after tests | `reset_state.py` now clears `rl:*` as well. |
| **Load test fails after pytest** | Brute-force test drained `user9`'s login bucket. Run `reset_state.py` — otherwise Locust's `on_start` login hits 429 and stops the run. |

---

## Files

```
backend/
├── rate_limit.py               ← new ⭐ token bucket (Lua) + dependencies
├── idempotency.py              ← new ⭐ SET NX claim + fingerprint + replay
├── config.py                   ← RATE_LIMIT_ENABLED
├── reset_state.py              ← now clears rl:* and idem:*
├── routers/
│   ├── seats.py                ← SEAT_LOCK limit on lock
│   ├── bookings.py             ← BOOKING limit + idempotency wrapper
│   └── auth.py                 ← login (per email, only on fail) + register
└── tests/test_concurrency.py   ← 7 new tests (13 → 20)

frontend/src/
├── api.js                      ← Idempotency-Key header, Retry-After parse
└── booking/BookingContext.jsx  ← friendly 429 message
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

- [Phase 4 — Redis Locking](04-redis-locking.md) — same Lua atomicity pattern
- [Phase 6 — Load Testing](06-load-testing.md) — load test
- [testing.md](../reference/testing.md) — all test commands
- [roadmap.md](../roadmap.md) — next steps
