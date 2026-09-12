# SeatPulse — Interview Prep

Format for every question: **question → why they are asking → answer (with actual numbers)**.

> **One rule:** Answers should be 30-60 seconds long. Long answers show nervousness, not confidence. Give a concise answer, then stop — the interviewer will dig where they are interested.

**Actual numbers for this project (remember, these are your greatest strength):**

| What | Number |
|---|---|
| Flash sale test | 200 concurrent users, one seat |
| Total requests | 8,154 · **0 failures** · 137 req/s |
| Result | **Exactly 1** confirmed booking in DB |
| Latency (flash sale) | p50 1,000 ms · p99 1,400 ms |
| Latency (50 users, normal) | p50 13-21 ms · p95 85-95 ms |
| Tests | 29 (auth + RBAC + rate limit + concurrency) |
| Bugs found via load test | 3 (all fixed, all measured) |
| Bugs found via tests | 2 (rate-limit peek, SQLAlchemy cascade) |

---

## 1. Opening — the first 60 seconds

This is the most important part. It determines the direction of the interview.

> "SeatPulse is an event ticketing platform — like BookMyShow. The real problem I solved: **overselling during flash sales**. When 5,000 people click on the same seat simultaneously, the naive `SELECT → check → UPDATE` flow sells that seat multiple times.
>
> I built three layers — Redis distributed lock for speed, Postgres optimistic locking for correctness, and a partial unique index as a final guarantee.
>
> And I didn't just build it, I **proved it**: 200 concurrent users on one seat using Locust, 8,000+ requests, zero failures, and exactly one booking in the database. That load test caught three real race conditions, which I debugged and fixed using `pg_stat_activity`."

**I have intentionally left three hooks in this pitch** — three layers, load test numbers, and "three bugs". The interviewer will dig into one of these, and you are ready.

---

## 2. Tech choices — "why this, why not that"

> **This question is not a trap.** They are checking if you **chose deliberately** or just copied a tutorial. The shape of the answer should always be:
> *"This was the constraint → that's why I chose this → if the constraint changed, I would have chosen X."*

### Why FastAPI?

> "I needed two things — WebSockets and high concurrency. FastAPI is ASGI-native, so WebSockets are first-class; in Django, you have to add Channels separately. Pydantic gave me validation and OpenAPI docs for free. If I had used Flask, both async and WebSockets would have been bolt-ons."

### Why React? Why not Next.js?

> "The seat grid has 100 cells that change state independently — when a WebSocket message arrives, only one seat should re-render, not the entire grid. React's reconciliation does exactly that.
>
> I didn't use Next.js because this is a fully authenticated dashboard — there was no benefit to SSR or SEO. It would have added an extra build layer without providing any value."

### Why PostgreSQL, not MongoDB?

**This is the best answer — put all your strength here.**

> "This was the most important choice of the project. My final defense against overselling is a **partial unique index** — one seat, one confirmed booking, at the database level. Also, the seat update and booking insert must happen in a single transaction.
>
> Mongo has multi-document transactions, but that isn't its strength. I needed **ACID here, not a flexible schema**. The seat schema never changes — it will always be row, number, status, price."

### Why Redis? Doesn't the database suffice?

> "**It does** — and it was working. In Phase 3, there was a DB-only version, and it worked perfectly at 20 concurrent requests.
>
> Redis isn't for correctness; it's for **load**. Out of 5,000 requests, 4,999 are stopped at Redis and never reach the database. And I got abandoned cart cleanup for free via TTL — I didn't have to write a cron job."

> ⭐ This answer is strong because you aren't **glorifying Redis**. Most candidates say "Redis prevents overselling" — that is incorrect.

### Why WebSockets, not polling?

> "If 1,000 clients polled every 2 seconds, 500 req/s would be wasted just asking 'did anything change?'. With WebSockets, traffic only occurs when something actually changes.
>
> SSE would have worked too — the flow is one-directional. I chose WebSockets so the base is ready if I need two-way communication later."

### Wouldn't Go or Java be better?

**Don't get defensive here. Acknowledge it, then bring it back to the real issue.**

> "Yes, Go would be better for this workload — the per-request cost in goroutines is very low; I wouldn't have had to deal with thread pools and pool sizing.
>
> I chose Python because of the ecosystem — SQLAlchemy, Alembic, Pydantic — which allowed me to focus on business logic.
>
> But the real point is: **the bottleneck wasn't the runtime, it was Postgres row-level contention.** 200 people on one seat — that row will be serialized just as much in Go. Changing the language doesn't solve that problem."

---

## 3. Core — concurrency and locking

### How do you prevent overselling? (most common question)

> "Three layers, the top one is the fastest and the bottom one is the most robust:
>
> **1. Redis lock** — `SET seat:42:lock <user> NX EX 300`. An atomic command. Out of 5,000 requests, exactly one gets `True`.
>
> **2. Optimistic locking** — there is a `version` column on the seat. The update runs like this: `UPDATE seats SET status='booked', version=version+1 WHERE id=? AND version=?`. In parallel updates, one will not match the `WHERE` clause, it will get `rowcount 0`, and I return 409.
>
> **3. Partial unique index** — `UNIQUE(seat_id) WHERE status='confirmed'`. This is the database's own rule. If Redis is down, if there's a bug in my code, if two servers are running — Postgres will simply not allow a second confirmed booking to be inserted."

### Why are all three layers needed? Doesn't one suffice?

**This is the best follow-up. Explain what breaks without each layer:**

| Only this layer | What breaks |
|---|---|
| Only Redis | All locks vanish on Redis restart. Overselling can happen in that window. Redis has no durability — and I intentionally didn't give it volume |
| Only version column | Correct, but **every** request hits the DB. 5,000 requests = 5,000 DB round trips |
| Only unique index | Correct, but every failure becomes an `IntegrityError`. Using exceptions for flow control is expensive and messy |

> "So Redis provides speed, the version column catches most clashes, and the index is the final insurance that hopefully never triggers."

### Optimistic vs Pessimistic — why did you choose optimistic?

> "Pessimistic means `SELECT ... FOR UPDATE` — locking the row and making everyone else wait. Optimistic means not taking a lock, assuming clashes will be rare, and **detecting** them if they happen.
>
> I chose optimistic because I **already have Redis on top**. Redis rejects 99% of requests beforehand, so the ones that reach the DB have very few clashes — and that is the best case for optimistic locking.
>
> In pessimistic, every request would queue for the row lock, regardless of whether there's a clash. If I didn't have Redis, pessimistic might have been better."

**Follow-up "prove it?":**
> "That's on my roadmap — to write a pessimistic variant and compare the p99 and throughput using the same Locust suite. I haven't measured it yet, so I won't claim it."

> ⭐ "I haven't measured it, so I won't claim it" — this line increases your credibility, it doesn't decrease it.

### How do you know `rowcount == 0` means a race occurred?

> "`UPDATE ... WHERE id=? AND version=3` — if someone else won first, the version will have become 4, and my `WHERE` won't match any row. Postgres returns `rowcount 0`. That is my signal that my data was stale, and I return 409.
>
> Most importantly: this is **one atomic statement**. Read and write are not separate steps — so no one can sneak in between."

### Why isn't the `if seat.status != 'available'` check before booking enough?

**This is a question for a sharp interviewer. You must know the answer:**

> "That check is absolutely not enough, and I kept it only to provide a **good error message**.
>
> There is a microsecond gap between that line and the UPDATE below. In that gap, another request can take that seat. The real guarantee is in the UPDATE's `WHERE` clause — because the database doesn't allow two UPDATEs on one row to run simultaneously."

### Which isolation level did you use?

> "Postgres default — **READ COMMITTED**. I didn't change it.
>
> Reason: I don't depend on the isolation level. My guarantee comes from an atomic conditional UPDATE and a unique index. Both are just as robust in READ COMMITTED.
>
> If I went to SERIALIZABLE, I would have to write retry logic for serialization failures, and throughput would drop — without gaining any extra safety."

---

## 4. Redis — in detail

### `SET NX EX` — why one command, why not two?

> "`NX` means 'only set if the key does not exist'. This is **atomic** inside Redis.
>
> If I checked `EXISTS` and then `SET`, another client could set the key between those two commands. In one command, that gap doesn't exist. Redis is single-threaded — commands run one by one.
>
> `EX 300` means a 5-minute TTL. This is the most elegant part: the user leaves the cart, the laptop closes, the tab crashes — the **seat frees itself automatically**. I didn't have to write any cleanup job."

### Why a Lua script for lock release? Why not just `DEL`?

> "Because a simple `DEL` could delete **someone else's lock**:
>
> 1. User A has a lock, it expires in 5 minutes.
> 2. User B takes the lock immediately.
> 3. User A's 'release' request arrives and executes `DEL`.
> 4. B's lock is gone — even though B did nothing wrong.
>
> So you have to check 'is this lock mine?' before deleting. But if I wrote `GET` then `DEL` in Python, the same race would exist between them. A Lua script runs atomically inside Redis — check and delete together."

**There is a test for this:** `test_cannot_release_someone_elses_lock` — the response returns `released: false` and the actual lock remains intact.

### What if Redis dies?

> "There are two separate questions here.
>
> **Correctness** — completely safe. Postgres's version column and unique index still work. Overselling still won't happen.
>
> **Availability** — locks will be lost, meaning seats that were on hold will immediately appear available, and the load will hit the DB. It will degrade, not break.
>
> And I **intentionally didn't give Redis volume** — it only contains 5-minute temporary locks. Money and bookings are always in Postgres. Redis is never the source of truth."

### Why no persistence in Redis?

> "It's a design decision, not negligence. It only contains temporary locks that die in 5 minutes anyway. If they disappear on restart, what's the harm — seats become available, which is already the correct state. If I kept persistence, I would be paying for disk I/O without any benefit."

### Why a 5-minute TTL? Why not 1 or 30 minutes?

> "It's a trade-off. Keep it short, and the user loses the seat during payment. Keep it long, and abandoned carts hold seats, blocking inventory.
>
> 5 minutes is a realistic checkout time. And it's in the config (`SEAT_LOCK_TTL`), not hardcoded — because the real number comes from production data, not my guess."

---

## 5. WebSockets

### How do real-time updates work?

> "Every event has its own Redis pub/sub channel — `seatpulse:event:1`. Whenever a seat is locked, released, booked, or canceled, the backend publishes to that channel. Every worker is subscribed to that channel and forwards messages to its connected sockets."

### Why Redis pub/sub? Why not broadcast directly to sockets?

**This is a real system design question:**

> "If there were one server, direct broadcast would suffice. But in production, 2-3 uvicorn workers run, and **each worker has its own separate sockets**.
>
> Suppose User A's lock is processed on Worker 1, and User B's socket is on Worker 2. If Worker 1 only tells its local sockets, User B will never know.
>
> Redis becomes the message bus — every worker publishes and subscribes. And Redis was already in the stack, so no new service was added."

### What if a message drops?

> "Redis pub/sub is **at-most-once** — no persistence, no replay. Messages can drop.
>
> That's why I kept WebSockets as an **optimization**, not the source of truth. Upon reconnecting, the frontend fetches the entire seat list again. And booking itself never depends on WebSockets — it's always an HTTP request with all three safety layers.
>
> If I needed guaranteed delivery — like for payment events — I would use Kafka or Redis Streams."

### What if the broadcast fails, but the booking succeeds?

> "The booking will succeed. My `publish()` swallows exceptions and only logs a warning. Real-time updates are **nice-to-have**, booking is **must-have**. A notification failure shouldn't break the payment flow."

### What if the connection breaks?

> "The frontend hook has **exponential backoff** — 1s, 2s, 4s, 8s, max 15s. If I kept a fixed 1-second retry, 100 clients would hammer the server every second if it went down, and it would never recover. The counter resets on a successful connection.
>
> After reconnecting, the full state is fetched again to compensate for messages missed during the disconnect."

### How did you authenticate WebSockets?

> "Access token in the query param — `?token=...`. Not in headers, because **the browser's WebSocket API doesn't allow sending custom headers**.
>
> The trade-off is that the URL can appear in server logs. That's why I only send a short-lived access token, 30 minutes — never a refresh token. If the token is invalid, the connection closes with code 1008."

---

## 6. Auth

### Where do you store the token? (this will definitely be asked)

> "Access token in React **memory** — never in localStorage. Refresh token in an **httpOnly cookie**.
>
> Anyone can read localStorage via JavaScript — XSS, npm packages, browser extensions. httpOnly cookies are not readable by JS.
>
> But I don't do everything via cookies, because cookies are sent automatically with every request — that opens the door to CSRF. That's why the **Authorization header does the real work** (which isn't sent automatically in CSRF), and the cookie is only used to get a new access token, with `path=/api/auth` and `SameSite=Lax`."

### What happens to the access token on reload?

> "That's exactly the point. As soon as the app mounts, it hits `/refresh` — if the cookie is valid, the session is restored immediately; the user doesn't even notice.
>
> This same mechanism works for Google login: the backend sets the cookie and redirects to the frontend, and the mount-refresh creates the session. **The token never goes into the URL.**"

### JWT is stateless — how does logout work?

**This is a sharp question. Most candidates get stuck here.**

> "You're right — in plain JWT, logout has no meaning; the token stays alive until expiry.
>
> That's why every refresh token has a `jti` that is **whitelisted in Redis**, with a TTL equal to the token's expiry. Logout deletes that key — the token becomes useless immediately, even if 7 days remain on the JWT expiry.
>
> The access token will still technically be valid for 30 minutes. That's why I kept it short. If I checked the DB on every request, the stateless benefit of JWT would be gone."

### What is refresh token rotation?

> "Every `/refresh` call revokes the old token and provides a new one.
>
> Benefit: if a token is stolen and the attacker uses it, the real user's token becomes invalid and they are logged out. **The theft is detected** — it doesn't just keep working."

### Why bcrypt, not SHA256?

> "Because bcrypt is **intentionally slow** — ~100ms. SHA256 is fast, and for passwords, speed is the problem: an attacker can make millions of guesses in a second. With bcrypt, that attack becomes practically impossible. Salt is also handled internally.
>
> And this slowness actually bit me during the load test — I'll get to that story later."

### Why Authorization Code flow for Google OAuth?

> "Two reasons:
>
> The old **Implicit flow** put the token directly in the URL — it would be printed in browser history and server logs.
>
> And in **frontend-only OAuth**, the `client_secret` would go to the browser, where anyone could read it. In Authorization Code flow, the exchange of the code is **server-to-server** — the secret never reaches the browser.
>
> There is also a `state` parameter for CSRF — I keep a random string in Redis, Google sends it back, if it doesn't match, I reject it."

### What if the Google user's email changes?

> "That's why I match on **`google_id` (sub claim)**, not email. Email can change, `sub` never changes.
>
> And if an account with that email already exists with a password, I **link** them — I don't create a duplicate account."

---

## 7. ⭐ Load testing and three bugs — this is where the most points are

> This is your strongest section. Most candidates don't have a load test, and those who do haven't **found bugs** with it.

### How did you test?

> "Two scenarios with Locust. One flash sale — 200 concurrent users, all on the same seat. The second, realistic browsing — 50 users viewing the grid and booking occasionally.
>
> But Locust's 'zero failures' wasn't enough for me. It could return 201 for both requests and count both as success. That's why I wrote `verify_integrity.py` which asks the **database** after the test — are there two confirmed bookings for any seat, do seat status and bookings match?
>
> Result: 8,154 requests, zero failures, and exactly one booking in the database."

### Did you find any bugs via load test?

**Say this with enthusiasm. Finding bugs is a good thing.**

> "I found three, and all three were of different types."

#### Bug 1 — Lost update race

> "The `lock_seat` DB update lacked a status guard. The sequence was:
>
> B read the seat (it was in A's lock), the check passed. A booked it in the meantime — status `booked`, Redis lock released. B now got that free Redis lock, and wrote `status = locked` in the DB — **overwriting `booked`**.
>
> Result: there was a confirmed booking, but the seat didn't appear booked.
>
> **This was never caught in the 20-request test.** That timing window only opened at 500 users. Fixed with the guarded-update pattern — `WHERE status IN ('available','locked')`, and if `rowcount 0` comes, release the Redis lock and return 409."

#### Bug 2 — bcrypt kept the transaction open

> "After adding auth, the load test exploded — 58 failures, p99 21 seconds, `QueuePool limit reached`.
>
> Instead of guessing, I asked Postgres:
>
> ```sql
> SELECT count(*) FILTER (WHERE state='idle in transaction'), count(*) FILTER (WHERE state='active')
> FROM pg_stat_activity WHERE datname='seatpulse';
> ```
>
> The answer: **50 out of 50 connections were 'idle in transaction', only 1 active.** Meaning no one was doing work, everyone was holding connections.
>
> Reason: SQLAlchemy opens a transaction on the first query and keeps it open until commit. In login, I read the user, then bcrypt runs for 100ms — the connection is blocked the whole time. Fix: `db.commit()` immediately after the read, before bcrypt."

#### Bug 3 — In-flight requests > connection pool

**This is the best one — tell the whole story.**

> "Increasing the pool didn't solve the problem. The root cause was:
>
> My routes are sync, and `get_db` grabs a connection at the **start** of the request. Then the request waits for a threadpool slot — and it holds the connection during that entire wait. That's why held connections exceeded the threadpool size.
>
> And increasing the pool isn't a fix — in-flight requests are **unbounded**. No matter how big the pool, it will break again under load.
>
> So I added **admission control** — a semaphore middleware that allows fewer requests than the pool size. Now the request stops at the door before grabbing a connection.
>
> The invariant is clear: `MAX_CONCURRENT_REQUESTS (30) < pool_size + max_overflow (40)`.
>
> Result: **from 1,250 requests and 58 failures to 8,154 requests and zero failures.** Throughput from 30 to 137 rps, p99 from 21 seconds to 1.4 seconds."

**Follow-up "but p50 increased (470ms → 1000ms)?":**
> "Yes, because requests now queue up. But the previous 470ms was **false** — 58 requests were failing and the p99 was 21 seconds. Now every request completes. **A slow response is a thousand times better than a 500 error.**"

### What do these three bugs teach you in one line?

> "That correctness must be **measured**, you can't assume it. None of these three bugs could be found by reading the code — all three only appeared under load."

---

## 8. Scaling — "what if 10x traffic comes?"

### How will you scale now?

> "In this order:
>
> **1. Multiple workers** — currently there is one uvicorn worker. I'll go to `--workers 4`. My design is already ready: Redis lock works cross-worker, and WebSocket broadcast uses Redis pub/sub — I built both that way for this reason.
>
> **2. Async database driver** — sync SQLAlchemy is my biggest limitation. I'll move to `asyncpg`, then one worker will handle much higher concurrency.
>
> **3. Read replicas** — reading the seat grid is the most frequent operation. That can go to a replica, writes to the primary.
>
> **4. Queue** — instead of giving everyone an immediate answer during a flash sale, a waiting room, like Ticketmaster does."

### What if 50,000 people come for one seat?

> "That's a different problem. In that case, taking a lock is useless — 49,999 people will get a 409 and the experience will be terrible.
>
> The real solution is a **waiting room**: put users in a queue and give them booking windows in batches. That can be done with a Redis sorted set. That's work beyond my current roadmap, I haven't built it yet."

### What if you have to run in two datacenters?

> "Then Redis lock isn't enough — cross-region Redis has latency and split-brain issues. There, you have to **shard** seat inventory region-wise, or use an algorithm like Redlock.
>
> But honestly — this isn't the scale of this project. One Redis in one region is perfectly fine."

---

## 9. Weaknesses — admit them yourself

> ⭐ This seems counter-intuitive but is the **strongest move**. Someone who knows their own limitations sounds senior.

### "What would you do differently?"

> "Three things:
>
> **1. Async instead of sync.** I used sync routes and sync SQLAlchemy. That's why I had to add admission control. With `asyncpg` + async SQLAlchemy, one worker would handle much more.
>
> **2. Consistency between booking and payment.** Currently, there is no payment in booking — and that's not just a missing feature, it's a missing *problem*. Details in the next section.
>
> **3. Seat model.** Currently, every seat is a row. For general admission events — where only the count matters, not the seat number — this is wasteful. There, you need a counter, not 5,000 rows."

### "What didn't you do that you should have?"

> "**Payments.** Booking is incomplete without payment — and that's not just a missing feature, it's a missing *problem*. As soon as money enters, the whole consistency question opens up, which isn't in my project yet.
>
> That's my next phase, and I know what I'll have to do — I can explain it in the next question."

And if they don't ask further, **bring it up yourself**. You should have this section ready.

---

## 10. Payments — "how would you add it?"

> ⚠️ This is **not built yet**. But this is the most common follow-up ("what's next?"), and giving a good answer shows you think about **problems**, not features.
>
> How to say it: "haven't built it yet, but I've thought through the design —" then the following.

### The real problem isn't the gateway

> "Integrating Stripe or Razorpay is something anyone can do by reading the docs. The real problem is what payments **force** upon you:
>
> **Money was deducted, but the booking failed.**
>
> This is the classic **dual-write problem** — keeping two systems, the payment gateway and my database, consistent when either can fail at any time."

### The seat state machine will change

> "Currently the flow is: `available → locked → booked`, and booked happens in that same API call.
>
> After payments:
>
> ```
> available → locked → payment_pending → booked
>                            ↓
>                   (fail/timeout) → available
> ```
>
> The seat won't be booked until the **webhook arrives**."

### ⭐ Webhook is the source of truth, not the browser redirect

This is the most important part of this entire answer.

> "After payment, the gateway redirects the user to my site. You **cannot** trust that redirect — for two reasons:
>
> 1. If the user closes the tab after paying, the redirect never arrives — but the money has been deducted. The booking must happen.
> 2. If someone hits that redirect URL directly, a booking will be created without payment.
>
> That's why the real confirmation comes from the **webhook** — server-to-server, and its **signature is verified**. The redirect is only for the UI: to show a 'thank you' page, not to make decisions."

### Idempotency becomes even more important here

> "I already have idempotency keys (Phase 9). Without payments, that was 'don't create two bookings on a double-click'.
>
> With payments, that becomes '**don't deduct money twice**' — same code, much higher stakes.
>
> And webhooks themselves are **at-least-once** — the gateway can send the same event twice if the first response is missed. So the webhook handler must also be idempotent. The same Redis pattern will apply, using the event ID as the key."

### Card details never on my server

> "I'd use hosted checkout — Stripe Checkout or Payment Links. Card details never touch my backend.
>
> Otherwise, I would fall under **PCI-DSS scope**, which is the wrong design even for a portfolio project — and in production, the entire burden of compliance would arrive."

### The project should run even without keys

> "If the interviewer clones my repo, they won't have my Stripe keys. That's why if `STRIPE_KEY` is empty, a 'Simulate payment' path appears that fires the same webhook internally.
>
> The exact same pattern I used in Google OAuth — if credentials aren't there, the button hides, everything else keeps working."

### Follow-ups that will come

| Question | Answer |
|---|---|
| "What if the webhook is late?" | The seat sits in `payment_pending` with its own TTL. If the TTL expires and no webhook arrives, release the seat, and trigger the refund flow. Otherwise, a failed payment would block the seat forever |
| "What if the webhook arrives twice?" | Idempotent via event ID — the second time returns the same stored result, doesn't do work again |
| "What if the webhook never arrives?" | The gateway retries. Above that, a reconciliation job — settle pending payments by asking the gateway. Never trust only the webhook |
| "How to refund?" | Booking cancel → refund API → booking becomes `refunded` only when the refund webhook arrives. Same asymmetry: we send money, gateway gives confirmation |
| "Why not both in one transaction?" | Because the gateway is not in my database transaction. Keeping an external call open inside a DB transaction is the most common mistake — the transaction stays open as long as the network call |


---

## 11. Dynamic pricing — "a quote is a promise"

This section seems small but is **very popular in interviews**, because
it contains a decision that most people miss.

### "How did you do dynamic pricing?"

> "The formula is the most boring part — `multiplier = 1 + (sold/total) x demand_factor`, with a `max_surge` cap. Two decisions were interesting.
>
> **First:** the seat's `price` column never updates. That is the BASE, and the current price is always calculated from it.
>
> **Second:** when a user holds a seat, their price is locked too."

### ⭐ "What was the problem with updating the base price?"

This is the question where you can list four different reasons — and four reasons sound much better than one:

> "Four things would break:
>
> 1. **History is erased** — the old booking says ₹800, the seat says ₹1400. There's no answer to 'what was the original price'.
> 2. **Write amplification** — 500 UPDATEs for one booking. 500 bookings in a flash sale means 250,000 row updates.
> 3. **New race condition** — two parallel bookings would now fight over the price update too. I would have created a new contention point.
> 4. **Compounding** — `price x 1.1` applied repeatedly means ₹800 → ₹880 → ₹968… that wasn't the intent of the formula.
>
> Now three separate facts are in three separate places: base price on the seat, multiplier calculated, and what was actually charged on the booking."

### ⭐⭐ "What if the price changes during checkout?"

**This is the best question of this entire feature. Keep it ready.**

> "I thought about this before building the feature, because it's a question of correctness, not UX.
>
> The user sees ₹1000, holds the seat, goes to the payment page. 4 more seats sold in the meantime. If checkout charges ₹1400 — I've quietly taken more money from the user. That's not a bug, that's deception.
>
> Solution: the quote is **locked with the hold**. We write the price shown to the `seats.held_price` column. It becomes NULL when the hold is released or expires."

**Follow-up — "why a column, why not calculate?"**

> "Because 'what was the price at that moment' cannot be computed later — demand has changed by then. A quote is a promise, and promises must be stored, not derived."

**Follow-up 2 — "what if the user holds-releases-holds to catch a cheaper price?"**

> "That's why `held_price` is set to NULL on release, and in lazy expiry cleanup too. A new hold means a new price. I specifically tested this — `test_releasing_a_hold_drops_the_locked_price`."

### "Did you find any bugs related to this in payments?"

Admit it yourself — admitting a self-caught bug sounds very strong:

> "There was a hidden error. I called `price_now()` twice — once to create the payment row, once to create the gateway session. The hold could expire in between, and the gateway would charge ₹1400 while my DB said ₹1000.
>
> That mismatch would only be caught by the reconciliation job — by then the user's money would have been deducted. Now the quote is extracted once and the same value goes to both places."

### "How did you send price updates via WebSockets?"

> "The straightforward path is broadcasting the new price for every seat — but for an event with 500 seats, one booking = 500 messages. In a flash sale, that's a DoS attack.
>
> The real point is that the multiplier is **the same for the entire event**, and the base price is already on the frontend. So I send an event-level `pricing_update` message. One message vs 500, same result."

**Follow-up — "wouldn't the frontend just do base x multiplier?"**

Here is a nice detail that surprises the interviewer:

> "I tried, but didn't keep it. JavaScript's `Math.round(100.5)` gives 101, Python's `round(100.5)` gives 100 — banker's rounding. They give different answers on ties.
>
> Meaning the UI shows ₹1010 and the server charges ₹1000. ₹10 sounds small, but the entire foundation of this feature is 'what you see is what you pay' — that breaks.
>
> So the banner updates immediately (what the user sees), and exact prices come from the server after a 400ms debounce."

### "How did you show urgency in the UI?"

> "Only when it's true. 'N seats left at this price' only appears when the server has actually calculated that the price will increase in N seats — and it runs a loop to find that, not a guess from a formula.
>
> If the price isn't going to change, or max surge has been reached, that line **doesn't appear at all**. Empty space is better than fake urgency. Along with the 'price locked' badge — that only appears when the market price is actually above the locked price."

### Rapid fire

| Question | One-line answer |
|---|---|
| Default on or off? | Off. Surge feels predatory for free community meetups — the organizer turns it on |
| Why `max_surge`? | Without a cap, pricing feels out of control. And there's an upper bound on `demand_factor` — typing 50 by mistake is very expensive |
| Can the organizer edit the base price? | No. It would make old bookings false. They can edit surge knobs — those only apply to future bookings |
| Why not cache the multiplier? | 2 count queries, not per-seat. And showing the wrong cached price is more expensive than that saving |
| Why not time-based surge? | Without real historical data, those are just random constants. I don't claim what I haven't measured |

---

## 12. ⭐ Locking benchmark — "I measured, and I was wrong"

This section is **most useful** in an interview, because
it contains something very few people say: *my guess was wrong.*

### "Why didn't you use `SELECT ... FOR UPDATE`?"

The old answer was theory. Now there are numbers:

> "I implemented both and ran them under the same load. Three things were found,
> and all three are interesting.
>
> **First:** if the Redis layer is on, this question is moot. Out of 1433
> contended requests, only **1** reached the database — the rest 1432
> were stopped at the Redis lock. The DB strategy code doesn't even run in production config.
>
> **Second:** when I measured without Redis, pessimistic was **5-7% faster**,
> not slower. This was contrary to my guess.
>
> **Third, and the real answer:** a booking request is **33 SQL statements**.
> The locking strategy changes **one** of them. That's why the difference
> wasn't visible — the 5% difference is just run-to-run variance."

### "How did pessimistic become faster?"

This follow-up will definitely come. The answer is in the code:

> "For the losers arriving after the seat is booked:
>
> - **optimistic** — `UPDATE ... WHERE version=?` runs, 0 rows match,
>   then rollback. The write statement still ran.
> - **pessimistic** — `SELECT ... FOR UPDATE` (lock is free, acquired
>   immediately), check status, found `booked`, return. **No UPDATE at all.**
>
> Meaning for losers, there is less work in the pessimistic path. There is no blocking, because the winner commits in milliseconds."

### ⭐⭐ "So why keep optimistic?"

**This is the most important answer of this entire section.** The mistake here is to twist the numbers to prove your decision right. Do the opposite:

> "Not because of speed — the numbers didn't show a speed difference.
> Because of the **failure mode**.
>
> In optimistic, the losing request exits immediately and releases its DB connection. In pessimistic, it stands in line and **holds** the connection. There are 40 connections in the pool.
>
> This didn't show up in my benchmark, and I don't claim it would — because the winning transaction commits in ~2ms, so no one waits. The cost of pessimistic grows with the time the lock is held.
>
> The danger is that time can grow — an external call, a slow query, a large report in the transaction. Then pessimistic turns directly into pool exhaustion, while optimistic's behavior remains the same.
>
> In one line: I didn't choose optimistic because it's faster today — it isn't. I chose it because it won't be bad tomorrow."

### "Did you find any bugs in the benchmark?"

Admit it yourself — this is the strongest thing in this section:

> "Yes, and it saved me from writing a completely wrong conclusion.
>
> In the first micro-benchmark run, I wrote code to clear rate limit buckets with the wrong prefix — `ratelimit:*`, while the real prefix is `rl:`. The buckets never cleared, and from the fourth round, every request started getting 429s. Those 429 latencies were blending into the numbers.
>
> My `errors` column caught this — which I kept only for sanity. If I had just printed p50/p99, the numbers would have **looked perfectly fine** and I would have written a doc on it.
>
> Lesson: always keep an invariant check in benchmarks — 'exactly one booking should win in every round' — don't just print timings."

### "Anything else found?"

> "Query counting caught a real inefficiency that had nothing to do with locking: `pricing_state()` runs **twice** in every booking — once to extract the price, once for the WebSocket broadcast. Meaning 6 useless queries per booking.
>
> I haven't fixed it yet because the benchmark numbers would change. It's written as a follow-up on my roadmap."

### Rapid fire

| Question | One-line answer |
|---|---|
| Did the benchmark run production code or a copy? | The same `_perform_booking()`. If I made a separate endpoint, I would be measuring something that isn't deployed |
| Is the strategy switch exposed in production? | No. `BENCHMARK_MODE=false` ignores the query param — a param that changes locking semantics is a footgun |
| Isn't turning off Redis cheating? | It's the only way to see the DB layer alone. And "Redis stops 1432/1433" is a finding itself |
| Did you check for ordering bias? | Yes — ran it with the order reversed, same result |
| Risk of deadlock? | Not here, only one row is locked. If I added multi-seat booking, I would have to lock in a consistent order |

---

## 13. Multi-worker + CI — "what isn't verified is just hope"

### "What changes when you deploy to multi-worker?"

> "I did it, and found two things.
>
> **First — what I claimed was finally verified.** In Phase 5, I broadcast via Redis pub/sub, not an in-memory dict, claiming 'memory isn't shared in multi-worker'. But in dev, only one worker ever ran, so that was never tested.
>
> Now it's tested: connect 12 WebSocket clients, prove they are on different processes via `/api/health`'s `worker_pid`, book a seat — and see that everyone got the update. They do.
>
> **Second — the config that was fine on one worker broke on four.** Each worker is its own process, with its own connection pool. `4 x (20 + 20) = 160 connections`, and Postgres's default limit is 100. The pool now comes from env and is 5+5 in prod, meaning 40 total."

### "What goes wrong with in-memory broadcast?"

This is the concrete answer to the follow-up:

> "Only the clients of the worker where the booking happened get the message. The other 8-9 clients stay silent, and that seat **stays green** on their grid even though it's sold. That user gets a 409 on the next click — and thinks the app is broken.
>
> This bug never reproduces on a single worker. That's why I put it in the test, not in a comment."

### ⭐ "Did CI provide any real benefit?"

**This is the strongest answer in this section.**

> "Yes — CI caught three bugs that had been in the code for months. All three were hidden because my local database was old. CI always starts with an empty volume.
>
> **One:** `seed.py` generated user numbering based on the COUNT of users. After named accounts (demo/organizer/admin) were created, the counter started at 3, so `user1` and `user2` were never created. Tests log in with them — the fixture was skipped. Result: **31 passed, 35 skipped**, and it looks GREEN in CI.
>
> **Two:** the seeded event's `organizer_id` was NULL. Gate check-in returned 403. This wasn't just a test failure — the demo data itself was broken; the event didn't even appear in the organizer portal.
>
> **Three:** two tests used fixed idempotency keys. That key stays alive in Redis until TTL, so the next run gets a replay on the same key: 201 is returned but no new booking is created."

### "How did you debug?"

Admitting the first guess was wrong sounds very good:

> "The third bug appeared on the prod multi-worker stack for the first time, so my first suspicion was multi-worker. I was wrong — it was a test isolation issue.
>
> That's why now I first ask 'does this happen on a single worker too?' All three times, the real reason was something else."

### Rapid fire

| Question | One-line answer |
|---|---|
| Why not GitHub's `services:` block? | It only gives DB/Redis, the app runner runs separately — meaning CI tests something that isn't deployed. I run the same compose that runs on my laptop |
| What's different in the prod image? | Non-root user, no dev tools, no `--reload`. CI asserts both properties, doesn't just build |
| Frontend prod image size? | 74MB (nginx + built assets) vs 407MB dev. `node_modules` and source aren't there |
| Most important line in nginx? | `try_files $uri $uri/ /index.html` — without it, refresh on `/events/3` returns 404 |
| Why not cache `index.html`? | That file tells you the new asset names. If cached, the user will ask for old assets that don't exist after deploy — blank page |
| Sticky sessions needed for WebSockets? | No. Every worker subscribes to Redis itself, so the client can go to any worker |
| Is "passed" enough in CI? | No — **skip count** too. "31 passed, 35 skipped" looks perfectly green |

---

## 14. ⭐ Group booking — "all or nothing"

This is the best section of the entire project, because there is a place
where my own default **didn't work** — and I chose it after benchmarking.

### "What's new in group booking?"

> "Until now, every correctness question in the project had the same face: **one seat, one booking**. Redis lock, version column, partial unique index — all three were answers to that one question.
>
> In groups, the question changes: **all or nothing, across N separate payments.** Every payment arrives at its own time, from a different user, a different browser — and the deadline is ticking.
>
> 3 paid, the 4th did not, and the deadline was reached. Giving seats to the three and not the fourth defeats the purpose of the group. They came to sit together. Therefore, the group is dissolved, seats are released, and money is refunded to the three."

### "Couldn't you just create N bookings and join them with a `group_id`?"

This is the first suggestion that always comes:

> "No, because **booking means 'the seat is secured'**. In a group, no seat is secured until everyone pays.
>
> So I needed an intermediate state — seats held, some money paid, decision pending. That's a new seat status (`group_held`) plus two tables. Bookings are only created when the group is confirmed, and then all at once."

### "Couldn't you reuse the `locked` status?"

Seems like a small question, the answer is big:

> "It can't be. Lazy cleanup quietly makes `locked` seats `available` when the TTL expires.
>
> That's wrong for group seats — some of those people have **already paid**. Releasing them also means refunding, and refunding is a decision, not a side-effect.
>
> That's why expiry happens via a cron job, not lazy cleanup: if someone never opens that event's page, lazy cleanup never runs and people are left waiting for their money. **Getting money back cannot depend on a stranger opening a page.**"

### ⭐⭐ "What was the hardest race?"

**This is the strongest answer in this entire project. Keep it ready.**

> "The last person is paying exactly when the expiry job is breaking the group. One needs to confirm, the other needs to break.
>
> I applied the same pattern as the rest of the project — atomic conditional UPDATE. **And it didn't work.**
>
> Reason: conditional UPDATE is only enough when both racers are deciding on **the same row**. Here, the payment thread changes a row in `group_shares` and the expiry job changes a row in `group_bookings`. A conditional UPDATE on one row cannot stop a race on another row.
>
> Result that actually appeared in the test: a `paid` share in an expired group. That person's money was deducted, seat not received, refund didn't happen — the worst possible outcome.
>
> Fix: `SELECT ... FOR UPDATE` on the group row. Now both transactions are serialized."

**Follow-up that will definitely come — "but you chose optimistic in the benchmark?"**

This answer connects both points:

> "Yes, and both points go together.
>
> The result of Phase 15 was: **throughput** difference isn't measurable, and the danger of pessimistic is that its cost grows with lock hold time.
>
> Here I'm using pessimistic **not for speed, but for correctness** — because two separate rows need to be serialized, and there is no optimistic version for that. And the lock stays for ~1ms, so the Phase 15 danger doesn't apply.
>
> Meaning optimistic is the default, and this is a deliberate exception — not a blind rule that 'pessimistic is bad'."

### "How did you catch this bug?"

> "Wrote a race test that runs 20 times, starts both threads at the same moment with a barrier, and checks invariants in every run: if the group is `confirmed`, all seats booked; if `expired`, all available and paid shares refunded; and it should **never** be stuck in `collecting`.
>
> The bug appeared 1 in 20 times. A normal test would never catch it.
>
> Learned one more thing: the expiry was ALWAYS winning, because it was a direct function call and payment went through the entire HTTP stack. Meaning half the path wasn't being tested. Added a bit of random jitter to expiry, then both outcomes started appearing — ~48 confirmed, ~12 expired in 60 runs, and zero invariant violations."

### "Anything else found?"

> "Yes, and the same race test found it. When the group broke, its shares' **pending payments were left hanging**.
>
> Two results: first, no new checkout could be created for that seat — the partial unique index blocked it. The seat appeared `available` but couldn't be bought. That's the worst kind of bug, because everything looks fine in the UI.
>
> Second, a user could complete an old checkout page and pay money to a **dead group**."

### "Is this feature complete?"

Answer honestly, it sounds good:

> "No, and I don't claim it is. I intentionally left three things out:
>
> **Refund only writes the status**, doesn't call the real refund API. In the real gateway, refund confirmation also comes via webhook — meaning I need one more state called `refund_pending`, just like payment.
>
> **No email notification** — 'your friend paid', '1 hour left'. The outbox pattern is already there, so it will be easy to add.
>
> **No grace period** — the deadline is strict."

### Rapid fire

| Question | One-line answer |
|---|---|
| Why not address by group id? | `share_token` (`secrets`) — sequential IDs would allow anyone to run 1, 2, 3 and see others' groups |
| Why no email in response? | The link can go to anyone; showing all members' emails there is a privacy leak |
| Can one user take two shares? | No — otherwise one person would claim the whole group and the meaning of "split" would be gone |
| Can group creation be partial? | No. If even one seat isn't secured, full rollback — partial holds are useless |
| When is the price frozen? | When creating the group. Surge can change in 30 minutes (same principle as Phase 14) |
| How often does the cron run? | Every 30 seconds, with `run_at_startup`. The deadline is in minutes, so 30s is enough |
| What if two workers expire at once? | `break_group` runs via atomic conditional UPDATE — only one will break it |

---

## 15. Seat layout — "don't break old data"

Small feature, but there is a question that interviewers often ask.

### "What do you do with old rows when adding a new column?"

> "In Phase 18, I added `Event.layout` and `Seat.section`, both **nullable**. And that's not convenience, it's the entire design.
>
> 17 phases of data — seeds, tests, demo events — were created without layout. `NULL` means 'old uniform event', and the frontend renders it exactly as it did before.
>
> There is a separate test for this: take event 1, check that `layout` is null, there are 100 seats, every seat's `section` is null, and everything else is as it was. This is the thing that breaks most easily when adding a column — and it's only noticed much later."

### "Did you remove the old API format?"

> "No. `price_tiers` still works.
>
> But I didn't keep two generators — they start behaving differently over time. I **convert** `price_tiers` into the layout shape, and then both go through the same expansion function.
>
> Benefit: an event created from tiers also stores the layout, so the grid renders every event the same way."

### ⭐ "Where do you validate?"

> "In two places, and both have different jobs.
>
> **Pydantic** looks at the shape — types, lengths, ranges. **`layout.py`** looks at business rules — duplicate row labels, seat cap, aisle position within the row.
>
> Separated because rules like 'same row label cannot exist in two sections' need the context of the entire layout, and it should be easy to test them without the DB.
>
> The most important rule is the duplicate row label one. There is a `UNIQUE(event_id, row_label, seat_number)` on `seats`. If this check didn't exist, expansion would die with an IntegrityError **after inserting 500 seats**."

### "Can a half-built event be created?"

> "No, and that's intentional. `expand()` doesn't touch the DB — it just returns a list. The caller inserts it in bulk in one transaction.
>
> If `expand` wrote itself, 'half seats created then error' would be possible. The test checks that the count of events for the organizer remains the same after a bad layout."

### "Why didn't you build drag-and-drop?"

This question will come, and "no time" is the wrong answer:

> "Because real venues are built in rows and sections. '12 seats in Row C, aisle after seat 4' **typing** is faster and more error-proof than dragging 12 boxes with a mouse.
>
> And drag-and-drop brings a mountain of pointer-events, undo/redo, snapping, and touch handling — for a feature used a few times a year.
>
> Instead, I kept a **live preview** — the exact shape the attendee will see. That's the real benefit of a builder: it's hard to think of 40 seats and 4 aisles in numbers, it's immediately clear when you see it."

### Rapid fire

| Question | One-line answer |
|---|---|
| Is an aisle a seat? | No — no seat is created, numbering doesn't stop. Purely for visuals, that's why it's in layout JSON, not the seats table |
| `section` is in layout JSON, why on the seat? | Ticket PDF and gate check-in need the section — parsing the layout for them would be wrong |
| Can the layout be edited? | No. Changing seats means breaking references to bookings — same rule as base price |
| Is client-side validation redundant? | It is, and intentional. Server makes the real decision; client only saves a round-trip |
| What if layout and seats differ? | Seats are the truth — bookings are tied to them. Layout is just a map |

---

## 16. ⭐ AI feature — "how much work should you give an LLM?"

AI is everywhere today, and interviewers often ask with skepticism. A good answer sounds very good.

### "Where did you use AI?"

> "Only in one small place: converting natural language into filters.
>
>     '3 seats together under 1500 near the stage'
>              |
>              v   <- LLM only does this
>     SeatFilters(quantity=3, together=True, max_price=1500,
>                 row_preference='front')
>
> Everything after that is normal code — matching, ranking, availability. No model."

### ⭐⭐ "Why didn't you have the LLM write SQL directly?"

**This question will come, and the answer is in three parts:**

> "**Security.** The model's output never becomes SQL. It becomes a validated Pydantic object, and the query remains parameterized. So prompt injection can at most create weird FILTERS — which the user sees anyway — not data leaks or SQL injection. I tested two injection queries, both fell into 'didn't understand'."
>
> **Testability.** Not one of the 15 tests for this feature needs an API key. This is necessary because if the key is missing, those tests are SKIPPED — and skipped tests look green in CI.
>
> **Reliability.** Key missing, model down, timeout — normal filters still work. The search box just doesn't appear."

### "How did you handle structured output?"

> "Used Gemini's `responseSchema`. Give the schema, the model must answer in that shape.
>
> 'Please return JSON' prompt-engineering breaks eventually: the model adds markdown fences or adds an explanation, and then you have to handle parsing failures. That's useless code when the API itself can guarantee it.
>
> And `temperature: 0` — this isn't creative work. One input should always have one answer."

### ⭐ "Which model, and why?"

Give the measurement-based answer:

> "I measured both:
>
>     gemini-3.5-flash        8.5s
>     gemini-3.1-flash-lite   1.7s     <- chose this
>
> The output was exactly the same. The job is 'convert a line to JSON' — for that, a large thinking model is 5x latency and more money, zero benefit.
>
> And I pinned the version, not `-latest` — that would automatically move to a new model and then prompt behavior could change without a deploy."

### ⚠️ "Did you find any security bugs?"

**Admit it yourself — this is the strongest answer in this section:**

> "Yes, and I created it myself. The first version sent the API key in the query param. Then a 404 from a wrong model name appeared and this was printed in the logs:
>
>     Client error '404 Not Found' for url
>     'https://...:generateContent?key=AQ.Ab8RN6...'
>
> **API key straight in the logs.** httpx's exception message contains the full URL — meaning any error writes the key to the log file, and log aggregators go to backups, and they aren't secured separately.
>
> Fixed in two places: the key now goes in the header, and the exception object isn't logged at all — only the status code. The second fix is necessary even without the first, because someone could add the param back tomorrow.
>
> Lesson: putting secrets in the URL is always wrong, even with HTTPS. It appears in browser history, proxy logs, access logs — everywhere."

### "Did the model ever misunderstand?"

Say yes honestly:

> "Yes. It turned 'two cheapest seats' into `min_price=800`.
>
> 'Cheapest' is a **sort preference**, not a filter — and results show cheapest first anyway. Applying `min_price` has the exact opposite effect.
>
> Fixed in the prompt, not the code — had to add an explicit rule. This is the reality of LLM features: 'it works' and 'it works correctly' are different things, and you only know by running real queries."

### "How did you control costs?"

| Thing | Why |
|---|---|
| Rate limit per-user | Every query is a paid call. Without a limit, someone could run a loop and exhaust the quota — and the feature stops **for everyone** |
| Redis cache 1 hour | "2 seats under 1000" is written by many, meaning it never changes. Repeat query 0.0s |
| Query max 200 chars | No real search happens beyond this — just an attempt to fill the prompt with garbage |
| Login required | Seats are public, but rate limits are per-user and the cost must be attributed to someone |


### ⭐ "Do you also have the AI write event copy — what if it writes something wrong?"

This is the Phase 20 question, and the answer has two parts:

> "**First:** AI never reaches the publish button. The endpoint saves nothing — it only returns a draft that fills the organizer's form, and they edit and publish it themselves.
>
> Reason: event description is a **promise** to the ticket buyer. A human hand must be on it. There's a test for this — requesting a draft shouldn't increase the event count.
>
> **Second:** I explicitly forbade the model from making up facts in the prompt — lineup, duration, price, ratings, awards, 'sold out'. This list isn't random: **these are the things a marketing LLM makes up first**, because they sound like a 'good listing'.
>
> And the UI clearly states that a machine wrote this and it must be read before publishing."

### "What temperature did you keep?"

This is a good follow-up because the answer is different in two places:

> "0 for the search parser, 0.8 for the copy draft.
>
> In the parser, one input should always have one answer — randomness is a bug there. In copy, it's the opposite: getting different options on two clicks is a benefit.
>
> Both are in the same file and both have reasons written, otherwise someone might make them the same in the name of 'consistency'."

### ⚠️ "You also built a poster generator, right?"

**Admitting the truth is the best answer here:**

> "No. It was in the plan, but Gemini's free tier has no quota for image generation — I tried all five image models, all return 429, while text models work fine with the same key.
>
> So there were two paths: write the feature and say 'it's built', or don't build it and write the reason. I chose the second, and explicitly wrote 'not built' in the README with that reason.
>
> A feature you've never seen running will fail the first time in a demo — and then not just the feature, but the rest of the README will be doubted."


### Rapid fire

| Question | One-line answer |
|---|---|
| What if AI is down? | Search keeps working — filters work without AI too. Only NL input stops |
| Confidence score? | No. Gemini doesn't give it, and making up a score is a lie |
| Follow-up query ("show me cheaper")? | No — that needs session state and the cache benefit is gone |
| Can AI book directly? | No. Clicking the result SELECTs the seat; the user books it. I didn't put AI in the money-deducting path |
| What if AI gives wrong filters? | Pydantic clamps it (quantity 1-10, price bounds). And filters are visible to the user, so errors are caught immediately |
| Can AI copy publish? | No — fills the draft form, organizer publishes. Description is a promise to the attendee |
| Poster generator? | Not built — no image quota in free tier (429). Wrote "not built" in README with reason |

---

## 17. Traps — where saying "yes" is wrong

### "Couldn't you use Kafka?"

> "I could, but it's a bad fit here. Kafka is needed when events **must be durable and replayable** — like a payment ledger.
>
> Here, seat locks live for 5 minutes and die. Replaying them makes no sense. Redis pub/sub is the right size tool for this job, and it was already in the stack.
>
> Yes, when I add payments — then the real need for a durable event log will arise."

### "Why didn't you break it into microservices?"

> "Because this is one team, one deployment, and one database system. Building microservices would have invited the pain of distributed transactions — and my entire core problem is transactional consistency. It wouldn't have made it easier, but harder.
>
> I kept a modular monolith — routers are separate, layers are clean. It will be easy to extract if needed."

### "Couldn't you have made it simpler?"

> "I could have dropped one layer — Redis. And it would still be correct with version + unique index. But then every request would hit the DB.
>
> If I remove either of the other two layers, I lose either correctness or insurance. All three are intentional, not duplicates of each other."

---

## 18. Rapid fire

| Question | One-line answer |
|---|---|
| What states can a seat be in? | available, locked, booked — check constraint is in the DB |
| How long is the lock? | 5 min, in config, via Redis TTL |
| Same user in two tabs? | Second tab gets 200 with `already_owned`, no new lock |
| Seat on booking cancel? | Booking row becomes `cancelled`, not deleted — partial index is only on `confirmed`, so seat can be sold again |
| Migration tool? | Alembic, and I read the autogenerated file before applying |
| How many tests? | 29 — auth, RBAC, rate limit, concurrency. With real HTTP, no mocks |
| Why no mocks? | Race conditions don't appear in mocks. The bug the load test caught would never be caught by a mocked test |
| How to stop bots? | Redis token bucket, per-user and per-email — not per-IP, that's the edge's (nginx/CDN) job |
| How are roles handled? | Three flat roles + `require_role`. But role check and **ownership** check are separate — being an organizer doesn't make every event yours |
| Double-click booking? | `Idempotency-Key` — if the same key arrives again, no new work, return the first answer |
| Where do frontend counts come from? | Derived from the seats array, not the server — updates automatically on WebSocket update |
| Redis persist in Docker? | No, intentional. Only temporary locks |
| Is there CI? | Not yet, on the roadmap |

---

## 19. Whiteboard — build the architecture like this

Build it in this order, while speaking:

```
1.  Browser ─── HTTP ──▶ FastAPI
2.                        │
3.                        ├──▶ Redis    (lock: SET NX EX 300)
4.                        │
5.                        └──▶ Postgres (version + unique index)
6.
7.  Browser ◀── WebSocket ── FastAPI ◀── Redis pub/sub
```

Three things to mention while speaking:
1. **Redis first** — "4999 out of 5000 stop here"
2. **Postgres final decision** — "atomic UPDATE and unique index"
3. **Pub/sub arrow** — "this is how it works on multi-worker"

---

## 20. What you should ask

The interview is two-way. Asking these shows you think about production:

- "How do you catch concurrency issues in production — is load testing in the pipeline or do you find out after an incident?"
- "What is the biggest scaling bottleneck in your system right now?"
- "Do you use optimistic locking anywhere? How do you decide when pessimistic is needed?"
- "How long does it take for a new engineer to reach production?"

---

## Final word

There are three things that set this project apart from normal projects. Every interview must include these three:

1. **Layered defense, and the reason for each layer** — the "Redis for speed, DB for correctness" line
2. **Numbers** — 200 users, 8154 requests, 0 failures, exactly 1 booking
3. **Three bugs found via load test** — especially the `pg_stat_activity` debugging one

And one line you should never forget:

> "Correctness shouldn't be assumed, it must be measured."

---

## Related

- [roadmap.md](roadmap.md) — what's built, what's left
- [Phase 4 — Redis Locking](phases/04-redis-locking.md) — locking design
- [Phase 6 — Load Testing](phases/06-load-testing.md) — load test and first bug
- [Phase 7 — Auth + Google OAuth](phases/07-auth-google-oauth.md) — auth + remaining two bugs
- [Phase 14 — Dynamic Pricing](phases/14-dynamic-pricing.md) — full price lock design
- [Phase 15 — Locking Benchmark](phases/15-locking-benchmark.md) — full numbers and method
- [Phase 16 — Multi-Worker + CI](phases/16-multiworker-ci.md) — deploy config and three bugs
- [Phase 17 — Group Booking](phases/17-group-booking.md) — full "all or nothing" design
- [Phase 18 — Seat Layout](phases/18-seat-layout.md) — nullable columns and backwards compatibility
- [Phase 19 — NL Seat Search](phases/19-nl-seat-search.md) — AI boundary and key leak bug
- [Phase 20 — AI Event Copy](phases/20-ai-event-copy.md) — "don't make up facts", and why no poster generator
- [testing.md](reference/testing.md) — commands to demo everything
