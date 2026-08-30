# Phase 17 — Group Booking + Split Payment

> 4 dost saath baithna chahte hain. Har koi apna paisa khud dega.
> 3 ka paisa aa gaya, chauthe ka nahi, aur deadline aa gayi.
>
> **Ab kya?**

---

## Problem naya kyu hai

Ab tak project ka har correctness sawaal ek hi shakal ka tha: **ek seat,
ek booking**. Redis lock, `version` column, partial unique index — teeno
usi ek sawaal ke teen jawab the.

Group me sawaal badal jata hai:

```
Ek seat, ek booking          ->  ek row par exactly-once
Sab ya koi nahi              ->  N ALAG payments par atomicity
```

Aur ye asli distributed problem hai, kyunki har payment apne waqt par
aati hai, alag user se, alag browser se — aur beech me deadline nikal
sakti hai.

**Faisla: sab ya koi nahi.** Teen logon ko seat dena aur chauthe ko nahi,
poore group ka maqsad hi khatam kar deta hai (wo saath baithne aaye the).
To group tootta hai, seats chhootti hain, aur teeno ka paisa wapas jata hai.

---

## ⭐ Faisla 1 — seat ka naya status, `booking` nahi

Seedha rasta ye lagta hai ki N normal bookings bana do aur unhe ek
`group_id` se jod do.

Wo galat hai: **booking ka matlab hi hai "seat pakki ho gayi"**. Group me
seat kisi ki bhi pakki nahi hoti jab tak sabka paisa na aa jaye.

To beech ki ek haalat chahiye — seats roki hui hain, kuch paise aa chuke
hain, faisla abhi baaki hai:

```
seats.status = 'group_held'      <- naya status
group_bookings                    <- faisla yahan hota hai
group_shares                      <- har seat + uska hissa
```

Bookings **tabhi** banti hain jab group `confirmed` hota hai — aur tab ek
saath sabki.

### `locked` reuse kyu nahi kiya

Ye sabse zaroori design detail hai.

`locked` aur `payment_pending` seats ko lazy cleanup (`release_expired_locks`)
chupchaap `available` kar deta hai jab TTL nikal jati hai. Group seats ke
saath aisa karna **galat** hoga:

> Un seats me se kuch logon ka **paisa kat chuka** hota hai. Unhe chhodne
> ka matlab sirf "seat free karo" nahi — refund bhi hai.

Aur refund ek faisla hai, ek side-effect nahi. Isliye lazy cleanup
`group_held` ko chhoota hi nahi.

📁 [`backend/models.py`](../../backend/models.py) · [`backend/groups.py`](../../backend/groups.py)

---

## Faisla 2 — expiry cron se, lazy cleanup se nahi

Baaki poore project me hum expired holds ko **lazy** saaf karte hain: jab
koi seats padhta hai, tab purane locks release ho jaate hain. Wo sasta hai
aur kaafi hai, kyunki wahan kuch khoya nahi jata.

Group me nahi chalega:

> Agar koi is event ka page hi na khole, to lazy cleanup **kabhi chalta hi
> nahi** — aur log apne paise ka intezaar karte reh jaate hain.
>
> **Paisa wapas milna kisi ajnabi ke page kholne par nirbhar nahi ho sakta.**

Isliye ARQ me ek cron job hai, har 30 second:

```python
cron_jobs = [
    cron(expire_groups, second={0, 30}, run_at_startup=True),
]
```

`run_at_startup` isliye ki worker restart hone par jo groups us beech
expire ho gaye the wo turant nipat jaayein.

Job idempotent hai — `break_group` ek atomic conditional UPDATE se chalta
hai, to do worker ek saath chalein to bhi ek hi todega.

📁 [`backend/worker.py`](../../backend/worker.py)

---

## ⭐⭐ Faisla 3 — jahan optimistic locking KAAM NAHI AAYI

Ye phase ka sabse dilchasp hissa hai, aur [Phase 15](15-locking-benchmark.md)
se seedha juda hua hai.

Wahan maine benchmark karke dikhaya tha ki optimistic aur pessimistic
locking me farak measurable nahi hai, aur optimistic default rakha. Yahan
ek jagah aisi mili jahan **pessimistic ke bina correctness hi nahi bachti**.

### Race jo test me asal me tooti

```
payment thread                  expiry job
--------------                  ----------
group.status padha
  -> 'collecting'
                                group ko 'expired' kiya
                                shares padhe
                                  -> ye share abhi 'unpaid' hai
                                  -> refund nahi kiya
share ko 'paid' kiya

Nateeja: expired group me ek 'paid' share.
Us bande ka paisa kat gaya, seat mili nahi, refund bhi nahi hua.
```

Ye classic TOCTOU hai — `if group.status != COLLECTING` ek **padhai** hai,
atomic guard nahi.

### Optimistic pattern yahan kyu fail hota hai

Poore project me hamara pattern ye hai:

```sql
UPDATE x SET ... WHERE id = ? AND status = 'expected'
```

Wo tab kaam karta hai jab dono racers **ek hi row** par faisla kar rahe hon.
Yahan aisa nahi hai:

- payment thread `group_shares` ki row badalta hai
- expiry job `group_bookings` ki row badalta hai

**Ek row ka conditional UPDATE doosri row ki race nahi rok sakta.** Inhe
serialize karna hi padta hai:

```python
group = db.execute(
    select(GroupBooking).where(GroupBooking.id == share.group_id).with_for_update()
).scalar_one()

if group.status != GROUP_COLLECTING:
    _refund_share(db, share, payment)
    return
```

`FOR UPDATE` ke saath: agar expiry job pehle chal raha hai (group row par
lock hai), payment thread **rukta** hai jab tak wo commit na kare, phir
dobara padhta hai aur `expired` dekhta hai → refund. Aur ulta ho to expiry
job rukta hai aur baad me `paid` share dekh ke refund karta hai.

> Phase 15 ne kaha: "optimistic default, kyunki pessimistic ka kharcha
> lock hold time ke saath badhta hai."
> Phase 17 kehta hai: "aur jahan do ALAG rows serialize karni hon, wahan
> pessimistic ke alawa koi option hai hi nahi."
>
> Dono baatein saath chalti hain. Yahan lock ~1ms rehta hai, to Phase 15
> wala khatra lagta hi nahi.

---

## Flow

```
1. CREATE     N seats ek transaction me claim  ->  status 'group_held'
              share_token milta hai  ->  link
                    |
2. CLAIM      har banda ek khaali share leta hai
              UPDATE ... WHERE claimed_by IS NULL      <- atomic
                    |
3. PAY        har share ka apna checkout, apna Payment row
              Payment.group_share_id set hota hai
                    |
4. SETTLE     har payment 'paid' karti hai...
              ...aur SAB paid hone par:
              UPDATE group_bookings SET 'confirmed' WHERE status='collecting'
                    |                                     ^
                    |                          rowcount 1 = maine jeeta
              N bookings ek saath banti hain
```

Aur doosra raasta:

```
   DEADLINE   cron -> break_group()
              UPDATE ... SET 'expired' WHERE status='collecting'
              -> seats available
              -> paid shares refunded
              -> pending payments expired
```

### Group creation all-or-nothing hai

```python
for seat_id in seat_ids:
    result = db.execute(update(Seat).where(...).values(status=SEAT_GROUP_HELD))
    if result.rowcount == 0:
        db.rollback()          # jo seats mil chuki thi wo bhi chhoot jaati hain
        raise GroupError(...)
```

Ek bhi seat na mile to poora group reject. Warna user ko 3 seats mil
jaati aur wo 4th ka intezaar karta rehta — jo kabhi milegi hi nahi.
**Aadhi hold kisi ke kaam ki nahi.**

Test isi ko pakadta hai: fail hui creation ke baad baaki seats
`available` honi chahiye, `group_held` me atki nahi.

### Price group banate waqt freeze hota hai

Group me 30 minute lag sakte hain. Us beech surge kaafi badh sakta hai
([Phase 14](14-dynamic-pricing.md)). `GroupShare.amount` creation ke waqt
likh diya jata hai — wahi waada hai, aur wahi charge hota hai.

---

## Security

| Cheez | Kyu |
|---|---|
| `share_token` (`secrets.token_urlsafe`), id nahi | Sequential id hoti to koi bhi `/api/groups/1`, `/2` chala ke doosron ke groups dekh leta |
| API me `id` bheja hi nahi jata | Leak hone ki jagah hi na bache |
| Response me naam, email nahi | Link kisi ke paas bhi ja sakta hai; usme sab members ke email dikhana privacy leak hai |
| Ek user ek hi share | Warna ek banda poora group claim kar leta aur "split" ka matlab khatam |
| Cancel par 404 (403 nahi) | Wahi pattern jo baaki project me hai — existence bhi na pata chale |
| `pay` par claimer check | Doosre ka hissa nahi bhar sakte |

---

## Proof

### 1. Teen scenarios, asli HTTP se

```
A. Sabne pay kiya -> sab confirm
   Group bana: 3 shares, seats ['A-4', 'A-5', 'A-6']
   Seat statuses: ['group_held', 'group_held', 'group_held']
     paid 1/3 -> group 'collecting'  | seats ['group_held', 'group_held', 'group_held']
     paid 2/3 -> group 'collecting'  | seats ['group_held', 'group_held', 'group_held']
     aakhri banda pay karta hai...
     paid 3/3 -> group 'confirmed'
     seats ['booked', 'booked', 'booked']
     bookings bani: 3 (teeno alag users ki)

B. Deadline nikal gayi
   1 banda pay karta hai -> paid 1/3, group 'collecting'
     group -> 'expired'
     share statuses: ['refunded', 'unpaid', 'unpaid']
     seats -> ['available', 'available', 'available']

C. Ek share, do log ek saath
   do parallel claims -> [200, 409]
   200 mile: 1
```

Notice: 2/3 paid hone par bhi **koi seat booked nahi** — yahi "sab ya koi
nahi" ka asli proof hai.

### 2. ⭐ Confirm vs expiry race — 60 runs

Aakhri payment aur expiry job theek ek lamhe me (barrier se sync, aur
expiry par thoda jitter taki dono raaste chalein):

```
20 runs — confirmed: 15, expired: 5, atke: 0
Invariant todne wale: 0

20 runs — confirmed: 16, expired: 4, atke: 0
20 runs — confirmed: 17, expired: 3, atke: 0
```

Har run me check hota hai:

| Group | Invariant |
|---|---|
| `confirmed` | saari seats `booked`, har share ki booking bani |
| `expired` | saari seats `available`, koi booking nahi, paid share refunded |
| `collecting` | **kabhi nahi** — koi jeeta hi nahi, ye fail hai |

`FOR UPDATE` lagane se **pehle** ye 20 me se 1 baar tootta tha.

### 3. Tests

**79/79 pass** (69 pehle ke + 10 naye).

```bash
docker compose exec backend python -m pytest tests/ -q -k "group or share"
```

Naye tests:
- `test_group_holds_seats_without_booking_them`
- ⭐ `test_partial_payment_confirms_nobody` — 3 me se 2 pe kuch nahi hota
- `test_all_paid_confirms_everyone`
- `test_expired_group_releases_seats_and_refunds`
- `test_pending_payment_dies_with_the_group`
- ⭐⭐ `test_late_webhook_after_expiry_is_refunded_not_booked`
- ⭐⭐ `test_confirm_and_expiry_race_has_exactly_one_winner`
- `test_broken_group_does_not_leave_pending_payments`
- `test_only_one_person_can_claim_a_share`
- `test_cannot_pay_someone_elses_share`
- `test_group_creation_is_all_or_nothing`
- `test_unknown_share_token_is_404`
- `test_only_creator_can_cancel`

---

## Kya toota (aur kya seekha)

### 1. Migration me table order

```
psycopg2.errors.UndefinedTable: relation "group_bookings" does not exist
```

Autogenerate ne `group_shares` pehle rakha, jabki uska FK `group_bookings`
par hai. **Alembic dependency order khud nahi samajhta jab dono tables ek
hi revision me banti hon.** Hand se reorder karna pada.

### 2. `ck_seat_status` — teesri baar

Autogenerate ne `group_held` ko check constraint me nahi joda. Wahi
limitation jo [Phase 11](11-payments.md) me `payment_pending` ke saath thi:
**autogenerate mojooda check constraint ke ANDAR ka text compare nahi
karta.** Bina iske app chal jati par pehli group booking par
`CheckViolation` aata — runtime pe, migrate pe nahi.

### 3. ⭐ Dangling pending payments

Race test likhte waqt ye mila:

```
duplicate key value violates unique constraint "uq_one_pending_payment_per_seat"
```

Group tootne par uske shares ke **pending** payments latke reh jaate the.
Do nateeje:

1. Us seat par naya checkout ban hi nahi sakta ([Phase 11](11-payments.md)
   ka partial unique index rokta hai). **Seat `available` dikhti par
   khareedi nahi ja sakti** — sabse bura kism ka bug, kyunki UI me sab
   theek lagta hai.
2. User purana checkout page complete karke ek **mare hue group** ko paisa
   de deta.

Fix: `break_group` ab un payments ko `expired` kar deta hai.

### 4. ⭐⭐ TOCTOU jo race test ne pakda

Upar detail me likha hai. Sabak: **conditional UPDATE tabhi kaafi hai jab
dono racers ek hi row par faisla kar rahe hon.** Do alag rows ho to
`FOR UPDATE` chahiye.

Aur is bug ko koi normal test nahi pakadta — ye 20 concurrent runs me se
1 baar dikha tha.

### 5. Ek test fix ne behaviour improvement khoji

Bug 3 fix karne ke baad `test_payment_after_expiry_is_refunded_not_booked`
fail hone laga: share `unpaid` nikla, `refunded` nahi.

Test galat nahi tha — **behaviour behtar ho gaya tha**. Ab pending payment
expire ho jata hai, to `/simulate` usse chhoota hi nahi aur user se paisa
**katta hi nahi**. Refund se behtar hai charge hi na karna.

Par asli gateway hamare band karne se nahi rukta — webhook der se aa sakta
hai. Isliye test ko do me toda:

- `test_pending_payment_dies_with_the_group` — checkout band ho jata hai
- `test_late_webhook_after_expiry_is_refunded_not_booked` — der se aaya
  webhook refund me jata hai

Ek test ko "fix" karke aage badhne se behtar tha ye samajhna ki system ne
kya kiya.

### 6. Group tests suite me 429 khane lage

Alag se pass, suite me fail — sab `429`. BOOKING limit 5 burst/user hai,
aur group test ek hi user se kai calls karta hai (create + har share ka
checkout). Pehle chal chuke booking tests bucket khaali kar chuke hote the.

Fix: fixture sirf `rl:user:*` saaf karti hai. `rl:login:*` ko haath nahi
lagate — brute-force wali test usi par tiki hai.

---

## Jo jaan-boojh ke NAHI banaya

Ye likhna zaroori hai, warna doc jhooth bolti hai:

- **Asli refund API call nahi hai.** Mock provider me `_refund_share` sirf
  status likhta hai. Asli gateway me refund ki confirmation bhi **webhook
  se** aati — yaani `refund_pending` naam ka ek aur state chahiye hota,
  bilkul payment jaisa. Wo state machine nahi banayi.
- **Email notification nahi hai.** "Tumhare dost ne pay kar diya", "1 ghanta
  bacha hai" — inke bina asli product adhoora hai. Outbox pattern
  ([Phase 12](12-background-tickets.md)) already hai, to jodna aasan hoga.
- **Partial refund / grace period nahi.** Deadline sakht hai. Asli product
  me shayad "aakhri banda 5 min late hai" par thoda flex hota.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `backend/groups.py` | Poora core — create, claim, confirm, break, expire |
| `backend/routers/group_bookings.py` | HTTP layer |
| `frontend/src/pages/GroupBooking.jsx` | Share link page |

**Badle:**
| File | Kya |
|---|---|
| `backend/models.py` | `GroupBooking`, `GroupShare`, `SEAT_GROUP_HELD`, `Payment.group_share_id` |
| `backend/schemas.py` | `GroupCreate`, `GroupOut`, `GroupShareOut` |
| `backend/routers/payments.py` | Group share ka alag fulfilment raasta |
| `backend/worker.py` | `expire_groups` cron |
| `frontend/src/booking/BookingContext.jsx` | `startGroup()` |
| `frontend/src/components/{HoldCard,SeatGrid}.jsx` | Entry point + `group_held` rang |
| `frontend/src/App.jsx`, `api.js` | Route + API calls |

---

## Related

- [Phase 11 — Payments](11-payments.md) — webhook source of truth, jispe ye tika hai
- [Phase 14 — Dynamic Pricing](14-dynamic-pricing.md) — price freeze ka wahi asool
- [Phase 15 — Locking Benchmark](15-locking-benchmark.md) — optimistic vs pessimistic, aur yahan uska apwaad
- [Phase 12 — Background Tickets](12-background-tickets.md) — ARQ worker jisme cron juda
- [Interview Prep](../interview-prep.md) — "sab ya koi nahi" ke sawaal
