# Phase 7 — JWT Auth + Google OAuth

[Phase 6 — Load Testing](06-load-testing.md) ke baad ka kaam.

**Kya theek hua:** Phase 6 tak `POST /api/bookings` me **`user_id` body me** jata tha. Matlab koi bhi ye bhej ke kisi aur ke naam booking kar sakta tha:

```json
{ "seat_id": 5, "user_id": 7 }
```

Interviewer `/docs` khol ke pehle hi endpoint pe ye pakad leta. Ab user **token se** aata hai.

---

## Token strategy (ye design decision hai — interview me poocha jata hai)

| Token | Kahan rehta hai | Kitni der | Kaam |
|---|---|---|---|
| **Access** | React ki memory (RAM) | 30 min | Har API call me `Authorization: Bearer` |
| **Refresh** | httpOnly cookie | 7 din | Sirf naya access token lene ke liye |

### localStorage me kyu nahi rakha

localStorage ko **koi bhi JavaScript padh sakta hai** — koi XSS, koi malicious npm package, koi browser extension. httpOnly cookie JS se readable hi nahi hoti.

### To phir sab kuch cookie se kyu nahi?

Cookie har request me apne aap jati hai — isse **CSRF** ka darwaza khulta hai. Isliye:

- **Asli kaam access token karta hai** — `Authorization` header se, jo CSRF attack me automatically nahi jata
- **Cookie sirf refresh ke liye** — aur wo bhi `path=/api/auth`, `samesite=lax` ke saath

### Access token RAM me — reload pe kya hota hai?

Chala jata hai. Isliye app mount hote hi ek `POST /api/auth/refresh` marta hai. Cookie valid hui to session turant wapas — user ko pata bhi nahi chalta.

Yahi mechanism **Google login ke baad** bhi kaam aata hai (neeche).

---

## Refresh token revocation — Redis whitelist

JWT stateless hota hai: ek baar bana diya to expiry tak valid rehta hai. Matlab **logout ka koi matlab hi nahi** — token 7 din chalta rahega.

Isliye har refresh token me ek `jti` (unique id) hota hai jo Redis me whitelist hoti hai:

```python
jti = uuid.uuid4().hex
redis_client.setex(f"refresh:{user_id}:{jti}", timedelta(days=7), "1")
```

- **Logout** → wo key delete → token turant bekaar
- **Redis TTL** = token expiry → purani entries apne aap saaf, koi cleanup job nahi
- **logout-all** → `scan_iter(f"refresh:{user_id}:*")` → sab devices se logout

### Rotation

`/refresh` par purana token **turant revoke** hota hai aur naya milta hai.

Faayda: token chori ho jaye aur attacker use kare, to asli user ka token invalid ho jayega aur uska logout ho jayega — **chori pakdi jayegi**.

---

## Password hashing — bcrypt

```python
bcrypt.hashpw(password.encode(), bcrypt.gensalt())
```

**bcrypt jaan-boojh ke DHEEMA hai (~100ms).** SHA256 jaisa fast hash yahan galat hai — attacker ek second me crores guesses kar leta. bcrypt pe brute force practically namumkin ho jata hai. Salt bhi apne aap andar aa jata hai.

> Ye "slow by design" wali baat aage load test me kaat gayi — neeche "Auth ne load test todha" section dekho.

---

## Google OAuth — Authorization Code flow

```
1. User "Continue with Google" dabata hai
   -> browser backend ke /api/auth/google/login pe jata hai

2. Backend user ko Google pe bhej deta hai (ek random `state` ke saath)

3. User Google pe login karta hai aur permission deta hai

4. Google user ko wapas /api/auth/google/callback pe bhejta hai, `code` ke saath

5. ⭐ BACKEND wo code Google ko wapas bhejta hai (client_secret ke saath)
   aur badle me user ki info leta hai — SERVER-TO-SERVER, browser beech me nahi

6. Backend refresh cookie set karta hai aur frontend pe redirect kar deta hai
```

### Kyu ye flow, koi aur nahi

| Sawaal | Jawab |
|---|---|
| Purana "Implicit" flow kyu nahi? | Wo token seedha URL me deta tha — browser history aur server logs me chhap jata |
| Frontend-only OAuth kyu nahi? | `client_secret` browser me chala jata, jahan koi bhi use padh sakta hai |
| `state` kis liye? | CSRF protection — random string Redis me rakhte hain, Google wahi wapas bhejta hai. Match na kare to reject |
| Access token URL me kyu nahi bheja? | Wahi wajah — history/logs. Sirf cookie set karke redirect karte hain, frontend `/refresh` se token le leta hai |

### google_id pe match, email pe nahi

```python
user = db.scalar(select(User).where(User.google_id == google_id))
```

User Google me apna email badal sakta hai, par `sub` (google_id) kabhi nahi badalta.

Agar us email se **password wala account pehle se hai**, to use link kar dete hain — naya duplicate account nahi banate.

### Google users ka password NULL hota hai

```python
hashed_password: Mapped[str | None] = mapped_column(String(255), nullable=True)
```

Isliye migration me column nullable karna pada. `verify_password()` `None` par hamesha `False` deta hai.

---

## Google credentials kaise banayein

1. [console.cloud.google.com](https://console.cloud.google.com) → **New Project** → naam `SeatPulse`

2. **APIs & Services → OAuth consent screen**
   - User Type: **External**
   - App name, support email, developer email bharo
   - Scopes: `userinfo.email` aur `userinfo.profile`
   - **Test users** me apna Gmail add karo (publish na karo to sirf yahi log login kar payenge)

3. **APIs & Services → Credentials → Create Credentials → OAuth client ID**
   - Type: **Web application**
   - **Authorized redirect URIs** me bilkul ye:
     ```
     http://localhost:8000/api/auth/google/callback
     ```

4. Client ID aur Secret `backend/.env` me daalo

> ⚠️ Redirect URI **exactly** wahi honi chahiye — ek extra slash bhi ho to `redirect_uri_mismatch` aata hai. Port **8000** (backend), 5173 nahi.

> `.env` gitignored hai, to credentials GitHub pe nahi jaate. Kabhi galti se push ho jaayein to Google Console se **turant revoke** karke naye bana lena.

**Credentials na ho to?** `GOOGLE_CLIENT_ID` khali chhod do — Google button apne aap chhup jayega (`/api/auth/config` batata hai), email/password chalta rahega.

---

## Endpoints

| Method | Route | Kaam |
|---|---|---|
| GET | `/api/auth/config` | Google button dikhana hai ya nahi |
| POST | `/api/auth/register` | Naya account (signup ke baad seedha logged in) |
| POST | `/api/auth/login` | Email + password |
| POST | `/api/auth/refresh` | Cookie se naya access token (rotation ke saath) |
| POST | `/api/auth/logout` | Ye device |
| POST | `/api/auth/logout-all` | Sab devices |
| GET | `/api/auth/me` | Current user |
| GET | `/api/auth/google/login` | Google pe redirect |
| GET | `/api/auth/google/callback` | Google se wapas |

---

## Security fixes jo saath me hue

| Kya | Pehle | Ab |
|---|---|---|
| Booking kis ke naam | Body me `user_id` — koi bhi kuch bhej sakta tha | Token se |
| Seat lock kis ke naam | Body me `user_id` | Token se |
| `GET /api/bookings` | `?user_id=` — koi bhi kisi ki bookings dekh leta | Sirf apni |
| `DELETE /api/bookings/{id}` | **Koi bhi kisi ki booking cancel kar sakta tha (IDOR)** | Ownership check |
| Login error message | — | "email nahi mila" aur "password galat" ka **ek hi** message (user enumeration se bachne ke liye) |
| WebSocket | Koi bhi connect kar sakta tha | Token chahiye (`?token=`) |

### IDOR fix me 404, 403 nahi

```python
if booking.user_id != user.id:
    raise HTTPException(404, "Booking nahi mili")
```

403 dete to attacker ko pata chal jata ki **wo booking exist karti hai**. 404 kuch nahi batata.

### WebSocket auth — token query param me kyu

Browser ka WebSocket API **custom headers bhejne hi nahi deta**. Isliye `?token=...`.

Trade-off: URL server logs me aa sakta hai. Isliye wahan sirf **short-lived access token** bhejte hain (30 min), refresh token kabhi nahi.

---

## Frontend

Teen nayi cheezein: token kahan rakhna hai, 401 pe kya karna hai, aur login page.

### `api.js` — token + automatic retry

**Token module-level variable me hai, `localStorage` me nahi:**

```js
let accessToken = null;            // sirf RAM me

export function setAccessToken(token) { accessToken = token; }
export function getAccessToken()      { return accessToken; }
```

Page reload pe ye chala jata hai — aur **wahi to chahiye**. Reload pe cookie se naya le lete hain.

**401 aane par ek baar refresh karke retry:**

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

| Cheez | Kyu |
|---|---|
| `retry` flag | Ek hi baar retry. Warna refresh bhi 401 de to infinite loop ban jata |
| `!path.startsWith("/api/auth/")` | `/login` ka 401 "galat password" hai — usko refresh karke retry karna bewakoofi hai |
| `credentials: "include"` | Iske bina cookie na jayegi na set hogi (cross-origin 5173 → 8000) |

**Faayda:** access token beech kaam me expire ho jaye — jaise user seat hold karke coffee peene chala gaya — to bhi use dobara login nahi karna padta. Retry chupchap ho jata hai.

### `AuthContext.jsx` — session ka dimaag

**Mount pe session restore:**

```js
useEffect(() => {
  async function boot() {
    const config = await api.getAuthConfig()      // Google button dikhana hai?
    setGoogleEnabled(config.google_enabled)

    const data = await api.refreshSession()       // cookie se session wapas
    if (data) applySession(data)

    setLoading(false)
  }
  boot()
}, [applySession])
```

> ⚠️ **`loading` state zaroori hai.** Iske bina ek pal ko login page flash hota hai aur phir gayab ho jata hai — kyunki refresh complete hone se pehle `user` null hota hai.

**Silent refresh — expire hone se 1 min pehle:**

```js
const scheduleRefresh = useCallback((expiresIn) => {
  clearTimeout(refreshTimer.current)
  const delay = Math.max((expiresIn - 60) * 1000, 10_000)

  refreshTimer.current = setTimeout(async () => {
    const data = await api.refreshSession()
    if (data) {
      setUser(data.user)
      scheduleRefresh(data.expires_in)     // khud ko dobara schedule
    } else {
      setUser(null)                        // refresh token bhi mar gaya
    }
  }, delay)
}, [])
```

| Cheez | Kyu |
|---|---|
| `expiresIn - 60` | Expire hone ka wait nahi karte — 1 min pehle hi naya le lete hain |
| `Math.max(..., 10_000)` | Server chhoti expiry bheje to bhi kam se kam 10 sec ka gap. Warna refresh loop ban jata |
| `clearTimeout` pehle | Do timers ek saath na chalein |
| Khud ko dobara schedule | Chain chalti rehti hai jab tak user logged in hai |

> `api.js` ka 401-retry **safety net** hai; ye timer **usse pehle** hi problem khatam kar deta hai. Dono chahiye — timer tab kaam nahi karta jab laptop sleep se utha ho.

**Google callback ke baad URL saaf:**

```js
useEffect(() => {
  const params = new URLSearchParams(window.location.search)
  if (params.has('auth') || params.has('auth_error')) {
    window.history.replaceState({}, '', window.location.pathname)
  }
}, [])
```

Backend `?auth=google` ke saath redirect karta hai. Wo URL me pada rehta to refresh karne pe error message dobara dikhta.

### `App.jsx` — auth gate

```jsx
if (authLoading) return <Loading />
if (!isAuthenticated) return <AuthPage />

return <BookingApp key={user.id} />
```

> **`key={user.id}` par dhyan do.** User badalne par React poora component naya banata hai. Iske bina pichhle user ki bookings aur selected seat nayi login me dikh jaati.

### `AuthPage.jsx`

Ek hi component login aur signup dono karta hai (`mode` state se) — do alag pages banane ki zaroorat nahi, 80% code same hota.

Aur ek chhoti cheez jo demo me bahut kaam aati hai:

```jsx
<button onClick={() => {
  setEmail('demo@seatpulse.dev')
  setPassword('demo1234')
}}>
  demo@seatpulse.dev / demo1234
</button>
```

Recruiter/interviewer ek click me andar. Type karne ki zaroorat nahi.

**Google button** tabhi dikhta hai jab `googleEnabled` true ho — credentials na ho to UI me wo option hai hi nahi, tootа hua button nahi dikhta.

### `useWebSocket.js` — token query param

```js
const token = getAccessToken()
if (!token) return                    // token nahi to connect hi mat karo

const base = API_URL.replace(/^http/, 'ws')
const wsUrl = `${base}/ws/events/${eventId}?token=${encodeURIComponent(token)}`
```

Browser ka WebSocket API **custom headers bhejne hi nahi deta**, isliye query param. `encodeURIComponent` zaroori hai warna token ke special characters URL tod dete.

---

## ⭐ Auth ne load test todha — aur usse do asli bug mile

Auth add karne ke baad load test dobara chalaya. **Sab kuch phat gaya:**

```
Total requests   : 1250
Failures         : 58        <- 500 Internal Server Error
Requests/sec     : 30.3
p99              : 21000 ms
```

Logs me:
```
sqlalchemy.exc.TimeoutError: QueuePool limit of size 20 overflow 30 reached,
connection timed out, timeout 10.00
```

### Bug 1 — bcrypt transaction khuli rakhta tha

Postgres se pucha ki ho kya raha hai:

```sql
SELECT count(*) total,
       count(*) FILTER (WHERE state='idle in transaction') idle_txn,
       count(*) FILTER (WHERE state='active') active
FROM pg_stat_activity WHERE datname='seatpulse';
```

```
 total | idle_txn | active
    51 |       50 |      1
```

**50 me se 50 connections "idle in transaction", sirf 1 active.** Kaam koi nahi kar raha tha — sab connections pakde baithe the.

Wajah: SQLAlchemy pehli query pe transaction khol deta hai aur commit/close tak khuli rehti hai. Login me:

```python
user = db.scalar(select(User).where(...))    # transaction khul gayi
...
verify_password(payload.password, user.hashed_password)   # bcrypt ~100ms+
```

Utni der Postgres us connection ko "idle in transaction" me pakde baitha rehta tha.

**Fix** — read ke turant baad transaction band:
```python
user = db.scalar(select(User).where(User.email == payload.email.lower()))
db.commit()      # <- bcrypt se PEHLE
```

### Bug 2 — in-flight requests > DB pool

Ye asli wala tha.

Hamare routes sync hain (`def`), aur `get_db` request ke **shuru me** connection pakad leta hai. Phir request threadpool slot ka wait karti hai — aur us poore intezaar me connection pakda hi rehta hai.

Isliye "held connections" threadpool size se **zyada** ho jaate the. Threadpool 32 rakhne ke baad bhi 40 connections checked out the.

Pool badha ke fix karne ki koshish bekaar hai — in-flight requests **unbounded** hain, kitna bhi pool rakho, load badhne pe phir phategi.

**Fix — admission control.** Darwaze pe hi rok lagao:

```python
_request_slots = asyncio.Semaphore(settings.MAX_CONCURRENT_REQUESTS)   # 30

@app.middleware("http")
async def limit_concurrency(request, call_next):
    async with _request_slots:
        return await call_next(request)
```

**Invariant:**
```
MAX_CONCURRENT_REQUESTS (30)  <  pool_size + max_overflow (20 + 20 = 40)
threadpool (40)               >=  MAX_CONCURRENT_REQUESTS (30)
```

Ab request andar aane se pehle rukti hai, connection pakadne se pehle. **Slow response 500 error se hazaar guna behtar hai.**

### Result

| | Fix se pehle | Fix ke baad |
|---|---|---|
| Total requests | 1,250 | **8,154** |
| Failures | 58 | **0** |
| Throughput | 30 rps | **137 rps** |
| p50 | 470 ms | **1,000 ms** |
| p99 | 21,000 ms | **1,400 ms** |

6.5× zyada throughput, **zero errors**, aur p99 21s se 1.4s.

> p50 thoda badha (470 → 1000ms) — kyunki ab requests queue me lagti hain. Par pehle wala 470ms **jhootha** tha: usme se 58 requests fail ho rahi thi aur p99 21 second tha. Ab har request poori hoti hai.

**Ye interview ki sabse achhi kahani hai:** load test se bug mila, `pg_stat_activity` se root cause nikala, aur guess karne ke bajaye measure karke fix kiya.

---

## ✅ Proof

### 1. Bina token ke kuch nahi hota
```bash
curl -X POST http://localhost:8000/api/bookings \
  -H "Content-Type: application/json" -d '{"seat_id":1}'
# {"detail":"Login karna zaroori hai"}
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

### 3. Galat password — same message
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

### 4. Refresh cookie se
```bash
curl -b /tmp/ck.txt -X POST http://localhost:8000/api/auth/refresh
```

### 5. Test suite
```bash
docker compose exec backend pytest tests/ -v
```
```
13 passed in 20.83s
```
Isme hain: protected routes, garbage token, user enumeration, refresh rotation, logout revocation, IDOR, aur poore concurrency tests.

### 6. Browser
http://localhost:5173 → login page. Demo credentials ka button hai (ek click me bhar jata hai).

Google configured ho to **Continue with Google** dikhega.

---

## Common Problems

| Problem | Fix |
|---|---|
| `redirect_uri_mismatch` | Google Console me URI **exactly** `http://localhost:8000/api/auth/google/callback` honi chahiye |
| Google button dikh hi nahi raha | `GOOGLE_CLIENT_ID`/`SECRET` `.env` me hain? `curl localhost:8000/api/auth/config` check karo |
| `Access blocked: app not verified` | Consent screen ke **Test users** me apna Gmail add karo |
| Login ho jata hai par reload pe logout | CORS me `allow_credentials=True` hai? Frontend me `credentials: "include"`? |
| Cookie set hi nahi ho rahi | `allow_origins` me `["*"]` nahi chalega credentials ke saath — specific origin do |
| `401` sab jagah | Access token 30 min ka hai. Frontend khud refresh karta hai; na ho to `/refresh` call karo |
| Login page ek pal ko flash hota hai | `AuthContext` ka `loading` state check nahi kar rahe App.jsx me |
| Pichhle user ka data naye login me dikh raha | `<BookingApp key={user.id} />` missing hai |
| Logout ke baad bhi WebSocket chal raha | `useWebSocket` me token check hai? Token null hone par connect nahi karna chahiye |
| Infinite refresh loop | `scheduleRefresh` me `Math.max(..., 10_000)` hai? Chhoti expiry pe loop ban jata hai |
| Load test me 500 errors | `MAX_CONCURRENT_REQUESTS` pool se chhota hai? Invariant check karo |
| `ModuleNotFoundError: jwt` | `docker compose up -d --build backend` |

---

## Files

```
backend/
├── auth.py                 ← naya  ⭐ hashing, JWT, dependencies, cookies
├── routers/auth.py         ← naya  ⭐ signup/login/refresh/logout/Google
├── models.py               ← update (google_id, avatar_url, is_active, password nullable)
├── schemas.py              ← update (auth schemas, user_id hataya)
├── config.py               ← update (JWT, Google, MAX_CONCURRENT_REQUESTS)
├── database.py             ← update (pool sizing + comment)
├── main.py                 ← update (admission control, WS auth, /api/me hataya)
├── seed.py                 ← update (asli bcrypt hashes)
├── routers/seats.py        ← update (auth)
├── routers/bookings.py     ← update (auth + IDOR fix)
├── tests/test_concurrency.py  ← update (13 tests)
└── alembic/versions/...    ← nayi migration

frontend/src/
├── auth/
│   ├── AuthContext.jsx     ← naya  ⭐ token memory + silent refresh
│   └── AuthPage.jsx        ← naya  (login/signup + Google button)
├── api.js                  ← update (Bearer, 401 retry, credentials)
├── App.jsx                 ← update (auth gate, logout, avatar)
├── main.jsx                ← update (AuthProvider)
└── hooks/useWebSocket.js   ← update (token query param)

loadtest/locustfile.py      ← update (AuthedUser base class)
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

---

## Related

- [Phase 6 — Load Testing](06-load-testing.md) — load testing
- [testing.md](../reference/testing.md) — saare test commands
- [roadmap.md](../roadmap.md) — poora plan
