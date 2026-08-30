# Phase 13 — Gate Check-in

[12-background-tickets.md](12-background-tickets.md) ke baad ka kaam.

**Kya bana:** QR scan karke entry validate karna — camera se, aur manual fallback ke saath.

---

## The problem this solves

Phase 12 me QR wala ticket ban gaya. Par event ke din gate pe do sawaal hote hain:

1. Ye ticket asli hai?
2. **Ye pehle use to nahi ho chuka?**

Dusra sawaal hi asli hai. Ek QR ka screenshot leke 5 doston ko bhej dena sabse aam ticketing fraud hai.

---

## Concept — ye wahi problem hai, alag kapdon me

| | Seat booking (Phase 4) | Gate check-in (ye phase) |
|---|---|---|
| Invariant | Ek seat, ek confirmed booking | Ek ticket, **ek entry** |
| Race | 5000 log ek seat pe | Do gates, wahi QR |
| Hal | Atomic conditional UPDATE | **Bilkul wahi** |

```sql
-- Seat booking
UPDATE seats SET status='booked' WHERE id=? AND version=?

-- Check-in
UPDATE bookings SET checked_in_at=now() WHERE id=? AND checked_in_at IS NULL
```

Dono me **read aur write alag steps nahi hain**. Isliye beech me kuch ghus nahi sakta.

> ⭐ Interview me ye connection banana: *"Check-in ki problem seat booking jaisi hi thi — exactly-once. Isliye hal bhi wahi pattern hai, naya kuch nahi sochna pada."*

---

## Step 1 — `checked_in_at` NULL hi guard hai

```python
checked_in_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
checked_in_by: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
```

**Boolean `is_checked_in` kyu nahi:**

| | Boolean | Timestamp ✅ |
|---|---|---|
| Guard | `WHERE is_checked_in = false` — kaam karta hai | `WHERE checked_in_at IS NULL` — kaam karta hai |
| "Kab aaya?" | ❌ pata nahi | ✅ pata hai |
| Dispute me | Bekaar | Yahi sabse zaroori jaankari hai |

Aur `checked_in_by` — kis staff ne scan kiya. Audit ke liye, aur duplicate ke case me "pehle kisne kiya tha" batane ke liye.

---

## Step 2 — Atomic check-in

```python
result = db.execute(
    update(Booking)
    .where(Booking.id == booking.id, Booking.checked_in_at.is_(None))
    .values(checked_in_at=utcnow(), checked_in_by=staff.id)
)

if result.rowcount == 0:
    # Pehle se andar aa chuka hai
    return _result(ok=False, reason="already_checked_in", ...)
```

Do gates ek saath:
```
Gate A                          Gate B
────────────────────────────────────────────
booking mili, checked_in NULL   booking mili, checked_in NULL
UPDATE ... WHERE IS NULL        UPDATE ... WHERE IS NULL
✓ rowcount 1 — andar jao        ✗ rowcount 0 — already used
```

---

## Step 3 — ⚠️ Fail hone par bhi 200 lautate hain

```python
return CheckInResult(ok=False, reason="already_checked_in", ...)
```

Ye counter-intuitive lagta hai. Wajah:

**Gate pe khada banda HTTP status nahi dekhta.** Use ek saaf jawab chahiye — "andar jao" ya "ye ticket 7:42 pm par use ho chuka tha".

409 dete to frontend ko error path me jaake wahi jaankari dobara nikalni padti. `ok: false` body me hone se dono cases ek hi code path me handle hote hain.

**Sirf asli errors non-200 hain** — permission (403), malformed request (422).

> Ye "business outcome vs technical error" ka farak hai. `already_checked_in` ek **valid business jawab** hai, error nahi.

---

## Step 4 — Role check aur ownership, dono

```python
staff: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))
...
if staff.role != ROLE_ADMIN and event.organizer_id != staff.id:
    raise HTTPException(403, "Ye ticket tumhare event ka nahi hai")
```

Bina ownership check ke ek organizer **kisi bhi** event ke tickets check-in kar deta.

> Wahi role-vs-ownership farak jo Phase 10 me tha. Role batata hai *kya kar sakte ho*, ownership batati hai *kis cheez par*.

**Note:** yahan `organizer`/`admin` ko gate staff maan liya hai. Asli deployment me ek alag `gate_staff` role hota jise organizer assign karta. Wo ek aur migration aur role management UI maangta — abhi scope me nahi, par [roadmap](../roadmap.md) me likha hai.

---

## Step 5 — Invalid token pe kuch mat batao

```python
if booking is None:
    logger.warning("Check-in: unknown token scanned by user %s", staff.id)
    return _result(ok=False, reason="invalid_ticket")     # aur kuch nahi
```

Response me `booking_id`, `seat_label`, sab `None` rehte hain.

⚠️ Agar "token exist karta hai par galat event ka hai" jaisa specific message dete, to koi tokens **brute-force** karke valid ones dhoondh leta. Test bhi likha hai iska.

---

## Step 6 — Camera scanning: library kyu nahi lagayi

```js
const supported = 'BarcodeDetector' in window
const detector = new window.BarcodeDetector({ formats: ['qr_code'] })
const codes = await detector.detect(videoRef.current)
```

**`BarcodeDetector` browser me built-in hai** — Chrome, Edge, Android. Firefox aur Safari me nahi.

| Option | Size | Support |
|---|---|---|
| `html5-qrcode` / `jsQR` | ~200KB | Sab browsers |
| `BarcodeDetector` ✅ | 0 KB | Chrome/Edge/Android |

Gate portal aksar **ek hi tarah ke device** pe chalta hai (staff ka Android phone ya tablet). Uske liye 200KB add karna bhaari sauda hai.

Aur **manual entry waise bhi chahiye** — phata hua QR, phone ki dead battery, camera permission block. To fallback pehle se maujood hai.

### Do chhoti cheezein jo zaroori hain

```js
// 1. Peeche wala camera — gate pe wahi use hota hai
video: { facingMode: 'environment' }

// 2. Ek QR kai frames tak dikhta hai — bina guard ke ek scan pe 20 requests
if (token !== lastScanned.current.token || now - lastScanned.current.at > 3000) {
    submit(token)
}
```

Aur cleanup:
```js
useEffect(() => () => stopCamera(), [])
```
Bina iske page chhodne ke baad bhi camera chalta rehta hai (phone ki light jalti rehti hai) — user ko lagta app spy kar raha hai.

---

## Step 7 — Result card gate ke liye banaya hai

Gate pe line lagi hoti hai. Staff ko **2 second** me faisla chahiye:

| Result | Rang | Headline |
|---|---|---|
| `checked_in` | 🟢 hara | "Andar jao" |
| `already_checked_in` | 🟡 peela | "Pehle se use ho chuka" + **kab aur kisne** |
| `invalid_ticket` | 🔴 laal | "Ticket valid nahi hai" |

Seat number sabse bada text hai — kyunki agla sawaal wahi hota hai.

Duplicate wale case me time aur scanner ka naam dikhta hai, kyunki gate pe bahas hoti hai: *"maine to abhi scan nahi karaya!"* — *"7:42 pm par ho chuka tha."*

---

## ✅ Proof

### 1. Valid ticket

```bash
curl -X POST -H "Authorization: Bearer $ORG" -d "{\"token\":\"$TOKEN\"}" \
  localhost:8000/api/checkin
```
```json
{"ok":true,"reason":"checked_in","booking_ref":"SP00273","seat_label":"B-5",
 "attendee_name":"Demo User","checked_in_at":"2026-08-30T05:00:25Z",
 "scanned_by":"Demo Organizer"}
```

### 2. ⭐ Wahi QR dobara

```json
{"ok":false,"reason":"already_checked_in","seat_label":"B-5",
 "checked_in_at":"2026-08-30T05:00:25Z","already_checked_in":true,
 "scanned_by":"Demo Organizer"}
```

Wahi timestamp — matlab dusri entry nahi hui.

### 3. Nakli token

```json
{"ok":false,"reason":"invalid_ticket","booking_id":null,"seat_label":null}
```

Sab null — koi detail leak nahi.

### 4. ⭐ 10 gates ek saath

```bash
for i in $(seq 1 10); do
  curl -s -X POST -H "Authorization: Bearer $ORG" -d "{\"token\":\"$TOKEN\"}" \
    localhost:8000/api/checkin | grep -o '"reason":"[^"]*"' &
done; wait
```

**Actual output:**
```
"reason":"checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
"reason":"already_checked_in"
```

**Exactly ek entry.** Wahi guarantee jo seat booking me hai.

### 5. Test suite

```
49 passed in 42.39s
```

7 naye tests — valid check-in, duplicate, **10 concurrent scans**, invalid token (aur uska info leak), attendee blocked, cancelled booking, stats.

---

## ⭐ Bug: do foreign keys, ek confusion

Migration ke baad app start hi nahi hua:

```
sqlalchemy.exc.InvalidRequestError: Could not determine join condition between
parent/child tables on relationship User.bookings — there are multiple foreign
key paths linking the tables.
```

**Wajah:** `Booking` me ab **do** FK `users` ko point karte hain:

```python
user_id       -> kisne book kiya
checked_in_by -> kis staff ne scan kiya
```

SQLAlchemy khud tay nahi kar sakta ki "user ki bookings" kaunse column se joduon.

**Fix:**
```python
# User side
bookings = relationship(..., foreign_keys="Booking.user_id")

# Booking side
user = relationship(..., foreign_keys=[user_id])
```

> **Rule:** jab ek table dusri table ko **do se zyada** FK se point kare, har relationship pe `foreign_keys` explicitly dena padta hai. Ye error tabhi aata hai jab app start hoti hai — to migration ke baad hamesha health check karo.

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "Ek QR do baar use na ho, kaise pakka kiya?" | `UPDATE ... WHERE checked_in_at IS NULL` — ek atomic statement. Do gates ek saath scan karein to sirf ek ko rowcount 1 milta hai. 10 concurrent scans pe test kiya hai: exactly 1 entry |
| "Ye kuch jaana pehchana lagta hai?" | Haan — bilkul wahi exactly-once problem hai jo seat booking me thi. Isliye hal bhi wahi pattern hai, naya kuch nahi sochna pada |
| "Boolean flag kyu nahi?" | Timestamp guard ka kaam bhi karta hai aur "kab aaya" bhi batata hai. Gate pe bahas hone par wahi sabse kaam ki jaankari hai |
| "Fail hone pe 409 kyu nahi?" | Gate pe khada banda status code nahi dekhta. `already_checked_in` ek valid business jawab hai, technical error nahi. Dono cases ek hi code path me handle hote hain |
| "Invalid token pe kya bhejte ho?" | Sirf `invalid_ticket` — koi detail nahi. Warna koi tokens brute-force karke valid ones dhoondh leta |
| "QR library kaunsi use ki?" | Koi nahi — browser ka native `BarcodeDetector`. 200KB bachaye. Support na ho to manual entry, jo waise bhi chahiye (phata QR, dead battery) |
| "Koi bhi organizer koi bhi ticket scan kar sakta hai?" | Nahi — ownership check hai, sirf apne event ke. Admin sabke |
| "Offline gate ka kya?" | Abhi network chahiye. Offline ke liye tickets pre-download karke locally verify karna padta, aur baad me sync — par tab duplicate detection kamzor ho jaati. Wo ek alag design problem hai |

---

## Common Problems

| Problem | Fix |
|---|---|
| "Is browser me QR scanning nahi hai" | Chrome/Edge me kholo, ya manual entry use karo |
| Camera nahi khul raha | HTTPS ya localhost chahiye — camera insecure origin pe block hai |
| Har frame pe request ja rahi | 3-second dedupe guard check karo |
| `invalid_ticket` valid QR pe | Ticket `ready` hai? `ticket_status` dekho |
| 403 scan karte waqt | Event tumhara nahi hai — organizer sirf apne event ke tickets scan karta hai |
| `multiple foreign key paths` error | Relationship pe `foreign_keys` chahiye |

---

## Files

```
backend/
├── routers/checkin.py          ← naya ⭐ atomic check-in + stats
├── models.py                   ← checked_in_at, checked_in_by, foreign_keys fix
├── schemas.py                  ← CheckInRequest, CheckInResult
├── main.py                     ← checkin router
├── tests/test_concurrency.py   ← 7 naye tests (42 → 49)
└── alembic/versions/...        ← checkin columns

frontend/src/
├── pages/gate/GatePortal.jsx   ← naya ⭐ camera scan + manual + result card
├── api.js                      ← checkIn, getCheckinStats
├── App.jsx                     ← /gate route (role-gated)
└── layout/Sidebar.jsx          ← Gate Check-in link
```

---

## Ab poora journey band ho gaya

```
Organizer event banata hai      (Phase 10)
        ↓
User seat hold karta hai        (Phase 4)
        ↓
Payment karta hai               (Phase 11)
        ↓
Worker ticket bhejta hai        (Phase 12)
        ↓
Gate pe QR scan hota hai        (ye phase)
```

Har step pe **exactly-once** guarantee hai, aur teeno jagah wahi pattern —
atomic conditional UPDATE.

---

## Related

- [04-redis-locking.md](04-redis-locking.md) — wahi exactly-once, seat pe
- [12-background-tickets.md](12-background-tickets.md) — QR yahin banta hai
- [10-rbac-organizer.md](10-rbac-organizer.md) — role vs ownership
- [../reference/testing.md](../reference/testing.md) — test commands
