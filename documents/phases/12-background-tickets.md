# Phase 12 — Background Queue + QR + PDF Ticket + Email

[11-payments.md](11-payments.md) ke baad ka kaam.

**Kya bana:** ARQ worker, QR code, PDF ticket, aur email — sab request ke **bahar**.

---

## The problem this solves

Booking confirm hone ke baad teen kaam karne hain:

| Kaam | Time |
|---|---|
| QR code generate | ~10ms |
| PDF render | ~80ms |
| Email bhejna | 1-3 second (SMTP, network) |

Mila ke **2-3 second**. Ye request ke andar karte to:

- User ko lagta payment atak gaya — jabki paisa kat chuka hai aur booking ban chuki hai
- SMTP down ho to booking **fail** ho jaati, jo bilkul galat hai
- Flash sale me har booking ek connection 3 second block karti — pool wahi phat jata jo Phase 7 me phata tha

Ab API turant `201 confirmed` deti hai, aur ticket background me banta hai. User ko **"Ticket…"** dikhta hai, jo sach bhi hai.

---

## Concept — kaunsa kaam background me jaana chahiye

Simple rule:

> **Kya user ko is kaam ka nateeja ABHI chahiye?**

| Kaam | Abhi chahiye? | Kahan |
|---|---|---|
| Seat book hui ya nahi | Haan — bina iske wo aage badh hi nahi sakta | Request me |
| Paisa kata ya nahi | Haan | Request me |
| PDF ticket | Nahi — wo email me aayega, aur baad me download bhi kar sakta hai | Queue |
| Email | Nahi | Queue |
| Analytics/logs | Nahi | Queue |

Aur ek dusra angle — **agar ye kaam fail ho jaye, to kya poori request fail honi chahiye?**

Ticket ke liye jawab **nahi** hai. Booking ho chuki hai, paisa kat chuka hai. Ticket baad me ban sakta hai. Isliye wo queue me jata hai.

---

## Step 1 — ARQ kyu, Celery kyu nahi

| | Celery | ARQ ✅ |
|---|---|---|
| Broker | RabbitMQ ya Redis | Redis — **hamare paas pehle se hai** |
| Model | Sync-first | asyncio-native, hamari ASGI app se match |
| Size | Bada ecosystem, bahut config | ~1500 lines, config chhota |
| Fit | Complex workflows, priorities, chains | Simple background jobs |

> Celery tab chahiye hoga jab multiple queues with priorities, complex workflows (chains/groups), ya team ko uska ecosystem chahiye ho. **Abhi wo over-engineering hoti.**

Aur ek practical faayda: koi nayi service nahi lagi. Redis pehle se lock, rate limit, idempotency, aur pub/sub ke liye chal raha hai — ab job queue bhi wahi.

---

## Step 2 — Worker same image, alag command

```yaml
worker:
  build: ./backend          # ⭐ wahi image jo backend ki hai
  command: arq worker.WorkerSettings
  volumes:
    - ./backend:/app
    - ticket_data:/app/tickets
```

Alag Dockerfile banate to models, config, database — sab duplicate ya shared-volume karna padta. Same image se worker ko **wahi code** milta hai jo API ke paas hai. Ek hi build, do commands.

### Shared volume kyu chahiye

Worker PDF **likhta** hai, API use **serve** karti hai. Do alag containers hain, to file share karne ke liye named volume chahiye — bina iske API ko file milti hi nahi.

---

## Step 3 — Enqueue kabhi fail nahi hona chahiye

```python
def enqueue_ticket(booking_id: int) -> None:
    try:
        asyncio.run(_enqueue("generate_ticket", booking_id))
    except Exception as exc:
        logger.warning("Ticket job queue nahi hua — booking %s: %s", booking_id, exc)
```

⚠️ Ye **kabhi raise nahi karta**, aur wo jaan-boojh ke hai.

Ye booking ho jaane ke **baad** call hota hai. Agar Redis down ho aur hum raise kar dein, to user ko 500 milega — jabki uska paisa kat chuka hai aur booking database me ban chuki hai. **Wo sabse bura outcome hai.**

Fail hone par booking `ticket_status = pending` me rehti hai, aur `retry_pending_tickets.py` use baad me utha leta hai.

> Yahi soch Phase 5 ke WebSocket broadcast me thi: notification "nice to have" hai, booking "must have".

---

## Step 4 — Job idempotent hai

```python
if booking.ticket_status == TICKET_READY and booking.qr_token:
    logger.info("Booking %s ka ticket pehle se ready hai — skip", booking_id)
    return booking.qr_token
```

Job do baar chal sakta hai — ARQ retry karta hai, aur retry script bhi re-queue kar sakta hai.

⚠️ Bina is check ke **QR token badal jata**, aur user ke paas jo ticket pehle se hai wo bekaar ho jata. Ye chhota check ek asli problem rokta hai.

> Yahi idempotency ka theme Phase 9 (keys), Phase 11 (webhook fulfilment) aur ab yahan — teeno jagah hai. Har wo cheez jo dobara chal sakti hai, dobara chalne layak honi chahiye.

---

## Step 5 — ⚠️ QR me booking id nahi

```python
def new_qr_token() -> str:
    return secrets.token_urlsafe(24)
```

Booking id **sequential** hai. QR me wo daalte to koi bhi 1, 2, 3... ka QR bana ke gate pe chala jata — poora ticketing system bekaar.

`token_urlsafe(24)` se 32 characters aate hain, guess karna practically namumkin. Column pe unique index bhi hai.

**Test bhi likha hai** iska — `test_qr_token_is_not_the_booking_id`.

---

## Step 6 — Thread me kyu chalta hai

```python
async def generate_ticket(ctx, booking_id):
    return await asyncio.to_thread(_generate, booking_id)
```

ARQ asyncio pe chalta hai, par hamara kaam **sync** hai (SQLAlchemy aur reportlab dono). Seedha call karte to PDF render karte waqt **poora event loop block** ho jata, aur worker koi dusra job nahi utha pata.

`to_thread` se wo kaam alag thread me jata hai aur loop khali rehta hai.

### Retry config

```python
max_tries = 3
retry_delays = [5, 30]     # badhta hua gap
job_timeout = 60
max_jobs = 5               # PDF render CPU-bound hai, isse zyada bekaar
```

Job me exception raise karna **theek hai** — ARQ retry karta hai. Par saari koshishein khatam hone par booking ko `failed` mark karte hain, warna user hamesha "generating..." dekhta rehta.

---

## Step 7 — Email: outbox pattern

```python
def send_ticket_email(*, to, subject, body, pdf, booking_id):
    eml = OUTBOX_DIR / f"booking-{booking_id}.eml"
    eml.write_text(...)
    logger.info("📧 Ticket email queued for %s", to)
```

⚠️ Asli SMTP nahi hai — koi credentials nahi hain, aur ek portfolio project se asli emails bhejna waise bhi galat hai.

Iski jagah email **disk par likh dete hain**. Django ka file/console email backend development me bilkul aisa hi karta hai.

**Asli SMTP lagana ho to sirf ye ek function badalna hai** — queue, retry, status, sab waisa ka waisa rahega. Yahi wajah hai ki ise alag function rakha.

> Wahi pattern jo mock payment provider aur "Google credentials na ho to button chhupa do" me hai: feature gracefully degrade hota hai, poora app nahi tootta.

---

## Step 8 — Frontend: download blob se

```js
const blob = await downloadTicket(booking.id)
const url = URL.createObjectURL(blob)
const a = document.createElement('a')
a.href = url
a.download = `SeatPulse-${bookingRef(booking.id)}.pdf`
a.click()
URL.revokeObjectURL(url)
```

⚠️ **`window.open` kaam nahi karega.** Endpoint ko `Authorization` header chahiye, par browser navigation me custom headers nahi jaate — user ko 401 milega.

Isliye fetch karke blob banate hain aur ek chhupa hua `<a>` click karte hain. `revokeObjectURL` zaroori hai, warna blob memory me pada rehta hai.

UI me teen states:

| `ticket_status` | Dikhta |
|---|---|
| `pending` | "Ticket…" (pulse karta hua) |
| `ready` | 🎫 Ticket — download button |
| `failed` | Retry button |

---

## ✅ Proof

### 1. Booking turant, ticket background me

```bash
curl -X POST -H "Authorization: Bearer $T" -d '{"seat_id":12}' .../api/bookings
# {"id":227, ...}     — turant, koi wait nahi
```

Worker logs:
```
INFO Ticket ban raha hai — booking 227 (attempt 1)
INFO 📧 Ticket email queued for demo@seatpulse.dev (outbox: booking-227.eml)
INFO ✅ Ticket ready: booking 227, seat B-2
04:48:01: 0.10s ← generate_ticket ● '6NTQE81XpTnebRSFPMFceDdV24hGAUC3'
```

### 2. PDF asli hai

```bash
curl -o t.pdf -w "HTTP %{http_code} type=%{content_type} size=%{size_download}\n" \
  -H "Authorization: Bearer $T" .../api/bookings/227/ticket
# HTTP 200  type=application/pdf  size=7383

head -c 8 t.pdf | xxd
# 00000000: 2550 4446 2d31 2e34    %PDF-1.4
```

### 3. ⚠️ Dusre ka ticket nahi milta

```bash
curl -o /dev/null -w "HTTP %{http_code}\n" -H "Authorization: Bearer $T2" \
  .../api/bookings/227/ticket
# HTTP 404
```

Ticket me QR hai — dusre ka download kar lena seedha **free entry** hai.

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
# INFO Sab tickets theek hain — kuch retry nahi karna
```

### 6. Test suite

```
42 passed in 35.02s
```

5 naye tests — pending state, end-to-end generation, **dusre ka ticket (404)**, auth required, aur **QR token sequential nahi hai**.

---

## Bugs / gotchas jo mile

### 1. Teesri baar wahi migration gotcha

```
[SQL: ALTER TABLE bookings ADD COLUMN ticket_status VARCHAR(16) NOT NULL]
```

`server_default` ke bina NOT NULL column existing rows par add nahi hota. Ye **teesri baar** hua (role, is_active, ab ticket_status).

> **Rule ban gaya:** NOT NULL column add kar rahe ho aur table me rows hain? `server_default` do, phir `alter_column(..., server_default=None)`.

### 2. Test se `import database` fail

Tests `tests/` me hain aur usme `__init__.py` nahi hai — us case me pytest sirf `tests/` ko `sys.path` me daalta hai, `/app` ko nahi.

Fix: backend root me `conftest.py` jo path insert karta hai. (`tests/__init__.py` dusra tareeka hai, par usse test files package ban jaati hain aur naam clash ho sakte hain.)

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Kaunsa kaam background me daalte ho?" | Do sawaal poochta hoon: user ko nateeja abhi chahiye? Aur ye fail ho to kya poori request fail honi chahiye? Ticket ke liye dono ka jawab "nahi" hai |
| "Celery kyu nahi?" | Redis pehle se tha, app ASGI hai, aur hume simple jobs chahiye the. ARQ dono se match karta hai. Celery tab chahiye jab priorities ya complex workflows hon |
| "Job do baar chal gaya to?" | Idempotent hai — ready hai to skip. Bina iske QR token badal jata aur user ka purana ticket bekaar ho jata |
| "Redis down ho jaye enqueue ke waqt?" | Enqueue exception nigal jata hai. Booking ho chuki hai, paisa kat chuka hai — usse 500 dena sabse bura hoga. Ticket `pending` rehta hai aur retry script uthata hai |
| "Worker crash ho jaye beech me?" | ARQ job ko complete mark nahi karta, wo dobara chalta hai. Aur job idempotent hai isliye safe |
| "QR me kya daala?" | Random 32-char token, booking id nahi. Sequential id daalte to koi bhi doosre ka QR bana leta |
| "Email kaise bhejte ho?" | Abhi outbox pattern — disk par likhta hoon. SMTP lagana ho to ek function badalna hai, baaki flow same |
| "Worker scale kaise karoge?" | Aur worker containers chala do — ARQ Redis se jobs uthata hai, koi coordination nahi chahiye. `max_jobs` se per-worker concurrency control hoti hai |

---

## Common Problems

| Problem | Fix |
|---|---|
| Ticket hamesha "pending" | Worker chal raha hai? `docker compose logs worker` |
| `ModuleNotFoundError: arq` | `docker compose up -d --build backend worker` |
| Download 409 "ready nahi hai" | Worker abhi bana raha hai — 1-2 second ruko |
| Download 409 "file nahi mili" | Volume saaf ho gaya. Endpoint khud re-queue kar deta hai, dobara try karo |
| Worker me `import` errors | Worker aur backend same image se bante hain — dono rebuild karo |
| Tests me `ModuleNotFoundError: database` | `backend/conftest.py` chahiye |

---

## Files

```
backend/
├── tickets.py                  ← naya ⭐ QR + PDF + email outbox
├── worker.py                   ← naya ⭐ ARQ worker + job
├── job_queue.py                ← naya (enqueue, kabhi raise nahi karta)
├── retry_pending_tickets.py    ← naya (safety net)
├── conftest.py                 ← naya (pytest sys.path fix)
├── models.py                   ← qr_token, ticket_status, ticket_generated_at
├── schemas.py                  ← BookingDetail me ticket_status
├── routers/bookings.py         ← ticket download + retry, enqueue
├── routers/payments.py         ← fulfilment ke baad enqueue
├── requirements.txt            ← arq, qrcode, reportlab
├── tests/test_concurrency.py   ← 5 naye tests (37 → 42)
└── alembic/versions/...        ← ticket columns

frontend/src/
├── api.js                      ← downloadTicket (blob), retryTicket
└── components/BookingsList.jsx ← TicketAction (3 states)

docker-compose.yml              ← worker service + ticket_data volume
.gitignore                      ← tickets/
```

---

## Related

- [11-payments.md](11-payments.md) — booking yahin se aati hai
- [05-websockets.md](05-websockets.md) — wahi "nice to have vs must have" soch
- [09-rate-limit-idempotency.md](09-rate-limit-idempotency.md) — idempotency ka pehla roop
- [../reference/testing.md](../reference/testing.md) — test commands
