# Phase 18 — Visual Seat Layout Builder

> Ab tak har event ek grid tha: N rows × M seats, sab barabar.
> Asli venue aisa nahi hota — usme aisles hoti hain, sections hote hain,
> aur har row me barabar seats nahi hoti.

---

## Problem

Phase 10 se organizer `price_tiers` se event banata hai:

```json
{ "seats_per_row": 10, "price_tiers": [{"rows": 2, "price": 2500}, ...] }
```

Simple hai aur zyadatar events ke liye kaafi bhi. Par ye teen cheezein
nahi kar sakta:

| Chahiye | Kyu nahi hota |
|---|---|
| Beech me **aisle** (chalne ka raasta) | Har row me seats lagatar hain |
| Alag **sections** (Ground, Balcony) | Sirf "tier 1, tier 2" hai, naam nahi |
| Har row me **alag seats** | `seats_per_row` poore event ke liye ek hai |

---

## ⭐ Faisla 1 — purana raasta HATAYA nahi

Sabse aasan rasta hota `price_tiers` ko nikaal ke sirf layout rakhna. Wo
galat hota:

1. **17 phases ka data usi se bana hai.** Seed, tests, demo — sab toot
   jaate.
2. **Zyadatar events ko naksha chahiye hi nahi.** "5 rows, 10 seats, ek
   price" ke liye layout banwana user ko sataana hai.

To dono raaste hain, **par ek hi generator**:

```
layout diya      ->  naksha waise ka waisa
price_tiers diya ->  usse layout BANAYA jata hai
                          |
                          v
                  seat_layout.expand()      <- ek hi jagah
                          |
                          v
                     bulk insert
```

`from_price_tiers()` purane input ko layout ke shape me badal deta hai.
Do alag generators rakhne ka matlab hota **do jagah bugs** — aur wo
dheere-dheere alag behave karne lagte.

Faayda: `price_tiers` se bana event bhi ab `layout` store karta hai, to
grid har event ko ek hi tarah render kar sakta hai.

📁 [`backend/layout.py`](../../backend/layout.py)

---

## ⭐⭐ Faisla 2 — purane events ka kya

Ye is phase ka sabse zaroori hissa hai.

`Event.layout` aur `Seat.section` dono **nullable** hain:

```python
layout: Mapped[dict | None] = mapped_column(JSON, nullable=True)
section: Mapped[str | None] = mapped_column(String(40), nullable=True)
```

`NULL` ka matlab hai "purana uniform event" — aur frontend usse bilkul
waise render karta hai jaise pehle karta tha:

```js
function aisleMap(layout) {
  const map = new Map()
  if (!layout?.sections) return map     // <- purana event, khali Map
  ...
}
```

Section headings bhi tabhi dikhte hain jab **ek se zyada** section ho.
Ek hi section wale event me "Ground" likhna sirf shor hai.

> Test `test_old_events_without_a_layout_still_work` seedha isi ko pakadta
> hai: seed wala event 1, `layout: null`, 100 seats, har seat ka
> `section: null` — aur baaki sab fields waise ke waise.

Ye woh cheez hai jo naya column add karte waqt sabse aasani se tootti hai,
aur us tootne ka pata bahut baad me chalta hai.

---

## Layout ka shape

```json
{
  "sections": [
    {
      "name": "Ground",
      "price": 2500,
      "rows": [
        {"label": "A", "seats": 8,  "aisles_after": [4]},
        {"label": "B", "seats": 10, "aisles_after": [3, 7]}
      ]
    },
    { "name": "Balcony", "price": 900, "rows": [{"label": "C", "seats": 12}] }
  ]
}
```

### Aisle sirf DIKHNE ki cheez hai

`aisles_after: [4]` matlab seat 4 ke **baad** ek gap.

**Koi seat nahi banti. Numbering nahi rukti.** Ye `seats` table me hai hi
nahi — sirf layout JSON me, kyunki wo purely presentation hai.

Ye aasan galti hai: aisle ko ek "khali seat" bana dena, ya uske baad
numbering skip kar dena. Dono galat hain — attendee "seat 5" maangta hai
aur usse seat 6 mil jati.

Test isi ko lock karta hai:

```python
with_aisle = expand(... _row("A", 6, [3]))
without    = expand(... _row("A", 6))
assert len(with_aisle) == len(without) == 6
assert [p.seat_number for p in with_aisle] == [1, 2, 3, 4, 5, 6]
```

---

## Validation — seats banane se PEHLE

```
Pydantic (schemas.py)  ->  shape: types, lengths, ranges
layout.py              ->  business rules: duplicate labels, seat cap, aisle position
```

Ye bantwara jaan-boojh ke hai. Shape rules schema me likhna aasan hai, par
"do sections me same row label nahi ho sakta" jaise rules ko **poore layout
ka context** chahiye — aur unhe DB ke bina test karna aasan hona chahiye.

### ⭐ Sabse zaroori rule: row label poore event me unique

```python
if label in seen_labels:
    raise LayoutError(f"Row '{label}' do jagah hai — ...")
```

`seats` par `UNIQUE(event_id, row_label, seat_number)` hai (Phase 2 se).
Ye check na hota to expansion **500 seats insert karne ke baad**
`IntegrityError` se marta.

Aur wo constraint section ko jaanta hi nahi — isliye label poore event me
unique hona chahiye, sirf section me nahi.

### expand() DB ko haath nahi lagata

```python
def expand(layout: dict) -> list[PlannedSeat]:
    validate(layout)
    ...
    return seats        # sirf list, koi insert nahi
```

Caller ise ek transaction me bulk insert karta hai. Agar `expand` khud
likhta, to "aadhi seats ban gayi phir error" wali haalat mumkin ho jati.

Test `test_bad_layout_creates_no_event` isi ko check karta hai: galat
layout ke baad organizer ke events ki ginti wahi rehni chahiye.

---

## Frontend

### Builder ek FORM hai, drag-and-drop canvas nahi

Ye ulta lag sakta hai, par soch ke liya gaya faisla hai:

> Asli venue rows aur sections me hi bana hota hai. "Row C me 12 seats,
> seat 4 ke baad aisle" **type** karna maus se 12 boxes ghaseetne se tez
> bhi hai aur galti-proof bhi.

Aur drag-and-drop apne saath pointer-events, undo/redo, snapping, aur
touch handling ka poora pahaad laata hai — us feature ke liye jo saal me
kuch baar use hota hai.

Iske badle **live preview** hai: bilkul wahi shape jo attendee ko dikhega.
Yahi builder ka asli point hai — 40 seats aur 4 aisles ko numbers me
sochna mushkil hai, dekh ke turant samajh aata hai.

📁 [`frontend/src/components/LayoutBuilder.jsx`](../../frontend/src/components/LayoutBuilder.jsx)

### Client-side validation duplicate hai, aur wo theek hai

`validateLayout()` server ke rules ki copy hai. Duplication jaan-boojh ke:

- Server par ye rules **hone hi chahiye** — koi bhi API seedha hit kar
  sakta hai, aur builder ek UI convenience hai
- Par user ko submit dabane se **pehle** pata chalna chahiye ki do rows ka
  label same hai. Wo round-trip bekaar hai.

Server hi asli faisla karta hai; client sirf jaldi feedback deta hai.

### Ek chhoti si detail jo bug banti

```js
r.aisles_after = e.target.value
  .split(',')
  .map((x) => parseInt(x.trim(), 10))
  .filter((x) => Number.isInteger(x) && x > 0)
```

`filter` ke bina: user "4," type kar raha hota hai (abhi 8 likhna baaki
hai) aur `parseInt("")` → `NaN` aa jata hai, jisse har keystroke pe
validation error flash karta hai.

### Grid me sections aur aisles

Aisle ek khali `<span>` hai — koi seat nahi, koi interaction nahi:

```jsx
{gaps?.has(seat.seat_number) && (
  <span className="w-4 shrink-0" aria-hidden="true" />
)}
```

`aria-hidden` isliye ki screen reader ke liye ye cheez hai hi nahi.

---

## Proof

```
=== A. LAYOUT se ===
201 30 seats
 sections: Counter({'Ground': 18, 'Balcony': 12})
 A row prices: {2500.0}
 C row prices: {900.0}
 layout stored: True | aisles A: [4]

=== B. Purana price_tiers raasta (backwards compat) ===
201 12 seats
 sections: Counter({'Tier 2': 8, 'Tier 1': 4})
 layout auto-generate hua: {"sections": [{"name": "Tier 1", "price": 1500.0, ...

=== C. Galat layouts reject hone chahiye ===
  duplicate row label      -> 422  Row 'A' do jagah hai — har row label poore event me alag hona chahiye
  aisle row ke bahar       -> 422  Row 'A': aisle position 9 row ke andar honi chahiye (1-4)
  duplicate section name   -> 422  Do sections ka naam ek hi hai: X

=== D. Purane events (layout NULL) abhi bhi chalte hain ===
 event 1: layout=None, 100 seats, section=None
```

### Tests

**90/90 pass** (79 pehle ke + 11 naye).

*Pure functions (koi DB nahi):*
- `test_expand_produces_every_seat`
- ⭐ `test_aisles_do_not_create_or_skip_seats`
- ⭐ `test_duplicate_row_label_across_sections_is_rejected`
- `test_aisle_outside_row_is_rejected`
- `test_duplicate_section_name_is_rejected`
- `test_empty_and_oversized_layouts_are_rejected`
- `test_price_tiers_convert_to_the_same_shape`

*HTTP flow:*
- `test_create_event_from_layout`
- ⭐ `test_bad_layout_creates_no_event`
- `test_price_tiers_path_still_works_and_stores_a_layout`
- ⭐⭐ `test_old_events_without_a_layout_still_work`

```bash
docker compose exec backend python -m pytest tests/ -q -k "layout or aisle"
```

---

## Kya toota

Is phase me koi bada bug nahi mila — aur wo khud batane layak hai.

Wajah shayad ye hai ki `layout.py` **pure functions** hai. Koi DB nahi,
koi network nahi, koi state nahi. Aise code ko test karna itna sasta hai
ki galtiyan likhte-likhte hi pakdi jaati hain, chalane se pehle.

Do chhoti cheezein zaroor thi:

**Migration is baar `NOT NULL` par nahi phansi.** Dono naye columns
nullable hain, to woh purana pattern ([Phase 14](14-dynamic-pricing.md),
16 me chauthi baar) laga hi nahi. Nullable rakhna sirf backwards
compatibility ke liye nahi tha — migration bhi usse aasan ho gayi.

**`layout` naam shadow kar raha tha.** Module ka naam `layout` hai aur
route me local variable bhi `layout` chahiye tha. `import layout as
seat_layout` se saaf kiya — warna wo bug baad me milta aur samajh nahi
aata.

---

## Jo jaan-boojh ke NAHI banaya

- **Layout edit nahi hota.** Event banne ke baad naksha badalna matlab
  seats badalna, aur unpe bookings ho sakti hain. Wahi asool jo base
  price ka hai ([Phase 14](14-dynamic-pricing.md)): bik chuki cheez ka
  reference nahi badalte.
- **Curved / angled rows nahi.** Stadium me rows seedhi nahi hoti. Uske
  liye har seat ka x/y coordinate chahiye hota — poora alag data model.
- **Templates nahi.** "Ye layout save karke agle event me use karo" —
  ab layout JSON store hota hai, to ye jodna aasan hoga.
- **Blocked/broken seats nahi.** Asli venue me kuch seats bikti hi nahi
  (pillar ke peeche, camera position). Uske liye ek aur seat status
  chahiye.

---

## Files

**Naye:**
| File | Kya |
|---|---|
| `backend/layout.py` | Validation + expansion, pure functions |
| `frontend/src/components/LayoutBuilder.jsx` | Builder + live preview |

**Badle:**
| File | Kya |
|---|---|
| `backend/models.py` | `Event.layout` (JSON), `Seat.section` — dono nullable |
| `backend/schemas.py` | `SeatLayout`, `LayoutSection`, `LayoutRow`; `EventCreate.layout` optional |
| `backend/routers/organizer.py` | Dono raaste ek hi expansion se |
| `backend/routers/events.py` | Detail me `layout` |
| `backend/routers/seats.py` | `SeatOut.section` |
| `frontend/src/components/SeatGrid.jsx` | Sections + aisles render |
| `frontend/src/pages/organizer/CreateEvent.jsx` | Simple / Layout builder toggle |

---

## Related

- [Phase 02 — Postgres + Models](02-postgres-models.md) — wo unique constraint jo validation ki wajah hai
- [Phase 10 — RBAC + Organizer](10-rbac-organizer.md) — `price_tiers` wala purana raasta
- [Phase 14 — Dynamic Pricing](14-dynamic-pricing.md) — "bik chuki cheez ka reference nahi badalte"
- [testing.md](../reference/testing.md) — commands
