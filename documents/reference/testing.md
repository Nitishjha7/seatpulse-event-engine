# Testing — Sab Kuch Ek Jagah

Har cheez kaise verify karni hai. Phase-wise details unki apni files me hain, ye quick reference hai.

---

## 0. Pehle ye — stack chal raha hai?

```bash
docker compose ps
```
**Paanch** containers `Up` dikhne chahiye — db, redis, backend, worker, frontend (`db` aur `redis` **healthy**).

```bash
curl http://localhost:8000/api/health
```
```json
{ "status": "healthy", "database": "connected", "redis": "connected", "version": "0.6.0" }
```

Kuch bhi galat lage to **sabse pehle logs**:
```bash
docker compose logs --tail=40 backend
```

---

## 1. Clean slate (test se pehle hamesha)

```bash
docker compose exec backend python reset_state.py
```
```
✅ N bookings hataye, 100 seats available ki
✅ N seat locks saaf kiye
✅ N rate limit buckets saaf kiye
✅ N idempotency keys saaf kiye
```

Data hi na ho (fresh DB):
```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

---

## 2. Automated tests — sabse tez tarika

```bash
docker compose exec backend pytest tests/ -v
```

```
tests/test_concurrency.py::test_health                              PASSED
tests/test_concurrency.py::test_protected_routes_need_a_token       PASSED
tests/test_concurrency.py::test_garbage_token_rejected              PASSED
tests/test_concurrency.py::test_login_wrong_password                PASSED
tests/test_concurrency.py::test_login_unknown_email_same_message    PASSED
tests/test_concurrency.py::test_refresh_rotates_and_old_token_dies  PASSED
tests/test_concurrency.py::test_logout_kills_refresh_token          PASSED
tests/test_concurrency.py::test_cannot_cancel_someone_elses_booking PASSED
tests/test_concurrency.py::test_only_one_user_gets_the_lock         PASSED
tests/test_concurrency.py::test_no_double_booking                   PASSED
tests/test_concurrency.py::test_lock_blocks_other_users_booking     PASSED
tests/test_concurrency.py::test_cannot_release_someone_elses_lock   PASSED
tests/test_concurrency.py::test_version_increments_on_change        PASSED

tests/test_concurrency.py::test_rate_limit_blocks_a_burst            PASSED
tests/test_concurrency.py::test_rate_limit_sends_headers            PASSED
tests/test_concurrency.py::test_rate_limit_is_per_user_not_global   PASSED
tests/test_concurrency.py::test_wrong_password_eventually_rate_limited PASSED
tests/test_concurrency.py::test_same_idempotency_key_returns_same_booking PASSED
tests/test_concurrency.py::test_same_key_different_body_is_rejected PASSED
tests/test_concurrency.py::test_booking_works_without_idempotency_key PASSED

============================= 20 passed in 22.43s ==============================
```

**Ek command me auth + poora concurrency logic verify ho jata hai.** Roz ke development me bas yahi chalao.

| Test | Kya check karta hai |
|---|---|
| `protected_routes_need_a_token` | Bina token ke booking/lock/bookings sab 401 |
| `garbage_token_rejected` | Nakli token se andar nahi ghus sakte |
| `login_unknown_email_same_message` | User enumeration nahi ho sakti (dono errors same) |
| `refresh_rotates_and_old_token_dies` | Refresh ke baad purana token bekaar |
| `logout_kills_refresh_token` | Logout Redis se token hata deta hai |
| `cannot_cancel_someone_elses_booking` | IDOR — dusre ki booking cancel nahi kar sakte |
| `rate_limit_blocks_a_burst` | 40 requests ek saath → kuch 429 |
| `rate_limit_is_per_user_not_global` | Ek user block hone se dusra affected nahi |
| `wrong_password_eventually_rate_limited` | Brute force protection |
| `same_idempotency_key_returns_same_booking` | Double-click → wahi booking, DB me ek row |
| `same_key_different_body_is_rejected` | 422, chupchap galat jawab nahi |
| `worker_generates_a_downloadable_ticket` | End-to-end: booking → worker → asli PDF |
| `cannot_download_someone_elses_ticket` | ⚠️ QR = entry pass. 404 |
| `qr_token_is_not_the_booking_id` | Sequential id QR me nahi honi chahiye |
| `attendee_cannot_touch_organizer_or_admin` | RBAC — role ke bina 403 |
| `organizer_cannot_touch_another_organizers_event` | ⭐ Ownership — role hone se resource tumhara nahi ho jata |
| `event_with_bookings_cannot_be_deleted` | Paid tickets kabhi gayab nahi honi chahiye |
| `organizer_creates_event_with_priced_rows` | Price tiers se seats sahi ban rahi hain |

---

## 3. Auth tests

### Bina token ke kuch nahi hota

```bash
curl -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" -d '{"seat_id":1}'
# {"detail":"Login karna zaroori hai"}

curl -X POST http://localhost:8000/api/seats/1/lock
curl http://localhost:8000/api/bookings
curl http://localhost:8000/api/auth/me
# sab 401
```

### ⭐ Token lo (aage ke saare tests isi se chalenge)

**Git Bash**
```bash
TOKEN=$(curl -s -c /tmp/ck.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"demo1234"}' \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

echo "token length: ${#TOKEN}"     # ~167 aana chahiye
```

> `-c /tmp/ck.txt` refresh cookie save karta hai — `/refresh` test ke liye chahiye.
> Host pe `python3` nahi hai isliye `sed` se nikal rahe hain.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/auth/me
# {"id":1000,"email":"demo@seatpulse.dev","full_name":"Demo User",...}
```

### Galat password — dono errors SAME hone chahiye

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"galat"}'
# {"detail":"Email ya password galat hai"}

curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"nahi@hai.dev","password":"kuchbhi"}'
# {"detail":"Email ya password galat hai"}     <- BILKUL same
```

> Alag messages dete to koi bhi email daal ke pata kar leta ki kaun registered hai (**user enumeration**).

### Refresh — cookie se naya access token

```bash
curl -b /tmp/ck.txt -c /tmp/ck.txt -X POST http://localhost:8000/api/auth/refresh
```
Naya `access_token` milega **aur cookie bhi badal jayegi** (rotation). Purani cookie ab 401 degi.

### Logout

```bash
curl -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/logout    # 204
curl -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/refresh   # ab 401
```

### Signup

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"naya@test.dev","password":"password123","full_name":"Naya User"}'
# 201 + turant logged in (access_token milta hai)

# Wahi email dubara -> 409
```

### Google OAuth

```bash
curl http://localhost:8000/api/auth/config
# {"google_enabled":true}      <- false ho to .env me credentials nahi hain

curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" \
  http://localhost:8000/api/auth/google/login
# 307 -> https://accounts.google.com/o/oauth2/v2/auth?client_id=...&state=...
```

Poora flow browser me hi test hota hai (redirect chahiye) — login page pe **Continue with Google** dabao.

> Credentials kaise banate hain: [Phase 7 — Auth + Google OAuth](../phases/07-auth-google-oauth.md) → "Google credentials kaise banayein"

---

## 3b. Rate limiting aur idempotency

### Burst — 429 aana chahiye

```bash
for i in $(seq 1 25); do
  curl -s -o /dev/null -w "%{http_code} " -X POST \
    -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/seats/5/lock
done; echo
```
Expected: pehli ~15 `200`, phir `429` (beech me kabhi `200` — wo refill hai).

### Headers

```bash
curl -s -D - -o /dev/null -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock | grep -iE "^(HTTP|x-ratelimit|retry-after)"
```
```
HTTP/1.1 429 Too Many Requests
retry-after: 1
x-ratelimit-limit: 15
x-ratelimit-remaining: 0
```

### Brute force

```bash
for i in $(seq 1 12); do
  curl -s -o /dev/null -w "%{http_code} " -X POST http://localhost:8000/api/auth/login \
    -H "Content-Type: application/json" \
    -d '{"email":"user9@seatpulse.dev","password":"galat"}'
done; echo
```
Expected: `401` × ~5, phir `429`.

> Sahi password se login **kabhi** limit me nahi phasta — budget sirf galat attempts pe kharch hota hai.

### ⭐ Idempotency

```bash
KEY="test-$(date +%s)"

# Pehli baar
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":7}' http://localhost:8000/api/bookings

# WAHI key dubara — wahi id aani chahiye
curl -s -D - -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":7}' http://localhost:8000/api/bookings | grep -iE "x-idempotent|\"id\""

# Wahi key, ALAG body — 422
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":8}' http://localhost:8000/api/bookings
```

**Asli proof DB me:**
```bash
docker compose exec db psql -U seatpulse -d seatpulse -t -c \
  "SELECT count(*) FROM bookings WHERE seat_id=7;"
# 1
```

### Redis me dekho

```bash
docker compose exec redis redis-cli KEYS "rl:*"      # rate limit buckets
docker compose exec redis redis-cli KEYS "idem:*"    # idempotency keys
docker compose exec redis redis-cli HGETALL "rl:user:1000"
```

---

## 4. Manual API tests

### Public endpoints (token nahi chahiye)

```bash
curl http://localhost:8000/api/stats
# {"events":1,"seats_total":100,"seats_by_status":{"available":100}}

curl http://localhost:8000/api/events
curl http://localhost:8000/api/events/1
curl http://localhost:8000/api/events/1/seats
```

### Lock flow (token chahiye)

```bash
# Lock lo
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock
# {"seat_id":5,"locked_by":1000,"expires_in":300,...}

# Kiske paas hai (public)
curl http://localhost:8000/api/seats/5/lock

# Chhodo
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock
```

### Booking

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"seat_id":5}' \
  http://localhost:8000/api/bookings
# 201

# Dubara -> 409
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"seat_id":5}' \
  http://localhost:8000/api/bookings
# {"detail":"Seat pehle se booked hai"}

# Meri bookings
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/bookings
```

> ⚠️ Note: `user_id` **kahin nahi** ja raha. Pehle body me jata tha — koi bhi kisi aur ke naam booking kar sakta tha. Ab token se aata hai.

### Swagger
http://localhost:8000/docs → upar **Authorize** button → `Bearer <token>` daalo → phir har endpoint "Try it out" se chalega.

---

## 5. Concurrency — quick manual test

Ab har "user" ka apna **token** chahiye. Pehle 6 alag users ke tokens le lo:

**Git Bash**
```bash
# user1..user6 ke tokens ek array me
TOKENS=()
for u in 1 2 3 4 5 6; do
  TOKENS+=("$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"user$u@seatpulse.dev\",\"password\":\"demo1234\"}" \
    | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')")
done
echo "${#TOKENS[@]} tokens mile"
```

**6 users, ek seat pe lock** (`&` se parallel, `wait` se sabka intezaar):
```bash
i=1
for t in "${TOKENS[@]}"; do
  curl -s -o /dev/null -w "user$i -> %{http_code}\n" \
    -X POST -H "Authorization: Bearer $t" \
    http://localhost:8000/api/seats/10/lock &
  i=$((i+1))
done; wait
```
Expected: ek `200`, paanch `409`

**6 users, ek seat book karein:**
```bash
for t in "${TOKENS[@]}"; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST -H "Authorization: Bearer $t" \
    -H "Content-Type: application/json" -d '{"seat_id":7}' \
    http://localhost:8000/api/bookings &
done; wait
```
Expected: ek `201`, paanch `409`

> ⚠️ Har user ka **alag token** hona zaroori hai. Same user dubara lock maange to `already_owned` wala 200 milta hai aur result jhootha lagta hai.
>
> Zyada users chahiye to `pytest tests/ -v` chalao — wo 40 users ke saath yahi karta hai.

---

## 6. Lua script test (dusre ka lock nahi hatta)

```bash
# TOKENS array section 5 me banaya tha
curl -X POST -H "Authorization: Bearer ${TOKENS[0]}" \
  http://localhost:8000/api/seats/12/lock

# Dusra user release karne ki koshish kare
curl -X DELETE -H "Authorization: Bearer ${TOKENS[1]}" \
  http://localhost:8000/api/seats/12/lock
# {"released": false}      <- Lua script ne roka

# Lock abhi bhi pehle user ke paas
docker compose exec redis redis-cli get "seat:12:lock"
```

**Ye Lua script ka asli proof hai.** Seedha `DEL` hota to dusre ka lock ud jata.

---

## 7. TTL auto-expiry

```bash
docker compose exec redis redis-cli set "seat:99:lock" "3" EX 5
docker compose exec redis redis-cli ttl "seat:99:lock"      # 4
sleep 6
docker compose exec redis redis-cli exists "seat:99:lock"   # 0
```

---

## 8. WebSocket / real-time

### Do browser windows (sabse aasan)

Ek normal, ek incognito — dono http://localhost:5173

**Ab dono me login karna padega.** Aur behtar test ke liye **alag-alag accounts** use karo:
- Window A: `demo@seatpulse.dev` / `demo1234`
- Window B: `user1@seatpulse.dev` / `demo1234`

Alag users se hi asli baat dikhti hai — A ki hold B ko **peeli** dikhegi (apni hold hoti to neeli dikhti).

| Window A me | Window B me turant |
|---|---|
| Hari seat click | **Peeli** (kisi aur ki hold) |
| Release Hold | **Hari** |
| Confirm Booking | **Laal**, counts badle |
| Cancel | **Hari** |

Refresh bilkul nahi karna.

### Bina token ke WebSocket connect nahi hota

```bash
# Browser console me:
new WebSocket("ws://localhost:8000/ws/events/1")
# turant band ho jayega — code 1008 "Authentication required"
```
Backend logs me `connection rejected (403 Forbidden)` dikhega.

### Redis pub/sub live

```bash
docker compose exec redis redis-cli psubscribe "seatpulse:event:*"
```
Ab UI me seat click karo — raw JSON messages behte dikhenge.

### Reconnect
```bash
docker compose restart backend
```
Header badge: **Live → Offline → Connecting → Live** (page refresh kiye bina)

### DevTools
F12 → Network → **WS** → `/ws/events/1` → Messages

---

## 9. Load test

```bash
# 1. Users (ek baar kaafi hai)
docker compose exec backend python seed.py

# 2. Clean state
docker compose exec backend python reset_state.py

# 3. Flash sale — 200 users, ek seat
docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 200 -r 15 -t 60s \
    --host http://backend:8000

# 4. ⭐ Verify (ye step SABSE important hai)
docker compose exec backend python verify_integrity.py
```

**Expected:**
```
Total requests   : ~8000
Failures         : 0
Requests/sec     : ~137
p50 / p99        : ~1000 ms / ~1400 ms
```
Aur DB me **exactly 1** confirmed booking.

> ⚠️ **Spawn rate (`-r`) dheema rakhna** — har Locust user pehle login karta hai, aur bcrypt jaan-boojh ke ~100ms leta hai. `-r 100` doge to poora test sirf logins me nikal jayega (66 requests aayi thi ek baar).

**Realistic load (response times ke liye):**
```bash
docker compose --profile loadtest run --rm locust \
    -f locustfile.py BrowsingUser --headless -u 50 -r 10 -t 40s \
    --host http://backend:8000
```

**Web UI (graphs, screenshot ke liye):**
```bash
docker compose --profile loadtest up locust
```
→ http://localhost:8089

> ⚠️ **Locust ka "0 failures" kaafi nahi hai.** Wo dono requests ko 201 de sakta hai aur dono ko success gin sakta hai. **Hamesha `verify_integrity.py` chalao** — asli sach database me hai.

### 500 errors aayein to

```bash
docker compose logs --tail=400 backend 2>&1 | grep -iE "Error" | sort | uniq -c | sort -rn | head
```

`QueuePool limit ... reached` dikhe to connection pool khatam ho raha hai. Live dekho ki connections kya kar rahe hain:

```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
"SELECT count(*) total,
        count(*) FILTER (WHERE state='idle in transaction') idle_txn,
        count(*) FILTER (WHERE state='active') active
 FROM pg_stat_activity WHERE datname='seatpulse';"
```

**`idle_txn` zyada aur `active` kam** = connections pakde hue hain par kaam nahi kar rahe. Invariant check karo:

```
MAX_CONCURRENT_REQUESTS (30)  <  pool_size + max_overflow (40)
```

Poori kahani: [Phase 7 — Auth + Google OAuth](../phases/07-auth-google-oauth.md) → "Auth ne load test todha"

---

## 10. Database verify

```bash
docker compose exec backend python verify_integrity.py
```
```
✅ Koi seat do baar nahi biki
✅ Seat status aur bookings match karte hain
✅ Koi booked seat bina booking ke nahi
✅ Koi booking bina booked seat ke nahi
```

### Manually

```bash
docker compose exec db psql -U seatpulse -d seatpulse
```
```sql
-- Overselling hui?
SELECT seat_id, count(*) FROM bookings
WHERE status = 'confirmed' GROUP BY seat_id HAVING count(*) > 1;
-- 0 rows aane chahiye

SELECT status, count(*) FROM seats GROUP BY status;
SELECT * FROM bookings ORDER BY created_at DESC LIMIT 10;

-- Constraints maujood hain?
\d seats
\d bookings
```

### Constraint tod ke dekho
```sql
INSERT INTO seats (event_id, row_label, seat_number, price, status, version)
VALUES (1, 'A', 1, 100, 'available', 0);
-- ERROR: duplicate key value violates unique constraint "uq_seat_position"

UPDATE seats SET status = 'Booked' WHERE id = 1;
-- ERROR: violates check constraint "ck_seat_status"
```
**Error aana achhi baat hai** — DB khud galat data rok raha hai.

---

## 11. Redis andar se

```bash
docker compose exec redis redis-cli
```
```
KEYS seat:*              # saare locks
GET seat:10:lock         # kiske paas
TTL seat:10:lock         # kitna time bacha
MONITOR                  # live commands — phir UI me seat click karo
```

---

## Full check — sab ek saath

Sab kuch verify karna ho (commit se pehle, ya demo se pehle):

```bash
docker compose ps
curl -s http://localhost:8000/api/health
curl -s http://localhost:8000/api/auth/config

docker compose exec backend python reset_state.py
docker compose exec backend pytest tests/ -v

# ⚠️ Phir se reset — brute-force test rate-limit buckets chhod jata hai,
# aur Locust ka login unhi buckets me phas jata hai
docker compose exec backend python reset_state.py

docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 200 -r 15 -t 60s \
    --host http://backend:8000
docker compose exec backend python verify_integrity.py

docker compose exec backend python reset_state.py
```

Sab green = demo ke liye ready.

Aakhir me browser me ek round: login → seat hold → book → cancel → logout.

---

## Cheat Sheet

| Kya test karna hai | Command |
|---|---|
| Sab theek hai? | `curl http://localhost:8000/api/health` |
| Google on hai? | `curl http://localhost:8000/api/auth/config` |
| **Auth + rate limit + concurrency (sab kuch)** | `docker compose exec backend pytest tests/ -v` |
| Rate limit buckets | `docker compose exec redis redis-cli KEYS "rl:*"` |
| Idempotency keys | `docker compose exec redis redis-cli KEYS "idem:*"` |
| Data sahi hai? | `docker compose exec backend python verify_integrity.py` |
| Fresh state | `docker compose exec backend python reset_state.py` |
| Load test | `docker compose --profile loadtest run --rm locust -f locustfile.py FlashSaleUser --headless -u 200 -r 15 -t 60s --host http://backend:8000` |
| Real-time | Do browser windows, **alag accounts** se |
| Redis locks | `docker compose exec redis redis-cli KEYS "seat:*"` |
| Refresh tokens | `docker compose exec redis redis-cli KEYS "refresh:*"` |
| DB connections | `docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT state, count(*) FROM pg_stat_activity WHERE datname='seatpulse' GROUP BY state;"` |
| Worker chal raha hai? | `docker compose logs -f worker` |
| Stuck tickets re-queue | `docker compose exec backend python retry_pending_tickets.py` |
| Bheji hui emails | `docker compose exec worker ls /app/tickets/outbox/` |
| Logs | `docker compose logs -f backend` |

---

## Demo accounts

| Email | Password | Role |
|---|---|---|
| `demo@seatpulse.dev` | `demo1234` | attendee |
| `organizer@seatpulse.dev` | `demo1234` | organizer |
| `admin@seatpulse.dev` | `demo1234` | admin |
| `user1@seatpulse.dev` … `user499@seatpulse.dev` | `demo1234` | attendee |

`seed.py` 500 users banata hai — load test me har concurrent user ka apna account chahiye hota hai.

---

## Related

- [Phase 7 — Auth + Google OAuth](../phases/07-auth-google-oauth.md) — auth design + **Google credentials kaise banayein**
- [Phase 6 — Load Testing](../phases/06-load-testing.md) — load testing ka poora detail + results
- [postgres-commands.md](postgres-commands.md) — DB queries
- [docker-commands.md](docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — poora plan
