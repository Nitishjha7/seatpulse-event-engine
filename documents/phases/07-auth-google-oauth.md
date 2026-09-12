# Phase 7 — JWT Auth + Google OAuth

Follows [Phase 6 — Load Testing](06-load-testing.md).

**Fixes implemented:** Up to Phase 6, `POST /api/bookings` accepted `user_id` in the request body. This allowed any user to book on behalf of others:

```json
{ "seat_id": 5, "user_id": 7 }
```

An interviewer could easily exploit this via `/docs`. Now, the user identity is derived from the token.

---

## Token strategy (Design decision — frequently asked in interviews)

| Token | Storage | Duration | Purpose |
|---|---|---|---|
| **Access** | React memory (RAM) | 30 min | `Authorization: Bearer` header for every API call |
| **Refresh** | httpOnly cookie | 7 days | Exclusively for obtaining a new access token |

### Why not localStorage?

`localStorage` is accessible by **any JavaScript** — including XSS, malicious npm packages, or browser extensions. `httpOnly` cookies are inaccessible to JavaScript.

### Why not use cookies for everything?

Cookies are sent automatically with every request, which exposes the application to **CSRF**. Therefore:

- **Access token handles primary operations** — via the `Authorization` header, which is not automatically included in CSRF attacks.
- **Cookie is for refresh only** — restricted to `path=/api/auth` and `samesite=lax`.

### What happens to the RAM-based access token on reload?

It is cleared. To handle this, the app triggers a `POST /api/auth/refresh` immediately upon mounting. If the cookie is valid, the session is restored seamlessly.

This same mechanism is used after **Google login** (see below).

---

## Refresh token revocation — Redis whitelist

JWTs are stateless; once issued, they remain valid until expiry. This makes **logout** ineffective without a revocation mechanism.

Each refresh token contains a `jti` (unique ID) whitelisted in Redis:

```python
jti = uuid.uuid4().hex
redis_client.setex(f"refresh:{user_id}:{jti}", timedelta(days=7), "1")
```

- **Logout** → Delete the key → Token becomes invalid immediately.
- **Redis TTL** = Token expiry → Entries expire automatically; no cleanup job required.
- **Logout-all** → `scan_iter(f"refresh:{user_id}:*")` → Revokes access across all devices.

### Rotation

Upon calling `/refresh`, the old token is **immediately revoked** and a new one is issued.

Benefit: If a token is stolen and used, the legitimate user's token will be invalidated, forcing a logout and exposing the theft.

---

## Password hashing — bcrypt

```python
bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

**bcrypt is intentionally slow (~100ms).** Fast hashes like SHA256 are unsuitable here, as attackers could perform millions of guesses per second. bcrypt makes brute force practically impossible. Salting is handled automatically.

> The "slow by design" nature impacted load testing — see the "Authentication impacted load testing" section below.

---

## Google OAuth — Authorization Code flow

```
1. User clicks "Continue with Google"
   -> Browser navigates to backend /api/auth/google/login

2. Backend redirects user to Google (with a random `state` parameter)

3. User logs in to Google and grants permissions

4. Google redirects user back to /api/auth/google/callback with a `code`

5. ⭐ BACKEND sends the code to Google (with client_secret)
   and retrieves user info — SERVER-TO-SERVER, bypassing the browser

6. Backend sets the refresh cookie and redirects to the frontend
```

### Why this flow?

| Question | Answer |
|---|---|
| Why not the "Implicit" flow? | It exposed tokens directly in the URL, leaking them into browser history and server logs. |
| Why not frontend-only OAuth? | It would expose the `client_secret` to the browser, where it could be stolen. |
| What is `state` for? | CSRF protection — a random string stored in Redis; Google returns it for verification. |
| Why not send access token in URL? | Same reason — history/logs. We set a cookie and redirect; the frontend fetches the token via `/refresh`. |

### Matching on `google_id`, not email

```python
user = db.scalar(select(User).where(User.google_id == google_id))
```

Users can change their Google email, but the `sub` (`google_id`) is immutable.

If an account with the same email already exists, we link it rather than creating a duplicate.

### Google users have NULL passwords

```python
hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

The migration makes this column nullable. `verify_password()` returns `False` for `None`.

---

## How to create Google credentials

1. [console.cloud.google.com](https://console.cloud.google.com) → **New Project** → Name: `SeatPulse`

2. **APIs & Services → OAuth consent screen**
   - User Type: **External**
   - Fill in App name, support email, and developer email.
   - Scopes: `userinfo.email` and `userinfo.profile`.
   - **Test users**: Add your Gmail (only these users can log in until published).

3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Type: **Web application**
   - **Authorized redirect URIs**:
     ```
     http://localhost:8000/api/auth/google/callback
     ```

4. Add Client ID and Secret to `backend/.env`.

> ⚠️ The Redirect URI must be **exact** — even an extra slash causes `redirect_uri_mismatch`. Use port **8000** (backend), not 5173.

> `.env` is gitignored. If credentials are accidentally pushed, **revoke them immediately** in the Google Console and generate new ones.

**If credentials are missing?** Leave `GOOGLE_CLIENT_ID` empty — the Google button will be hidden automatically (`/api/auth/config` handles this), and email/password login will continue to function.

---

## Endpoints

| Method | Route | Purpose |
|---|---|---|
| GET | `/api/auth/config` | Determine if Google button should be shown |
| POST | `/api/auth/register` | Create account (auto-login after signup) |
| POST | `/api/auth/login` | Email + password login |
| POST | `/api/auth/refresh` | Get new access token via cookie (with rotation) |
| POST | `/api/auth/logout` | Logout current device |
| POST | `/api/auth/logout-all` | Logout all devices |
| GET | `/api/auth/me` | Get current user |
| GET | `/api/auth/google/login` | Redirect to Google |
| GET | `/api/auth/google/callback` | Callback from Google |

---

## Security fixes implemented

| Feature | Before | After |
|---|---|---|
| Booking ownership | `user_id` in body — spoofable | Derived from token |
| Seat lock ownership | `user_id` in body | Derived from token |
| `GET /api/bookings` | `?user_id=` — exposed all bookings | Only own bookings |
| `DELETE /api/bookings/{id}` | **IDOR vulnerability** | Ownership check |
| Login error message | Specific errors | Generic "Invalid email or password" (prevents enumeration) |
| WebSocket | Open to all | Requires token (`?token=`) |

### IDOR fix: 404 instead of 403

```python
if booking.user_id != user.id:
    raise HTTPException(404, "Booking not found")
```

Returning 403 would confirm the booking exists. 404 provides no information to an attacker.

### WebSocket auth via query param

The browser WebSocket API **does not support custom headers**. Hence, `?token=...`.

Trade-off: The token may appear in server logs. Therefore, we only send a **short-lived access token** (30 min), never the refresh token.

---

## Frontend

Three key additions: token storage, 401 handling, and the login page.

### `api.js` — token + automatic retry

**Token is stored in a module-level variable, not `localStorage`:**

```js
let accessToken = null;            // RAM only

export function setAccessToken(token) { accessToken = token; }
export function getAccessToken()      { return accessToken; }
```

This clears on reload, which is intended. We restore it via the cookie on reload.

**Automatic retry on 401:**

```js
async function request(path, options = {}, { retry = true } = {}) {
  let res = await rawRequest(path, options, accessToken);

  if (res.status === 401 && retry && !path.startsWith("/api/auth/")) {
    const refreshed = await tryRefresh();
    if (refreshed) res = await rawRequest(path, options, accessToken);
  }
  ...
}
```

| Feature | Why |
|---|---|
| `retry` flag | Limit to one retry to prevent infinite loops. |
| `!path.startsWith("/api/auth/")` | 401 on `/login` means "wrong password"; retrying is useless. |
| `credentials: "include"` | Required for cookies to be sent/set (cross-origin 5173 → 8000). |

**Benefit:** If the access token expires while the user is idle, the app silently refreshes it without forcing a re-login.

### `AuthContext.jsx` — session management

**Restore session on mount:**

```js
useEffect(() => {
  async function boot() {
    const config = await api.getAuthConfig()
    setGoogleEnabled(config.google_enabled)

    const data = await api.refreshSession()
    if (data) applySession(data)

    setLoading(false)
  }
  boot()
}, [applySession])
```

> ⚠️ **`loading` state is mandatory.** Without it, the login page flashes briefly because `user` is null before the refresh completes.

**Silent refresh — 1 minute before expiry:**

```js
const scheduleRefresh = useCallback((expiresIn) => {
  clearTimeout(refreshTimer.current)
  const delay = Math.max((expiresIn - 60) * 1000, 10_000)

  refreshTimer.current = setTimeout(async () => {
    const data = await api.refreshSession()
    if (data) {
      setUser(data.user)
      scheduleRefresh(data.expires_in)
    } else {
      setUser(null)
    }
  }, delay)
}, [])
```

| Feature | Why |
|---|---|
| `expiresIn - 60` | Refresh 1 minute early to avoid expiration. |
| `Math.max(..., 10_000)` | Ensure at least 10s gap to prevent refresh loops. |

### `App.jsx` — auth gate

```jsx
if (authLoading) return <Loading />
if (!isAuthenticated) return <AuthPage />

return <BookingApp key={user.id} />
```

> **Note `key={user.id}`.** This forces React to re-render the component tree when the user changes, preventing data leakage between sessions.

### `AuthPage.jsx`

Handles both login and signup via a `mode` state.

**Demo button:**

```jsx
<button onClick={() => {
  setEmail('demo@seatpulse.dev')
  setPassword('demo1234')
}}>
  demo@seatpulse.dev / demo1234
</button>
```

Allows recruiters to log in with one click.

---

## ⭐ Authentication impacted load testing — and revealed two bugs

After adding auth, the load test failed:

```
Total requests   : 1250
Failures         : 58        <- 500 Internal Server Error
Requests/sec     : 30.3
p99              : 21000 ms
```

Logs:
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 30 reached,
connection timed out, timeout 10.00
```

### Bug 1 — bcrypt held transactions open

Postgres analysis:
```sql
SELECT count(*) total,
       count(*) FILTER (WHERE state='idle in transaction') idle_txn,
       count(*) FILTER (WHERE state='active') active
FROM pg_stat_activity WHERE datname='seatpulse';
```

**50 connections were "idle in transaction".** SQLAlchemy opened a transaction on the first query, which remained open during the ~100ms bcrypt operation.

**Fix** — Commit immediately after the read:
```python
user = db.scalar(select(User).where(User.email == payload.email.lower()))
db.commit()      # <- BEFORE bcrypt
```

### Bug 2 — In-flight requests > DB pool

Our routes are synchronous (`def`), and `get_db` acquires a connection at the start of the request. If the threadpool is saturated, the connection is held while waiting for a thread.

**Fix — Admission control:**

```python
_request_slots = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)   # 30

@app.middleware("http")
async def limit_concurrency(request, call_next):
    async with _request_slots:
        return await call_next(request)
```

Requests now queue *before* acquiring a database connection.

### Result

| | Before Fix | After Fix |
|---|---|---|
| Total requests | 1,250 | **8,154** |
| Failures | 58 | **0** |
| Throughput | 30 rps | **137 rps** |
| p99 | 21,000 ms | **1,400 ms** |

6.5× higher throughput, **zero errors**, and p99 reduced from 21s to 1.4s.

---

## ✅ Proof

### 1. Protected routes
```bash
curl -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" -d '{"seat_id":1}'
# {"detail":"Login required"}
```

### 2. Login → booking
```bash
TOKEN=$(curl -s -c /tmp/ck.txt -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"demo@seatpulse.dev","password":"demo1234"}' \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p')

curl -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/auth/me
curl -X POST -H "Authorization: Bearer $TOKEN" http://localhost:8000/api/seats/1/lock
curl -X POST -H "Authorization: Bearer $TOKEN" -H "Content-Type: application/json" \
  -d '{"seat_id":1}' http://localhost:8000/api/bookings
```

### 3. Test suite
```bash
docker compose exec backend pytest tests/ -v
```
```
13 passed in 20.83s
```

---

## Files

```
backend/
├── auth.py                 ← new  ⭐ hashing, JWT, dependencies, cookies
├── routers/auth.py         ← new  ⭐ signup/login/refresh/logout/Google
├── models.py               ← update (google_id, avatar_url, is_active, password nullable)
├── schemas.py              ← update (auth schemas, removed user_id)
├── config.py               ← update (JWT, Google, MAX_CONCURRENT_REQUESTS)
├── database.py             ← update (pool sizing)
├── main.py                 ← update (admission control, WS auth)
├── routers/seats.py        ← update (auth)
├── routers/bookings.py     ← update (auth + IDOR fix)
└── tests/test_auth.py         ← 7 new auth tests (6 → 13)

frontend/src/
├── auth/
│   ├── AuthContext.jsx     ← new  ⭐ token memory + silent refresh
│   └── AuthPage.jsx        ← new  (login/signup + Google button)
├── api.js                  ← update (Bearer, 401 retry, credentials)
├── App.jsx                 ← update (auth gate, logout)
└── hooks/useWebSocket.js   ← update (token query param)
```

---

## Commit

```bash
git add .
git commit -m "Phase 7: JWT auth (access + httpOnly refresh) and Google OAuth

- Remove user_id from request bodies; user now comes from the token
- Fix IDOR: users could cancel anyone's booking
- Fix pool exhaustion under load: admission control + no bcrypt inside a txn
  200 concurrent users: 1250 reqs/58 failures -> 8154 reqs/0 failures"
git push
```
