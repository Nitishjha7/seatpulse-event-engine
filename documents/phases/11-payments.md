# Phase 11 — Payments

[10-rbac-organizer.md](10-rbac-organizer.md) ke baad ka kaam.

**Kya bana:** checkout flow, webhook-based confirmation, aur seat ka naya state `payment_pending`.

---

## The problem this solves

Booking payment ke bina aadhi thi. Par payments sirf ek missing **feature** nahi the — ek missing **problem** the.

Jaise hi paisa aata hai, ye sawaal khulta hai:

> **Paisa kat gaya, par booking fail ho gayi. Ab kya?**

Ye classic **dual-write problem** hai — do systems (payment gateway aur hamara database) ko consistent rakhna, jab dono me se koi bhi kabhi bhi fail ho sakta hai.

Aur ye ek naya sawaal nahi hai — ye Phase 9 wale idempotency ka bada bhai hai. Wahan double-click se do booking rokni thi; yahan double-charge rokna hai.

---

## Concept — webhook source of truth kyu hai

Payment ke baad gateway user ko hamare site pe **redirect** karta hai. Ek naya developer sochta hai: "redirect aa gaya matlab payment ho gaya, booking bana do."

**Ye galat hai. Do tarah se.**

```
GALAT — redirect par bharosa:

  User pay karta hai ─► Gateway ─► redirect ─► hum booking banate hain

  Problem 1: user pay karke tab BAND kar de
             → redirect kabhi aata hi nahi
             → par paisa kat chuka hai
             → booking nahi bani. User ka paisa gaya.

  Problem 2: koi seedha success URL kholde
             → bina paise ke booking ban gayi
```

```
SAHI — webhook par bharosa:

  User pay karta hai ─► Gateway ──┬─► redirect ─► "thank you" page (sirf UI)
                                  │
                                  └─► webhook ─► server-to-server
                                                 signature verified
                                                 → BOOKING YAHAN BANTI HAI

  User tab band kar de? Webhook phir bhi aata hai. Booking ban jaati hai.
  Koi nakli redirect khole? Kuch nahi hota — wo page sirf poochta hai.
```

**Redirect UI ke liye hai, faisla lene ke liye nahi.**

---

## Step 1 — Seat ka naya state

```
available → locked → payment_pending → booked
                           │
                  (fail/timeout) → available
```

`payment_pending` alag status kyu, `locked` hi kyu na rakhein:

| | |
|---|---|
| Dusre users ko | "hold me hai" aur "bik rahi hai" alag dikhta hai (UI me orange, pulse karta hua) |
| Cleanup ko | Expired hold aur abandoned checkout alag treat kar sakte hain |
| Debugging ko | Seat kis stage me atki hai, ek nazar me pata chalta hai |

Migration me **check constraint dobara banani padi** — `ALTER` se constraint badalti nahi, drop karke recreate karni padti hai.

> ⚠️ Autogenerate ne ye **miss kiya**. Alembic existing check constraints ka *content* compare nahi karta. Bina isko haath se add kiye, `payment_pending` insert karte hi `CheckViolation` aata.
>
> Yahi wajah hai ki har autogenerate migration khol ke padhni chahiye.

---

## Step 2 — Payment table alag kyu, Booking me merge kyu nahi

```python
class Payment(Base):
    booking_id: Mapped[int | None]   # succeed hone par hi bharta hai
    status: str                       # pending | succeeded | failed | expired | refunded
    provider: str                     # "stripe" | "mock"
    provider_ref: str | None          # gateway ka session id — UNIQUE
    expires_at: datetime
```

Merge kar dete to teen dikkatein hoti:

1. **"Failed booking"** jaisi ajeeb cheez banti — booking ya to hai ya nahi hai
2. User do baar try kare (pehli fail, dusri succeed) — do payments, ek booking. Merge me ye represent hi nahi hota
3. Refund history kahin nahi bachti

### ⭐ `provider_ref` UNIQUE hai — yahi webhook ko idempotent banata hai

Gateway same event **do baar** bhej sakta hai (webhooks at-least-once hote hain). Unique constraint ki wajah se dusri baar naya row insert nahi hota — lookup hota hai.

### Ek aur partial unique index

```python
Index("uq_one_pending_payment_per_seat", "seat_id", unique=True,
      postgresql_where=text("status = 'pending'"))
```

Ek seat ka ek hi pending payment. Do log ek saath checkout shuru nahi kar sakte, aur ek user do tab me do session nahi bana sakta.

**Wahi pattern** jo bookings pe hai (`uq_one_confirmed_booking_per_seat`) — sirf `pending` par lagta hai, isliye fail hone ke baad retry chalta hai.

---

## Step 3 — Provider abstraction

```python
class MockProvider:    name = "mock"
class StripeProvider:  name = "stripe"

def get_provider():
    return StripeProvider() if settings.payment_provider == "stripe" else MockProvider()
```

Provider `STRIPE_SECRET_KEY` se khud chunta hai — config me alag flag nahi rakha, kyunki phir do jagah sach hota aur wo galat ho sakta.

### ⭐ Mock kyu banaya

Interviewer mera repo clone karega — uske paas meri Stripe keys nahi hongi. Bina mock ke wo poora checkout flow chala hi nahi sakta, aur "payments hain" ka claim uske liye jhoot jaisa lagta.

Mock ka checkout page frontend pe khulta hai (`/pay/:id`), aur wo **wahi `_fulfil`/`_fail` call karta hai** jo asli webhook karta hai. Matlab mock ke liye alag code path test nahi ho raha — sirf trigger alag hai, logic bilkul same.

Yahi pattern Google OAuth me use kiya tha: credentials na ho to feature gracefully band, poora app nahi tootta.

### Stripe SDK kyu nahi use kiya

`httpx` pehle se dependency hai aur Stripe ka REST API seedha hai. SDK add karne se ek aur dependency aati — aur zyada important, **webhook signature verification ek black box ban jaata**. Wo khud likhne se pata chalta hai ki wo actually kaam kaise karta hai.

---

## Step 4 — ⭐ Webhook signature verification

Ye endpoint **authenticated nahi ho sakta** — Stripe ke paas hamara JWT nahi hai. To signature hi uska authentication hai. Bina verify kiye koi bhi POST maar ke free ticket le leta.

Stripe header aisa bhejta hai:
```
Stripe-Signature: t=1712345678,v1=abc123...
```

Verify karne ka tarika:
```python
signed = f"{timestamp}.".encode() + raw_body
expected = hmac.new(webhook_secret.encode(), signed, hashlib.sha256).hexdigest()
hmac.compare_digest(expected, provided)
```

Teen cheezein jo galat karna aasan hai:

| Cheez | Galat karo to |
|---|---|
| **Raw body chahiye**, parsed JSON nahi | Signature exact bytes par bani hai. JSON parse karke dobara serialize karoge to spacing badal jayegi aur signature kabhi match nahi karegi |
| **Timestamp check** | Bina iske koi ek valid webhook capture karke **baar-baar replay** kar sakta hai — signature to hamesha valid hi rahegi |
| **`compare_digest`**, `==` nahi | Normal `==` pehle mismatch pe return kar deta hai. Jawab ke **time** se attacker ek-ek character guess kar sakta hai (timing attack) |

---

## Step 5 — ⭐ Fulfilment idempotent hona chahiye

```python
def _fulfil(db, payment):
    # Pehle se ho chuka? Wahi booking lauta do.
    if payment.status == PAYMENT_SUCCEEDED and payment.booking_id:
        return db.get(Booking, payment.booking_id)
    ...
```

**Kyu zaroori hai:**
- Webhooks at-least-once hote hain — gateway retry karta hai agar hamara response miss ho jaye
- Reconciliation job bhi isi function ko call karta hai
- Mock ka simulate bhi

Teeno raaste ek hi function pe aate hain, aur wo dobara chale to naya kaam nahi karta.

### Aur teeno purani layers yahan bhi lagti hain

```python
result = db.execute(
    update(Seat)
    .where(Seat.id == payment.seat_id, Seat.status.in_((PAYMENT_PENDING, LOCKED, AVAILABLE)))
    .values(status=SEAT_BOOKED, version=Seat.version + 1)
)
if result.rowcount == 0:
    # Seat kisi aur ne le li — par paisa kat chuka hai!
    payment.status = PAYMENT_FAILED
    payment.failure_reason = "seat_taken_after_payment"
    logger.error("Payment %s succeeded par seat %s le li gayi — REFUND CHAHIYE", ...)
```

⚠️ Ye theoretically nahi hona chahiye (lock hamare paas tha). Par **"nahi hona chahiye" aur "nahi hoga" alag baatein hain.** Isliye ise chupchap ignore nahi kiya — payment ko failed mark karke reason likh dete hain, taki refund flow ise utha sake, aur log me ERROR jata hai.

---

## Step 6 — Reconciliation — sirf webhook kaafi nahi

Webhook miss ho sakta hai: hamara server neeche tha, network gira, ya gateway ne saare retries khatam kar diye. Us case me paisa kat chuka hoga par booking nahi bani hogi.

`reconcile_payments.py` un pending payments ko uthata hai jinka TTL nikal gaya:

```
Stripe se poochho → "paid" hai? → _fulfil karo (webhook miss hua tha)
                  → "unpaid"?  → _fail karo, seat wapas
                  → pata nahi? → chhod do, agli baar dekhenge
```

> ⚠️ **Stripe se poochhe bina expire karna galat hoga** — matlab user ka paisa le lena aur ticket na dena. Isliye status pata na chale to kuch nahi karte.

Webhook **fast path** hai, reconciliation **safety net**. Har paise wale system me dono hote hain.

---

## Step 7 — Frontend

| Page | Kya |
|---|---|
| `HoldCard` | "Confirm Booking" ab **"Pay ₹800"** hai |
| `/pay/:id` | Mock checkout — sirf jab Stripe keys na hon. Upar saaf banner: "🧪 Simulated Checkout, koi asli paisa nahi katega" |
| `/payment/return` | Gateway se wapas aane par. **Kuch decide nahi karta** — sirf backend se status poochta hai |

### Return page poll kyu karta hai

```js
if (p.status !== 'pending') return       // terminal — ruk jao
if (n < 20) setTimeout(() => poll(n + 1), 1500)
```

Redirect aksar webhook se **pehle** aa jata hai. Ek baar poochh ke "failed" bol dena galat hoga. ~30 second poll karte hain, phir user se kehte hain ki bookings me check kar le — kyunki webhook baad me bhi aayega aur booking khud ban jayegi.

---

## ✅ Proof

### 1. Checkout — seat payment_pending hoti hai, booked nahi

```bash
curl -X POST -H "Authorization: Bearer $T" localhost:8000/api/seats/3/lock
curl -X POST -H "Authorization: Bearer $T" -H "Content-Type: application/json" \
  -d '{"seat_id":3}' localhost:8000/api/payments/checkout
```
```json
{"payment_id":1,"checkout_url":"http://localhost:5173/pay/1","provider":"mock","amount":2500.0}
```
```bash
curl -s localhost:8000/api/seats/3 | grep -o '"status":"[^"]*"'
# "status":"payment_pending"
```

### 2. Dusra user block hota hai

```bash
curl -X POST -H "Authorization: Bearer $T2" -d '{"seat_id":3}' .../payments/checkout
# {"detail":"Ye seat kisi aur ke paas hold hai"}
```

### 3. ⭐ Fulfilment idempotent hai

```bash
# Pehli baar
curl -X POST ... /api/payments/1/simulate -d '{"outcome":"success"}'
# {"status":"succeeded","booking_id":196}

# DOBARA
curl -X POST ... /api/payments/1/simulate -d '{"outcome":"success"}'
# {"status":"succeeded","booking_id":196}     <- WAHI booking

psql -c "SELECT count(*) FROM bookings WHERE seat_id=3 AND status='confirmed';"
#  1
```

### 4. Fail path — seat wapas

```bash
curl -X POST ... /simulate -d '{"outcome":"fail"}'
# {"status":"failed","failure_reason":"declined_by_user"}

curl -s .../seats/4 | grep status     # "status":"available"
psql -c "SELECT count(*) FROM bookings WHERE seat_id=4;"     # 0
```

### 5. Reconciliation — abandoned checkout

```bash
psql -c "UPDATE payments SET expires_at = now() - interval '1 hour' WHERE id=9;"
docker compose exec backend python reconcile_payments.py
```
```
INFO 1 stale pending payments mile
INFO ✅ 0 fulfil kiye (missed webhooks), 1 expire kiye
```
```
seat 9 → "available"
payment 9 → failed / expired_unpaid
```

### 6. Test suite

```
37 passed in 28.93s
```

8 naye tests — checkout state, dusre user ka block, success path, **idempotency**, fail path, IDOR, aur **do webhook signature tests** (bad signature aur missing signature dono 400).

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Payment integrate kaise kiya?" | Gateway integrate karna asli kaam nahi tha. Asli problem **dual-write** thi — paisa kat gaya par booking fail. Isliye webhook source of truth hai, redirect nahi |
| "Redirect par bharosa kyu nahi?" | Do wajah: user tab band kar de to redirect aata hi nahi par paisa kat chuka hota hai; aur koi seedha success URL khol ke bina paise ke booking bana leta |
| "Webhook do baar aaya to?" | `provider_ref` unique hai aur `_fulfil` idempotent — dusri baar wahi booking wapas milti hai, nayi nahi |
| "Webhook aaya hi nahi to?" | Reconciliation job. TTL nikal jaane par gateway se poochte hain aur settle karte hain. Sirf webhook pe bharosa nahi |
| "Webhook authenticate kaise kiya?" | Signature — HMAC-SHA256 raw body par, timestamp tolerance ke saath (replay rokta hai) aur `compare_digest` se (timing attack rokta hai) |
| "Card details kahan store karte ho?" | Kahin nahi. Hosted checkout hai — card mere server ko chhuta hi nahi, isliye PCI scope me nahi aata |
| "Paisa kat gaya par seat kisi aur ne le li?" | Lock ki wajah se hona nahi chahiye, par handle kiya hai — payment failed mark hota hai `seat_taken_after_payment` reason ke saath aur ERROR log jata hai, taki refund flow uthaye |
| "Ek transaction me dono kyu nahi?" | Gateway meri DB transaction me hai hi nahi. External call ko transaction ke andar rakhna sabse aam galti hai — transaction network call jitni der khuli rehti hai |

---

## Common Problems

| Problem | Fix |
|---|---|
| `CheckViolation` on payment_pending | Migration me check constraint drop+recreate karna bhool gaye |
| Checkout 409 de raha | Seat ka pending payment already hai — `reset_state.py` ya TTL ka wait |
| Webhook 400 | Signature galat, ya raw body ki jagah parsed JSON verify kar rahe ho |
| Stripe se `amount` galat kata | Stripe **paise** me leta hai — ₹800 = `80000`, `800` nahi |
| Seat payment_pending me atki | `reconcile_payments.py` chalao, ya grid refresh karo (lazy cleanup) |
| Mock page pe "already settled" | Payment pehle hi succeed/fail ho chuka — return page pe jao |

---

## Files

```
backend/
├── payments.py                 ← naya ⭐ providers + signature verification
├── routers/payments.py         ← naya ⭐ checkout, webhook, fulfilment
├── reconcile_payments.py       ← naya (missed webhooks ka safety net)
├── models.py                   ← Payment model, SEAT_PAYMENT_PENDING
├── schemas.py                  ← checkout/payment schemas
├── config.py                   ← Stripe keys, PAYMENT_TTL, provider picker
├── reset_state.py              ← payments bhi saaf karta hai
├── routers/seats.py            ← payment_pending ka expiry cleanup
├── tests/test_concurrency.py   ← 8 naye tests (29 → 37)
└── alembic/versions/...        ← payments table + seat constraint

frontend/src/
├── pages/MockCheckout.jsx      ← naya (simulated gateway)
├── pages/PaymentReturn.jsx     ← naya (polls, decide nahi karta)
├── booking/BookingContext.jsx  ← payForSeat()
├── components/HoldCard.jsx     ← "Pay ₹X" button
├── components/SeatGrid.jsx     ← payment_pending color + legend
├── api.js                      ← checkout/payment calls
└── App.jsx                     ← /pay/:id aur /payment/return routes
```

---

## Note: `POST /api/bookings` abhi bhi hai

Direct booking endpoint hataya nahi hai — load tests aur concurrency tests usse use karte hain, aur wo teeno defence layers ka sabse saaf demo hai.

Production me ye **internal** ho jata (sirf fulfilment se call hota) ya hata diya jata. UI ab payment flow se hi jaati hai.

---

## Related

- [09-rate-limit-idempotency.md](09-rate-limit-idempotency.md) — idempotency ka pehla roop
- [04-redis-locking.md](04-redis-locking.md) — seat lock, jo checkout ke dauraan extend hota hai
- [../reference/testing.md](../reference/testing.md) — test commands
- [../roadmap.md](../roadmap.md) — aage kya
