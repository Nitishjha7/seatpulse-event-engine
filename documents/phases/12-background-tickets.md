# Phase 12 — Background Queue + QR + PDF Ticket + Email

Follow-up to [11-payments.md](11-payments.md).

**Implemented:** ARQ worker, QR code, PDF ticket, and email — all processed **outside** the request cycle.

---

## The problem this solves

After a booking is confirmed, three tasks must occur:

| Task | Time |
|---|---|
| QR code generation | ~10ms |
| PDF rendering | ~80ms |
| Email dispatch | 1-3 seconds (SMTP, network) |

Totaling **2-3 seconds**. If performed within the request:

- The user perceives the payment as hanging, despite the money being deducted and the booking created.
- If SMTP is down, the booking **fails**, which is unacceptable.
- During flash sales, every booking blocks a connection for 3 seconds, causing the connection pool to collapse (as seen in Phase 7).

Now, the API immediately returns `201 confirmed`, and the ticket is generated in the background. The user sees **"Ticket..."**, which is accurate.

---

## Concept — which tasks belong in the background

Simple rule:

> **Does the user need the result of this task RIGHT NOW?**

| Task | Needed now? | Location |
|---|---|---|
| Seat booking status | Yes — they cannot proceed without it | Request |
| Payment status | Yes | Request |
| PDF ticket | No — it arrives via email and can be downloaded later | Queue |
| Email | No | Queue |
| Analytics/logs | No | Queue |

Another perspective — **if this task fails, should the entire request fail?**

For tickets, the answer is **no**. The booking is complete, and the payment is processed. The ticket can be generated later. Therefore, it goes to the queue.

---

## Step 1 — Why ARQ, not Celery

| | Celery | ARQ ✅ |
|---|---|---|
| Broker | RabbitMQ or Redis | Redis — **we already have it** |
| Model | Sync-first | asyncio-native, matches our ASGI app |
| Size | Large ecosystem, complex config | ~1500 lines, minimal config |
| Fit | Complex workflows, priorities, chains | Simple background jobs |

> Celery is only necessary for multiple queues with priorities, complex workflows (chains/groups), or if the team requires its ecosystem. **It would be over-engineering at this stage.**

Practical benefit: no new services. Redis is already used for locks, rate limiting, idempotency, and pub/sub — now it handles the job queue as well.

---

## Step 2 — Worker uses the same image, different command

```yaml
worker:
  build: ./backend          # ⭐ same image as the backend
  command: arq worker.WorkerSettings
  volumes:
    - ./backend:/app
    - ticket_data:/app/tickets
```

Creating a separate Dockerfile would require duplicating models, config, and database logic or managing shared volumes. Using the same image ensures the worker has the **exact same code** as the API. One build, two commands.

### Why a shared volume is required

The worker **writes** the PDF, and the API **serves** it. Since they are separate containers, a named volume is required to share files; otherwise, the API cannot access the file.

---

## Step 3 — Enqueue must never fail

```python
def enqueue_ticket(booking_id: int) -> None:
    try:
        asyncio.run(_enqueue("generate_ticket", booking_id))
    except Exception as exc:
        logger.warning("Ticket job not queued — booking %s: %s", booking_id, exc)
```

⚠️ This **never raises an exception**, by design.

It is called **after** the booking is successful. If Redis is down and we raise an error, the user receives a 500 error despite their money being deducted and the booking existing in the database. **That is the worst possible outcome.**

On failure, the booking remains in `ticket_status = pending`, and `retry_pending_tickets.py` picks it up later.

> This follows the same logic as the Phase 5 WebSocket broadcast: notifications are "nice to have," while bookings are "must have."

---

## Step 4 — Job is idempotent

```python
if booking.ticket_status == TICKET_READY and booking.qr_token:
    logger.info("Booking %s ticket already ready — skipping", booking_id)
    return booking.qr_token
```

The job may run twice — ARQ performs retries, and the retry script may re-queue jobs.

⚠️ Without this check, the **QR token would change**, rendering the user's existing ticket invalid. This small check prevents a significant issue.

> This theme of idempotency appears in Phase 9 (keys), Phase 11 (webhook fulfillment), and now here. Anything that can run multiple times must be designed to do so safely.

---

## Step 5 — ⚠️ QR does not contain booking ID

```python
def new_qr_token() -> str:
    return secrets.token_urlsafe(24)
```

Booking IDs are **sequential**. If we put them in the QR, anyone could generate a QR for 1, 2, 3... and gain entry — breaking the entire ticketing system.

`token_urlsafe(24)` generates 32 characters, making it practically impossible to guess. There is also a unique index on the column.

We have a **test** for this — `test_qr_token_is_not_the_booking_id`.

---

## Step 6 — Why it runs in a thread

```python
async def generate_ticket(ctx, booking_id):
    return await asyncio.to_thread(_generate, booking_id)
```

ARQ runs on asyncio, but our tasks are **synchronous** (SQLAlchemy and reportlab). Calling them directly would **block the entire event loop** during PDF rendering, preventing the worker from picking up other jobs.

`to_thread` offloads the task to a separate thread, keeping the loop free.

### Retry config

```python
max_tries = 3
retry_delays = [5, 30]     # increasing gap
job_timeout = 60
max_jobs = 5               # PDF rendering is CPU-bound; more is inefficient
```

Raising an exception in the job is **fine** — ARQ handles retries. However, once all attempts are exhausted, we mark the booking as `failed`; otherwise, the user would see "generating..." indefinitely.

---

## Step 7 — Email: outbox pattern

```python
def send_ticket_email(*, to, subject, body, pdf, booking_id):
    eml = OUTBOX_DIR / f"booking-{booking_id}.eml"
    eml.write_text(...)
    logger.info("📧 Ticket email queued for %s", to)
```

⚠️ This is not a real SMTP implementation — there are no credentials, and sending real emails from a portfolio project is inappropriate.

Instead, emails are **written to disk**. Django’s file/console email backend works similarly in development.

**To implement real SMTP, only this function needs to change** — the queue, retry logic, and status tracking remain identical. This is why it is kept as a separate function.

> This follows the same pattern as the mock payment provider and "hide button if no Google credentials": the feature degrades gracefully without breaking the app.

---

## Step 8 — Frontend: download via blob

```js
const blob = await downloadTicket(booking.id)
const url = URL.createObjectURL(blob)
const a = document.createElement('a')
a.href = url
a.download = `SeatPulse-${bookingRef(booking.id)}.pdf`
a.click()
URL.revokeObjectURL(url)
```

⚠️ **`window.open` will not work.** The endpoint requires an `Authorization` header, but browser navigation does not support custom headers — the user would get a 401.

Therefore, we fetch the data, create a blob, and trigger a hidden `<a>` click. `revokeObjectURL` is necessary to prevent memory leaks.

UI has three states:

| `ticket_status` | Display |
|---|---|
| `pending` | "Ticket…" (pulsing) |
| `ready` | 🎫 Ticket — download button |
| `failed` | Retry button |

---

## ✅ Proof

### 1. Booking immediate, ticket background

```bash
curl -X POST -H "Authorization: Bearer $T" -d '{"seat_id":12}' .../api/bookings
# {"id":227, ...}     — immediate, no wait
```

Worker logs:
```
INFO Ticket is being generated — booking 227 (attempt 1)
INFO 📧 Ticket email queued for demo@seatpulse.dev (outbox: booking-227.eml)
INFO ✅ Ticket ready: booking 227, seat B-2
04:48:01: 0.10s ← generate_ticket ● '6NTQE81XpTnebRSFPMFceDdV24hGAUC3'
```

### 2. PDF is valid

```bash
curl -o t.pdf -w "HTTP %{http_code} type=%{content_type} size=%{size_download}\n" \
  -H "Authorization: Bearer $T" .../api/bookings/227/ticket
# HTTP 200  type=application/pdf  size=7383

head -c 8 t.pdf | xxd
# 00000000: 2550 4446 2d31 2e34    %PDF-1.4
```

### 3. ⚠️ Cannot access others' tickets

```bash
curl -o /dev/null -w "HTTP %{http_code}\n" -H "Authorization: Bearer $T2" \
  .../api/bookings/227/ticket
# HTTP 404
```

The ticket contains a QR code — downloading someone else's ticket would be equivalent to free entry.

### 4. Email outbox

```
To: demo@seatpulse.dev
Subject: Your ticket for Arijit Singh Live — seat B-2
X-Attachment: ticket-227.pdf (7383 bytes)

Hi Demo User,
```

### 5. Retry script

```bash
docker compose exec backend python retry_pending_tickets.py
# INFO All tickets are correct — nothing to retry
```

### 6. Test suite

```
42 passed in 35.02s
```

5 new tests — pending state, end-to-end generation, **others' tickets (404)**, auth required, and **QR token non-sequentiality**.

---

## Bugs / gotchas encountered

### 1. Migration gotcha (third occurrence)

```
[SQL: ALTER TABLE bookings ADD COLUMN ticket_status VARCHAR(16) NOT NULL]
```

Without `server_default`, a NOT NULL column cannot be added to existing rows. This is the **third time** this has happened (role, is_active, now ticket_status).

> **New Rule:** Adding a NOT NULL column to a table with existing rows? Provide `server_default`, then `alter_column(..., server_default=None)`.

### 2. `import database` failing in tests

Tests are in `tests/` without an `__init__.py` — in this case, pytest only adds `tests/` to `sys.path`, not `/app`.

Fix: `conftest.py` in the backend root that inserts the path. (`tests/__init__.py` is another way, but it turns test files into a package, potentially causing name clashes.)

---

## Interview questions

| Question | Answer |
|---|---|
| "Which tasks do you put in the background?" | I ask two questions: Does the user need the result now? And if it fails, should the whole request fail? For tickets, the answer to both is "no." |
| "Why not Celery?" | Redis was already present, the app is ASGI, and we needed simple jobs. ARQ matches all these. Celery is for priorities or complex workflows. |
| "What if the job runs twice?" | It is idempotent — if ready, it skips. Without this, the QR token would change, invalidating the user's old ticket. |
| "What if Redis is down during enqueue?" | Enqueue swallows the exception. The booking is done, payment is taken — returning a 500 would be the worst outcome. The ticket stays `pending` and the retry script picks it up. |
| "What if the worker crashes mid-job?" | ARQ does not mark the job as complete, so it runs again. Since the job is idempotent, it is safe. |
| "What is in the QR?" | A random 32-char token, not the booking ID. Sequential IDs would allow anyone to generate others' QRs. |
| "How do you send emails?" | Currently an outbox pattern — written to disk. To add SMTP, only one function changes; the flow remains the same. |
| "How do you scale the worker?" | Run more worker containers — ARQ pulls jobs from Redis, no coordination needed. `max_jobs` controls per-worker concurrency. |

---

## Common Problems

| Problem | Fix |
|---|---|
| Ticket always "pending" | Is the worker running? `docker compose logs worker` |
| `ModuleNotFoundError: arq` | `docker compose up -d --build backend worker` |
| Download 409 "not ready" | Worker is still generating — wait 1-2 seconds |
| Download 409 "file not found" | Volume was cleared. Endpoint re-queues automatically, try again |
| Worker `import` errors | Worker and backend use the same image — rebuild both |
| Tests `ModuleNotFoundError: database` | Need `backend/conftest.py` |

---

## Files

```
backend/
├── tickets.py                  ← new ⭐ QR + PDF + email outbox
├── worker.py                   ← new ⭐ ARQ worker + job
├── job_queue.py                ← new (enqueue, never raises)
├── retry_pending_tickets.py    ← new (safety net)
├── conftest.py                 ← new (pytest sys.path fix)
├── models.py                   ← qr_token, ticket_status, ticket_generated_at
├── schemas.py                  ← BookingDetail includes ticket_status
├── routers/bookings.py         ← ticket download + retry, enqueue
├── routers/payments.py         ← enqueue after fulfillment
├── requirements.txt            ← arq, qrcode, reportlab
├── tests/test_concurrency.py   ← 5 new tests (37 → 42)
└── alembic/versions/...        ← ticket columns

frontend/src/
├── api.js                      ← downloadTicket (blob), retryTicket
└── components/BookingsList.jsx ← TicketAction (3 states)

docker-compose.yml              ← worker service + ticket_data volume
.gitignore                      ← tickets/
```

---

## Related

- [11-payments.md](11-payments.md) — source of bookings
- [05-websockets.md](05-websockets.md) — "nice to have vs must have" logic
- [09-rate-limit-idempotency.md](09-rate-limit-idempotency.md) — first instance of idempotency
- [../reference/testing.md](../reference/testing.md) — test commands
