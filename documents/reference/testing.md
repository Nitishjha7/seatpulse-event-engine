# Testing — Centralized Reference

How to verify everything. Phase-specific details are in their respective files; this is a quick reference.

---

## 0. Prerequisites — Is the stack running?

```bash
docker compose ps
```
**Five** containers should show as `Up` — db, redis, backend, worker, frontend (`db` and `redis` must be **healthy**).

```bash
curl http://localhost:8000/api/health
```
```json
{ "status": "healthy", "database": "connected", "redis": "connected", "version": "0.6.0" }
```

If anything seems wrong, **check logs first**:
```bash
docker compose logs --tail=40 backend
```

---

## 1. Clean slate (always before testing)

```bash
docker compose exec backend python reset_state.py
```
```
✅ N bookings removed, 100 seats made available
✅ N seat locks cleared
✅ N rate limit buckets cleared
✅ N idempotency keys cleared
```

For a fresh database:
```bash
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

---

## 2. Automated tests — The fastest method

```bash
docker compose exec backend pytest tests/ -v
```

```
tests/test_auth.py::test_health                              PASSED
tests/test_auth.py::test_protected_routes_need_a_token       PASSED
tests/test_auth.py::test_garbage_token_rejected              PASSED
tests/test_auth.py::test_login_wrong_password                PASSED
tests/test_auth.py::test_login_unknown_email_same_message    PASSED
tests/test_auth.py::test_refresh_rotates_and_old_token_dies  PASSED
tests/test_auth.py::test_logout_kills_refresh_token          PASSED
tests/test_auth.py::test_cannot_cancel_someone_elses_booking PASSED
tests/test_concurrency.py::test_only_one_user_gets_the_lock         PASSED
tests/test_concurrency.py::test_no_double_booking                   PASSED
tests/test_concurrency.py::test_lock_blocks_other_users_booking     PASSED
tests/test_concurrency.py::test_cannot_release_someone_elses_lock   PASSED
tests/test_concurrency.py::test_version_increments_on_change        PASSED

tests/test_concurrency.py::test_rate_limit_blocks_a_burst            PASSED
tests/test_rate_limit_sends_headers                                 PASSED
tests/test_rate_limit_is_per_user_not_global                        PASSED
tests/test_wrong_password_eventually_rate_limited                   PASSED
tests/test_same_idempotency_key_returns_same_booking                PASSED
tests/test_same_key_different_body_is_rejected                      PASSED
tests/test_booking_works_without_idempotency_key                    PASSED

============================= 20 passed in 22.43s ==============================
```

**One command verifies auth + full concurrency logic.** Run this during daily development.

| Test | Checks |
|---|---|
| `protected_routes_need_a_token` | Booking/lock/bookings return 401 without token |
| `garbage_token_rejected` | Invalid tokens are rejected |
| `login_unknown_email_same_message` | Prevents user enumeration (identical error messages) |
| `refresh_rotates_and_old_token_dies` | Old token invalidated after refresh |
| `logout_kills_refresh_token` | Logout removes token from Redis |
| `cannot_cancel_someone_elses_booking` | IDOR — cannot cancel others' bookings |
| `rate_limit_blocks_a_burst` | 40 requests at once → some 429 |
| `rate_limit_is_per_user_not_global` | One user blocked does not affect others |
| `wrong_password_eventually_rate_limited` | Brute force protection |
| `same_idempotency_key_returns_same_booking` | Double-click → same booking, one DB row |
| `same_key_different_body_is_rejected` | 422, no silent failure |
| `worker_generates_a_downloadable_ticket` | End-to-end: booking → worker → PDF |
| `cannot_download_someone_elses_ticket` | ⚠️ QR = entry pass. 404 |
| `qr_token_is_not_the_booking_id` | QR must not contain sequential IDs |
| `same_qr_cannot_be_used_twice` | ⭐ One ticket, one entry |
| `concurrent_scans_admit_exactly_one` | ⭐ 10 gates simultaneously → 1 entry |
| `invalid_token_is_rejected` | No details leaked (prevents brute-force) |
| `attendee_cannot_touch_organizer_or_admin` | RBAC — 403 without role |
| `organizer_cannot_touch_another_organizers_event` | ⭐ Ownership — role does not grant access to all resources |
| `event_with_bookings_cannot_be_deleted` | Paid tickets must never disappear |
| `organizer_creates_event_with_priced_rows` | Price tiers correctly generate seats |

---

## 3. Auth tests

### Nothing works without a token

```bash
curl -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" -d '{"seat_id":1}'
# {"detail":"Login required"}

curl -X POST http://localhost:8000/api/seats/1/lock
curl http://localhost:8000/api/bookings
curl http://localhost:8000/api/auth/me
# all 401
```

### ⭐ Get a token (required for subsequent tests)

**Git Bash**
```bash
TOKEN=$(curl -s -c /tmp/ck.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"demo1234"}' \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

echo "token length: ${#TOKEN}"     # should be ~167
```

> `-c /tmp/ck.txt` saves the refresh cookie — required for `/refresh` testing.
> Using `sed` because `python3` is not available on the host.

```bash
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/auth/me
# {"id":1000,"email":"demo@seatpulse.dev","full_name":"Demo User",...}
```

### Wrong password — errors must be IDENTICAL

```bash
curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"wrong"}'
# {"detail":"Incorrect email or password"}

curl -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"not@exists.dev","password":"anything"}'
# {"detail":"Incorrect email or password"}     <- IDENTICAL
```

> Different messages would allow user enumeration.

### Refresh — new access token via cookie

```bash
curl -b /tmp/ck.txt -c /tmp/ck.txt -X POST http://localhost:8000/api/auth/refresh
```
Returns a new `access_token` and rotates the cookie. The old cookie will now return 401.

### Logout

```bash
curl -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/logout    # 204
curl -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/refresh   # now 401
```

### Signup

```bash
curl -X POST http://localhost:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"new@test.dev","password":"password123","full_name":"New User"}'
# 201 + logged in (returns access_token)

# Duplicate email -> 409
```

### Google OAuth

```bash
curl http://localhost:8000/api/auth/config
# {"google_enabled":true}      <- false if credentials missing in .env

curl -s -o /dev/null -w "%{http_code} -> %{redirect_url}\n" \
  http://localhost:8000/api/auth/google/login
# 307 -> https://accounts.google.com/o/oauth2/v2/auth?client_id=...&state=...
```

Full flow tested in browser (requires redirect) — click **Continue with Google** on the login page.

---

## 3b. Rate limiting and idempotency

### Burst — expect 429

```bash
for i in $(seq 1 25); do
  curl -s -o /dev/null -w "%{http_code} " -X POST \
    -H "Authorization: Bearer $TOKEN" \
    http://localhost:8000/api/seats/5/lock
done; echo
```
Expected: first ~15 `200`, then `429` (refills occur intermittently).

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
    -d '{"email":"user9@seatpulse.dev","password":"wrong"}'
done; echo
```
Expected: `401` × ~5, then `429`.

> Correct passwords never trigger rate limits — budget is only consumed by failed attempts.

### ⭐ Idempotency

```bash
KEY="test-$(date +%s)"

# First attempt
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":7}' http://localhost:8000/api/bookings

# Same key again — should return same ID
curl -s -D - -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":7}' http://localhost:8000/api/bookings | grep -iE "x-idempotent|\"id\""

# Same key, different body — 422
curl -s -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -H "Idempotency-Key: $KEY" \
  -d '{"seat_id":8}' http://localhost:8000/api/bookings
```

**DB verification:**
```bash
docker compose exec db psql -U seatpulse -d seatpulse -t -c \
  "SELECT count(*) FROM bookings WHERE seat_id=7;"
# 1
```

---

## 4. Manual API tests

### Public endpoints (no token required)

```bash
curl http://localhost:8000/api/stats
# {"events":1,"seats_total":100,"seats_by_status":{"available":100}}

curl http://localhost:8000/api/events
curl http://localhost:8000/api/events/1
curl http://localhost:8000/api/events/1/seats
```

### Lock flow (token required)

```bash
# Lock
curl -X POST -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock
# {"seat_id":5,"locked_by":1000,"expires_in":300,...}

# Check status (public)
curl http://localhost:8000/api/seats/5/lock

# Release
curl -X DELETE -H "Authorization: Bearer $TOKEN" \
  http://localhost:8000/api/seats/5/lock
```

### Booking

```bash
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"seat_id":5}' \
  http://localhost:8000/api/bookings
# 201

# Duplicate -> 409
curl -X POST -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" -d '{"seat_id":5}' \
  http://localhost:8000/api/bookings
# {"detail":"Seat already booked"}

# My bookings
curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/bookings
```

> ⚠️ Note: `user_id` is no longer passed in the body; it is derived from the token.

---

## 5. Concurrency — quick manual test

Requires unique tokens for each "user". Generate 6 tokens:

**Git Bash**
```bash
TOKENS=()
for u in 1 2 3 4 5 6; do
  TOKENS+=("$(curl -s -X POST http://localhost:8000/api/auth/login \
    -H "Content-Type: application/json" \
    -d "{\"email\":\"user$u@seatpulse.dev\",\"password\":\"demo1234\"}" \
    | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')")
done
echo "${#TOKENS[@]} tokens obtained"
```

**6 users, locking one seat:**
```bash
i=1
for t in "${TOKENS[@]}"; do
  curl -s -o /dev/null -w "user$i -> %{http_code}\n" \
    -X POST -H "Authorization: Bearer $t" \
    http://localhost:8000/api/seats/10/lock &
  i=$((i+1))
done; wait
```
Expected: one `200`, five `409`.

**6 users, booking one seat:**
```bash
for t in "${TOKENS[@]}"; do
  curl -s -o /dev/null -w "%{http_code}\n" \
    -X POST -H "Authorization: Bearer $t" \
    -H "Content-Type: application/json" -d '{"seat_id":7}' \
    http://localhost:8000/api/bookings &
done; wait
```
Expected: one `201`, five `409`.

---

## 6. Lua script test (cannot release others' locks)

```bash
# Using tokens from section 5
curl -X POST -H "Authorization: Bearer ${TOKENS[0]}" \
  http://localhost:8000/api/seats/12/lock

# Attempt release by different user
curl -X DELETE -H "Authorization: Bearer ${TOKENS[1]}" \
  http://localhost:8000/api/seats/12/lock
# {"released": false}      <- Blocked by Lua script

# Lock still held by first user
docker compose exec redis redis-cli get "seat:12:lock"
```

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

### Two browser windows
Use one normal, one incognito — both at http://localhost:5173.
Use **different accounts**:
- Window A: `demo@seatpulse.dev`
- Window B: `user1@seatpulse.dev`

| Window A | Window B |
|---|---|
| Click green seat | **Yellow** (held by other) |
| Release Hold | **Green** |
| Confirm Booking | **Red**, counts update |
| Cancel | **Green** |

---

## 9. Load test

```bash
# 1. Seed users
docker compose exec backend python seed.py

# 2. Clean state
docker compose exec backend python reset_state.py

# 3. Flash sale — 200 users, one seat
docker compose --profile loadtest run --rm locust \
    -f locustfile.py FlashSaleUser --headless -u 200 -r 15 -t 60s \
    --host http://backend:8000

# 4. ⭐ Verify
docker compose exec backend python verify_integrity.py
```

**Expected:**
```
Total requests   : ~8000
Failures         : 0
Requests/sec     : ~137
p50 / p99        : ~1000 ms / ~1400 ms
```
And **exactly 1** confirmed booking in the DB.

---

## 10. Database verification

```bash
docker compose exec backend python verify_integrity.py
```
```
✅ No seat sold twice
✅ Seat status and bookings match
✅ No booked seat without a booking
✅ No booking without a booked seat
```

---

## 11. Redis internals

```bash
docker compose exec redis redis-cli
```
```
KEYS seat:*              # all locks
GET seat:10:lock         # owner
TTL seat:10:lock         # remaining time
MONITOR                  # live commands
```

---

## Cheat Sheet

| Test | Command |
|---|---|
| Health check | `curl http://localhost:8000/api/health` |
| Auth + Concurrency | `docker compose exec backend pytest tests/ -v` |
| Data integrity | `docker compose exec backend python verify_integrity.py` |
| Reset state | `docker compose exec backend python reset_state.py` |
| Load test | `docker compose --profile loadtest run --rm locust -f locustfile.py FlashSaleUser --headless -u 200 -r 15 -t 60s --host http://backend:8000` |
| Redis locks | `docker compose exec redis redis-cli KEYS "seat:*"` |
| DB connections | `docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT state, count(*) FROM pg_stat_activity WHERE datname='seatpulse' GROUP BY state;"` |
| Worker logs | `docker compose logs -f worker` |
