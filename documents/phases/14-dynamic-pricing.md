# Phase 14 — Dynamic Pricing

> Demand se price badhta hai. Par jo price user ko dikhaya, wahi charge hota hai.

---

## Problem kya thi

Ab tak har seat ka price fixed tha. Asli ticketing me aisa nahi hota —
airlines, Uber, BookMyShow sab demand ke saath price badhate hain.

Feature banate waqt asli sawaal ye **nahi** tha ki "price kaise badhayein"
(wo teen line ka formula hai). Asli sawaal do the:

1. **Price kahan rakhein?** Seat ka `price` column update karte rahein?
2. **Checkout ke beech price badal gaya to?** User ne ₹800 dekha, hold kiya,
   payment page pe pahuncha — aur tab tak 5 seats aur bik gayi. Ab ₹920?

Dusra sawaal hi is poore phase ka dil hai. Pehla galat kar do to code gandha
hota hai. Dusra galat kar do to **user se chup-chaap zyada paisa katta hai** —
aur wo bug hai jiske liye company pe case hota hai.

---

## Faisla 1 — Base price kabhi nahi badalta

Sabse seedha rasta ye lagta hai:

```python
# ❌ Jo pehli baar dimaag me aata hai
seat.price = seat.price * multiplier
db.commit()
```

Ye char wajahon se galat hai:

| Problem | Kya hota |
|---|---|
| **History mit jati** | Purani booking me `amount = ₹800` hai, par seat pe ab ₹1400 likha hai. "Original price kya tha?" ka jawab kahin nahi bachta. |
| **Write amplification** | Ek booking par 500 seats ka UPDATE. Flash sale me 500 bookings = 250,000 row updates. |
| **Naya race condition** | Do parallel bookings ab price update pe bhi ladenge — humne ek nayi contention point bana di. |
| **Rounding drift** | `price × 1.1` baar-baar karo to ₹800 → ₹880 → ₹968 → ₹1064… compounding ho jata hai, jo formula ka matlab hi nahi tha. |

Isliye:

```
seats.price       = BASE. Immutable. Organizer ne jo set kiya.
current_price     = base × multiplier, har baar CALCULATE hota hai
booking.amount    = jo actually charge hua (pehle se hi store ho raha tha)
```

Teen alag cheezein, teen alag jagah. Koi ek doosre ko corrupt nahi karti.

**Cost:** har seat list request pe do count queries (`total seats`,
`confirmed bookings`). 500 seats ke liye bhi ye 2 queries hain, 500 nahi —
kyunki multiplier poore event ka ek hi hota hai, per-seat nahi.

📁 [`backend/pricing.py`](../../backend/pricing.py) — pure functions, koi DB nahi
📁 [`backend/pricing_state.py`](../../backend/pricing_state.py) — DB se state nikalna

---

## Formula

```python
sold_ratio = confirmed_bookings / total_seats
multiplier = 1 + (sold_ratio × demand_factor)
multiplier = min(multiplier, max_surge)

current_price = round_to_10(base × multiplier)
```

`demand_factor = 0.5` → sold out par price 1.5×. Beech me linear.

**Ye jaan-boojh ke simple hai.** Asli surge pricing me time-to-event, booking
velocity, competitor pricing, aur historical demand curves hote hain. Wo sab
bina asli data ke sirf random constants hote — aur interview me "maine
demand forecasting kiya hai" bolke uska defend na kar paana bahut bura hai.

Ye formula transparent hai: user se exactly bata sakte hain ki price kyu badha.

### Rounding ka ek surprise

Python ka `round()` **banker's rounding** karta hai:

```python
round(100.5)  # 100  (101 nahi!)
round(101.5)  # 102
```

Iska seedha natija: multiplier thoda badhne par bhi final price wahi reh
sakta hai. Isliye `_seats_until_increase()` formula se andaza nahi lagata —
wo asli price ko aage badha kar dekhta hai ki kab badalta hai.

Ye test likhte waqt pakda gaya — expectation `1` thi, jawab `2` aaya. Code
sahi tha, test galat. (Neeche "Kya toota" me detail.)

---

## Faisla 2 — ⭐ Price hold ke saath lock ho jata hai

Ye phase ka sabse zaroori hissa hai.

```
User A: seat dekhi        -> ₹1000 dikha
User A: hold kiya          -> held_price = 1000  ← YAHAN lock hua
User B,C,D,E: 4 seats khareed li  -> market ab ₹1400
User A: pay kiya           -> ₹1000 charge hua ✅
```

`seats.held_price` ek nullable column hai. Hold par set hota hai, hold
chhutne/expire hone par `NULL`.

**Ye column kyu, calculation kyu nahi?** Kyunki "us waqt kya price tha" ko
baad me dobara compute kiya hi nahi ja sakta — demand tab tak badal chuki
hoti hai. Quote ek **waada** hai, aur waade store karne padte hain.

### Ek hi jagah jahan se price aata hai

```python
# backend/pricing_state.py
def price_now(db, seat) -> float:
    if seat.held_price is not None:
        return float(seat.held_price)          # waada nibhao
    event = db.get(Event, seat.event_id)
    return current_price(float(seat.price), pricing_state(db, event))
```

Bookings aur payments dono yahi call karte hain. Kahin bhi seedha
`seat.price` use karna ab bug hai — wo BASE hai, aur dynamic pricing on ho
to user ne wo number kabhi dekha hi nahi tha.

### Payments me ek chhupi hui galti

```python
# ❌ Do baar call — beech me hold expire ho sakta hai
payment = Payment(amount=price_now(db, seat), ...)
session = provider.create_checkout(amount=price_now(db, seat), ...)

# ✅ Ek baar nikalo, dono jagah wahi bhejo
quoted = price_now(db, seat)
payment = Payment(amount=quoted, ...)
session = provider.create_checkout(amount=quoted, ...)
```

Pehle wale me gateway ₹1400 charge karta jabki hamare DB me ₹1000 likha
hota. Wo mismatch reconciliation job me hi pakda jata — tab tak user ka
paisa kat chuka hota.

### Hold release par lock bhi jaata hai

```python
.values(status=SEAT_AVAILABLE, held_price=None, ...)
```

Bina iske user `hold → release → hold` karke hamesha ke liye sabse sasta
price pakad leta, aur surge ka koi matlab hi na bachta. Ye lazy-expiry
cleanup me bhi hai, sirf explicit unlock me nahi.

📁 [`backend/routers/seats.py`](../../backend/routers/seats.py)

---

## Faisla 3 — WebSocket pe kya bhejein

Ek booking hone par **saari** seats ka price badal jata hai. Seedha rasta:
har seat ka naya object broadcast karo.

500 seats × har booking = 500 messages per booking. Flash sale me ye khud
ek DoS hai.

Asli baat ye hai: multiplier **poore event ka ek hi** hai, aur base price
frontend ke paas pehle se hai. To sirf event-level message bhejo:

```json
{
  "type": "pricing_update",
  "pricing": {
    "enabled": true, "multiplier": 1.4, "surge_percent": 40,
    "sold": 4, "total": 10, "seats_until_increase": 1
  }
}
```

Ek chhota message vs 500 — result bilkul same.

### Bhoolna mumkin hi na ho

```python
# backend/events_broadcast.py
_SOLD_COUNT_CHANGED = ("booked", "cancelled")
...
if action in _SOLD_COUNT_CHANGED:
    _publish_pricing(seat.event_id, info)
```

Ye list `broadcast_seat_update` ke andar hai, call sites pe nahi. Kal koi
naya route booking banaye to pricing broadcast apne aap ho jayega. Call
site par chhoda hota to koi ek route bhoolta, aur us route se aayi booking
ke baad sab clients stale price dikhate rehte.

---

## Frontend — client-side me price calculate NAHI karte

Ye counter-intuitive hai. `base × multiplier` ek line ka kaam hai, aur
usse ek network round-trip bach jata. Phir bhi nahi kiya:

```js
Math.round(100.5)   // 101   ← JavaScript
round(100.5)        // 100   ← Python
```

Ties par dono alag jawab dete hain. Matlab UI ₹1010 dikhata aur server
₹1000 charge karta. ₹10 chhota lagta hai — par "jo dikha wahi kata" ka
bharosa toot jata hai, aur wahi is poore feature ki buniyaad hai.

To: `pricing_update` aane par **banner turant** update hota hai (wahi user
dekhta hai), aur exact seat prices 400ms debounce ke baad server se aate
hain. Debounce isliye ki flash sale me ek second me 20 bookings ho sakti
hain.

📁 [`frontend/src/booking/BookingContext.jsx`](../../frontend/src/booking/BookingContext.jsx)

### Ek hi jagah price ka faisla

```js
export function seatPrice(seat) {
  return seat.held_price ?? seat.current_price ?? seat.price
}
```

Har component apna hisaab lagata to kisi ek jagah `held_price` bhoolna
aasan hota — aur wahi ek jagah user ko galat price dikha deti.

### UI me kya NAHI dikhaya

Ticketing sites "Only 3 left! 🔥" chipka deti hain chahe 300 seats khaali
padi hon. Yahan har number server se aata hai aur sach hai:

- `seats_until_increase` **null** ho (price abhi nahi badhega, ya max surge
  aa chuka) to wo line **dikhati hi nahi**. Jhoothi urgency banane se
  behtar khaali jagah hai.
- HoldCard ka "🔒 Price locked" badge tabhi aata hai jab market price
  actually locked price se upar ho. Har haal me "you saved!" chipkana jhooth
  hota.

📁 [`frontend/src/components/PricingBanner.jsx`](../../frontend/src/components/PricingBanner.jsx)

---

## Organizer ke controls

| Field | Range | Default |
|---|---|---|
| `dynamic_pricing` | on/off | **off** |
| `demand_factor` | 0 – 2.0 | 0.5 |
| `max_surge` | 1.0 – 3.0 | 2.0 |

**Default off kyu:** surge har event ke liye theek nahi hai. Free community
meetup pe ye bhaddha lagta hai. Organizer jaan-boojh ke on kare.

**`max_surge` hard ceiling kyu:** chahe formula kuch bhi kahe, isse upar
nahi jayega. Bina cap ke pricing bekaboo lagti hai aur bharosa uth jata hai.
`demand_factor` ka upper bound 2.0 hai — galti se `50` type ho jaana bahut
mehnga padta (422 milta hai).

**PATCH me surge knobs badal sakte hain, base price nahi.** Faraq: base
price badalna purani bookings ko jhootha bana deta ("₹800 ka ticket kaha
tha, ab ₹1200 likha hai"). Surge band karna sirf AAGE ki bookings pe asar
daalta hai — jo har organizer ko karne ka haq hona chahiye agar sales slow
ho rahi hain. Change hone par turant `pricing_update` broadcast hota hai.

---

## Proof

10 seats @ base ₹1000, `demand_factor = 1.0`. User A ek seat hold karta hai,
phir User B chaar seats khareedta hai.

```
A ne A-1 hold ki
  quoted price = Rs.1000

B 4 seats khareedta hai:
  A-2 booked @ Rs.  1000   | baaki seats ab Rs.1100
  A-3 booked @ Rs.  1100   | baaki seats ab Rs.1200
  A-4 booked @ Rs.  1200   | baaki seats ab Rs.1300
  A-5 booked @ Rs.  1300   | baaki seats ab Rs.1400

WebSocket: 5 seat_update, 4 pricing_update
  +10%  sold 1/10  next increase in 1 seat(s)
  +20%  sold 2/10  next increase in 1 seat(s)
  +30%  sold 3/10  next increase in 1 seat(s)
  +40%  sold 4/10  next increase in 1 seat(s)

A ki held seat: held_price=Rs.1000   (market ab Rs.1400)
A ne book ki -> charged Rs.1000
MATCH — quote nibha
```

Do cheezein sabit ho gayi:

1. **Surge live chal raha hai** — har booking pe price +10%, aur WebSocket pe
   4 pricing_update messages gaye
2. **Quote nibha** — market 40% upar chala gaya, par A se exactly wahi ₹1000
   liya jo usse dikhaya tha

### Tests

**63/63 pass** (pehle 50 the, 13 naye).

Naye tests do hisso me:

*Formula (pure functions, DB nahi):*
- `test_multiplier_grows_with_demand`
- `test_max_surge_is_a_hard_ceiling`
- `test_empty_event_does_not_divide_by_zero` — naya event banate waqt total=0
- `test_price_rounds_to_a_clean_number`
- `test_disabled_pricing_never_surges`
- `test_seats_until_increase_counts_forward`
- `test_max_surge_reached_reports_no_further_increase`

*Waada (poora HTTP flow):*
- `test_new_event_starts_at_base_price`
- `test_price_rises_after_a_booking` — aur BASE price nahi badla
- ⭐ `test_held_price_survives_a_price_rise` — **ye sabse zaroori hai**
- `test_releasing_a_hold_drops_the_locked_price`
- `test_organizer_can_turn_surge_off`
- `test_base_price_cannot_be_edited`
- `test_absurd_surge_settings_are_rejected`

```bash
docker compose exec backend python -m pytest tests/test_concurrency.py -q
```

---

## Kya toota (aur kya seekha)

### 1. Migration me `NOT NULL` — chauthi baar

```
column "dynamic_pricing" of relation "events" contains null values
```

Wahi purana pattern, chauthi baar (`is_active`, `role`, `ticket_status` ke
baad). Ab ye reflex hona chahiye:

```python
op.add_column('events', sa.Column('dynamic_pricing', sa.Boolean(),
              nullable=False, server_default='false'))
op.alter_column('events', 'dynamic_pricing', server_default=None)
```

Default lagao → purani rows backfill ho jaayein → default hata do (taki
aage se application value decide kare, DB nahi).

### 2. Test ka arithmetic galat tha, code sahi

```
assert info.seats_until_increase == 1
E   assert 2 == 1
```

100 seats, factor 0.5, base ₹1000:
- 1 seat bika → 1.005× → ₹1005 → ₹10 pe round → **₹1000** (koi badlaav nahi)
- 2 seats bike → 1.010× → **₹1010** ← yahan badla

Jawab 2 hai. ₹5 ka farq ₹10 ke rounding me gayab ho jata hai.

Yahan galti maanna zaroori hai: **test galat thi, code nahi.** Aasan hota
`_seats_until_increase` ko "theek" karke test pass kara dena — aur tab UI
jhooth bolta ki "ek aur booking pe price badhega" jab wo actually nahi
badhta. Iska nateeja: `pricing.py` me rounding behaviour ab explicitly
documented hai.

### 3. Proof script me event loop block ho gaya

Pehla proof run me **0 pricing_update messages** dikhe, jabki prices sahi
badh rahe the. Lagta tha broadcast toota hai.

Asli wajah: proof script sync `httpx.Client` use kar raha tha `asyncio.run()`
ke andar. Sync calls poora event loop block kar dete hain, to WebSocket
listener coroutine ko chalne ka mauka hi nahi mila.

`httpx.AsyncClient` pe switch karte hi 4 messages aa gaye.

**Sabak:** proof script bhi code hai. Usme bug ho sakta hai, aur "feature
toota hai" wala conclusion pehle uske khilaf check karna chahiye. Yahan
production code bilkul theek tha.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `backend/pricing.py` | Formula. Pure functions, koi DB nahi — isliye test karna aasan |
| `backend/pricing_state.py` | DB se pricing state; `price_now()` ek hi source of truth |
| `frontend/src/components/PricingBanner.jsx` | Live surge indicator |

**Badle:**
| File | Kya |
|---|---|
| `backend/models.py` | `Event.dynamic_pricing/demand_factor/max_surge`, `Seat.held_price` |
| `backend/schemas.py` | `SeatOut.current_price/held_price`, `PricingOut`, `SeatLockOut.price` |
| `backend/routers/seats.py` | Lock par price freeze, release par clear |
| `backend/routers/bookings.py` | `price_now()` se amount |
| `backend/routers/payments.py` | Ek hi quote, payment + gateway dono me |
| `backend/routers/organizer.py` | Create/update me surge knobs |
| `backend/events_broadcast.py` | `pricing_update` message |
| `frontend/src/hooks/useWebSocket.js` | Naya message type handle |
| `frontend/src/booking/BookingContext.jsx` | `seatPrice()`, debounced refetch |
| `frontend/src/components/{SeatGrid,HoldCard}.jsx` | Displayed price |
| `frontend/src/pages/organizer/CreateEvent.jsx` | Surge toggle + slider |

---

## Related

- [Phase 11 — Payments](11-payments.md) — amount kahan se aata hai
- [Phase 05 — WebSockets](05-websockets.md) — pub/sub fan-out jo yahan reuse hua
- [Phase 04 — Redis Locking](04-redis-locking.md) — hold ka lifecycle
- [Interview Prep](../interview-prep.md) — pricing ke sawaal
- [Roadmap](../roadmap.md)
