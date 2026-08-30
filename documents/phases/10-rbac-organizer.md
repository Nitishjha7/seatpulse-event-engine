# Phase 10 — RBAC + Organizer Portal

[09-rate-limit-idempotency.md](09-rate-limit-idempotency.md) ke baad ka kaam.

**Kya bana:** teen roles, organizer apne events bana sakta hai, admin ko platform stats dikhte hain.

---

## Teen alag cheezein jo log mila dete hain

| | Sawaal | Kahan solve hui |
|---|---|---|
| **Authentication** | Tum kaun ho? | Phase 7 — token se |
| **Authorization** | Tum kya kar sakte ho? | **Ye phase** — role se |
| **Ownership** | Ye cheez TUMHARI hai? | **Ye phase** — `organizer_id` se |

> ⭐ Teesri sabse zyada bhulai jaati hai. **Role check pass hone ka matlab ye nahi ki har resource tumhara hai.** Organizer role hone se tum kisi bhi event ko edit nahi kar sakte — sirf apne wale ko.
>
> Ye interview me poocha jata hai, aur bahut log sirf pehli do batate hain.

---

## Step 1 — Roles

```python
ROLE_ATTENDEE  = "attendee"     # seats dekho aur book karo
ROLE_ORGANIZER = "organizer"    # apne events banao aur manage karo
ROLE_ADMIN     = "admin"        # poore platform ka access
```

### Sirf teen flat roles kyu, permission matrix kyu nahi

Granular permissions (`event.create`, `event.delete`, `stats.read`...) bade systems me chahiye hoti hain. Yahan wo **over-engineering** hoti.

Aur ek practical baat: **flat role se granular pe jaana aasan hai; ulta bahut mushkil.** Aaj `role` column hai, kal `permissions` table add karke role usme map kar sakte ho.

### Column pe check constraint

```python
CheckConstraint(f"role IN ({', '.join(repr(r) for r in ALL_ROLES)})", name="ck_user_role")
```

Typo se koi `"Organizer"` ya `"orgnizer"` na ban jaye — DB hi rok dega. Wahi pattern jo `seats.status` pe laga hai.

---

## Step 2 — `require_role` dependency

```python
def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"Is kaam ke liye {' ya '.join(roles)} role chahiye")
        return user
    return dependency
```

Use:
```python
user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))
```

### ⚠️ Yahan 403, booking me 404 — dono kyu alag

| Case | Status | Kyu |
|---|---|---|
| Attendee organizer route pe | **403** | Chhupane ko kuch hai hi nahi. Endpoint `/docs` me dikh raha hai. Bas permission nahi hai |
| Organizer A, organizer B ka event | **404** | Yahan chhupana hai ki wo event **exist karta hai** |
| User A, user B ki booking (Phase 7) | **404** | Same wajah |

> Rule: **capability** ki kami = 403. **Existence** chhupani ho = 404.

---

## Step 3 — Ownership check

```python
def _owned_event(event_id: int, user: User, db: Session) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event nahi mila")

    if user.role != ROLE_ADMIN and event.organizer_id != user.id:
        raise HTTPException(404, "Event nahi mila")   # 403 nahi

    return event
```

Har organizer endpoint isse hi event nikalta hai. Bina iske koi bhi organizer `/api/organizer/events/5` chala ke kisi aur ka event edit kar deta — **role check pass ho jata, par ownership fail hoti.**

Admin ko chhoot hai — wo sab dekh aur edit kar sakta hai.

---

## Step 4 — Event + seats ek saath

Organizer ko har seat alag se nahi banani. Wo **price tiers** deta hai:

```json
{
  "seats_per_row": 10,
  "price_tiers": [
    { "rows": 2, "price": 2500 },
    { "rows": 3, "price": 1200 },
    { "rows": 5, "price": 800 }
  ]
}
```

Tiers **upar se neeche** lagte hain — pehla tier row A se. Matlab: A-B @2500, C-E @1200, F-J @800. Total 10 rows × 10 = **100 seats**.

```python
row_index = 0
for tier in payload.price_tiers:
    for _ in range(tier.rows):
        label = ROW_LABELS[row_index]
        seats.extend(Seat(...) for n in range(1, payload.seats_per_row + 1))
        row_index += 1

db.bulk_save_objects(seats)
```

`bulk_save_objects` — 2000 alag INSERT se bahut tez.

### Limits — bina inke koi DB bhar sakta hai

```python
if total_rows > 26:            # A-Z se zyada rows nahi
if total_seats > 2000:         # ek event me max
```

Pydantic me bhi: `seats_per_row: int = Field(..., gt=0, le=50)`, `price_tiers: max_length=10`.

### `EventUpdate` me layout aur pricing kyu nahi

```python
class EventUpdate(BaseModel):
    name: str | None = None
    venue: str | None = None
    starts_at: datetime | None = None
    description: str | None = None
    category: str | None = None
    # seats_per_row aur price_tiers JAAN-BOOJH KE nahi hain
```

Jab log tickets khareed chuke hon, tab seats ya price badalna galat hai. Layout badalna ho to event delete karke naya banana padega — aur delete tabhi hoga jab koi confirmed booking na ho.

**Schema me field hi na rakhna** sabse saaf tarika hai — route me `if` likhne se behtar.

### `exclude_unset` ka farak

```python
for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(event, field, value)
```

Bina `exclude_unset` ke, client ne jo field **bheji hi nahi** wo bhi `None` set ho jaati — matlab `{"name": "New"}` bhejne se description NULL ho jata.

---

## Step 5 — Delete guard

```python
confirmed = db.scalar(
    select(func.count(Booking.id)).where(
        Booking.event_id == event_id, Booking.status == BOOKING_CONFIRMED
    )
)
if confirmed:
    raise HTTPException(409, f"{confirmed} confirmed booking hain — delete nahi ho sakta")
```

⚠️ **Ye sabse important business rule hai.** Cascade delete laga hua hai, to bina is check ke ek DELETE se logon ki **khareedi hui tickets gayab** ho jaatin.

---

## ⭐ Bug: SQLAlchemy ne DB ka kaam khud karne ki koshish ki

Test fail hua:

```
AssertionError: assert 500 == 204
```

Logs me:
```
sqlalchemy.exc.IntegrityError: (psycopg2.errors.NotNullViolation)
null value in column "seat_id" of relation "bookings" violates not-null constraint
```

### Kya ho raha tha

DB me FK pe `ON DELETE CASCADE` laga hua hai:
```python
seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"))
```

Par `db.delete(event)` par **SQLAlchemy DB ka intezaar nahi karta**. Wo khud "helpful" banne ki koshish karta hai:

1. Event ki saari seats memory me load karta hai
2. Un seats ki saari bookings load karta hai
3. Aur `UPDATE bookings SET seat_id = NULL` chalata hai

Par `seat_id` NOT NULL hai → violation.

### Fix — `passive_deletes=True`

```python
seats: Mapped[list["Seat"]] = relationship(
    back_populates="event", cascade="all, delete-orphan", passive_deletes=True
)
```

Ye SQLAlchemy se kehta hai: *"tu kuch mat kar, DB ka `ON DELETE` khud sambhal lega."*

> **Rule:** jab bhi FK pe `ondelete="CASCADE"` ya `"SET NULL"` lagao, relationship pe `passive_deletes=True` bhi lagao. Warna dono cascade karne ki koshish karte hain aur takra jaate hain.
>
> Bonus: ye tez bhi hai — SQLAlchemy hazaaron child rows memory me load nahi karta.

Maine ye teeno jagah lagaya: `Event.seats`, `Seat.bookings`, `User.bookings`.

---

## Step 6 — Admin stats

```python
active_locks = sum(1 for _ in redis_client.scan_iter("seat:*:lock"))
live = sum(manager.count(event_id) for event_id in manager.rooms())
```

Data **teen jagah** se aata hai — Postgres (users, events, bookings, revenue), Redis (active locks), aur is worker ki memory (WebSocket clients).

> ⚠️ `scan_iter` use kiya, `KEYS` nahi. `KEYS` poore Redis ko block kar deta hai — production me kabhi mat use karna. `scan` cursor-based hai.

> ⚠️ `live_connections` **sirf is worker ka** count hai. Multi-worker me har worker apna alag number dega. Sahi total ke liye ye bhi Redis me rakhna padega — abhi wo zaroorat nahi, par limitation doc me likhi hui hai (aur UI me bhi dikhayi hai).

### N+1 se bachna

Organizer ke 20 events ke counts chahiye. Naive tarika: har event ke liye ek query = 20 queries.

```python
seat_rows = db.execute(
    select(Seat.event_id, Seat.status, func.count(Seat.id))
    .where(Seat.event_id.in_(event_ids))
    .group_by(Seat.event_id, Seat.status)
).all()
```

Ek query, sab events ke counts. **N+1 sabse common performance bug hai** aur interview me bhi poocha jata hai.

---

## Step 7 — Frontend

### Role-gated nav

```jsx
const isOrganizer = user?.role === 'organizer' || user?.role === 'admin'
const isAdmin = user?.role === 'admin'
```

Sidebar me "Organizer" aur "Admin" sections role ke hisaab se dikhte hain.

### ⚠️ Client-side gate security NAHI hai

```jsx
function RequireRole({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to="/" replace />
}
```

Ye **sirf UX** ke liye hai. React DevTools se state badalna trivial hai. Asli gate backend ka `require_role` hai — bypass karke bhi user ko 403 hi milega.

> Interview me ye khud bolna: *"Frontend role check sirf isliye hai ki user ko wo page na dikhe jo waise bhi 403 dega. Security backend me hai."*

### Pages

| Route | Kaun | Kya |
|---|---|---|
| `/organizer/events` | organizer, admin | Apne events + sales bar + revenue |
| `/organizer/events/new` | organizer, admin | Create form with live seat preview |
| `/admin` | admin | Platform stats, 10s pe refresh |

**Create form me live preview** — jaise tier badalte ho, `A–B`, `C–E` labels aur "40 seats" turant update hote hain. Backend ki limits (26 rows, 2000 seats) yahan bhi check hoti hain, taki user ko submit karne se pehle pata chal jaye.

---

## ✅ Proof

### 1. RBAC matrix

```bash
login() { curl -s -X POST http://localhost:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"email\":\"$1\",\"password\":\"demo1234\"}" \
  | sed -n 's/.*"access_token":"\([^"]*\)".*/\1/p'; }

ATT=$(login demo@seatpulse.dev)
ORG=$(login organizer@seatpulse.dev)
ADM=$(login admin@seatpulse.dev)
```

| Role | `/organizer/events` | `/admin/stats` |
|---|---|---|
| attendee | **403** | **403** |
| organizer | **200** | **403** |
| admin | **200** | **200** |

### 2. Price tiers

```bash
curl -X POST -H "Authorization: Bearer $ORG" -H "Content-Type: application/json" \
  -d '{"name":"Test Comedy Night","venue":"Habitat, Mumbai",
       "starts_at":"2026-12-01T19:30:00Z","seats_per_row":8,
       "price_tiers":[{"rows":2,"price":2000},{"rows":3,"price":900}]}' \
  http://localhost:8000/api/organizer/events
```

```sql
SELECT row_label, count(*), min(price) FROM seats WHERE event_id=2 GROUP BY row_label;
```
```
 A | 8 | 2000.00
 B | 8 | 2000.00
 C | 8 |  900.00
 D | 8 |  900.00
 E | 8 |  900.00
```

### 3. ⭐ Ownership isolation

```bash
# Dusra organizer pehle ka event edit kare
curl -X PATCH -H "Authorization: Bearer $ORG2" -d '{"name":"HACKED"}' \
  http://localhost:8000/api/organizer/events/2
# -> 404

curl -X DELETE -H "Authorization: Bearer $ORG2" http://localhost:8000/api/organizer/events/2
# -> 404

# Usse apni list me kuch dikhta bhi nahi
curl -H "Authorization: Bearer $ORG2" http://localhost:8000/api/organizer/events
# -> []

# Admin ko sab dikhte hain
curl -H "Authorization: Bearer $ADM" http://localhost:8000/api/organizer/events
# -> "Test Comedy Night", "Arijit Singh Live"

# Owner khud edit kare
curl -X PATCH -H "Authorization: Bearer $ORG" -d '{"venue":"Habitat World"}' ...
# -> 200
```

### 4. Delete guard

```bash
# Booking karne ke baad
curl -X DELETE -H "Authorization: Bearer $ORG" http://localhost:8000/api/organizer/events/2
# {"detail":"1 confirmed booking hain — event delete nahi ho sakta"}
```

### 5. Admin stats

```json
{"users":504,"organizers":1,"events":1,"seats":100,
 "bookings_confirmed":0,"bookings_cancelled":7,"revenue":0.0,
 "active_locks":0,"live_connections":0}
```

### 6. Test suite

```
29 passed in 39.88s
```

9 naye tests: role in `/me`, attendee blocked, organizer blocked from admin, admin sees all, price tiers, attendee can't create, **ownership isolation**, **delete guard**, layout limits.

### 7. Browser

Teen accounts se login karke dekho — sidebar har baar alag dikhega:

| Login | Sidebar me |
|---|---|
| `demo@seatpulse.dev` | Sirf Dashboard, Events, My Bookings, Profile |
| `organizer@seatpulse.dev` | + **Organizer** section (My Events, Create Event) |
| `admin@seatpulse.dev` | + **Admin** section (Platform Stats) |

Profile page pe role badge bhi dikhta hai — admin laal, organizer violet.

---

## Interview me kya poocha jayega

| Sawaal | Jawab |
|---|---|
| "RBAC kaise implement kiya?" | `role` column + `require_role` dependency. Par sirf role kaafi nahi — **ownership** alag check hai. Organizer role hone se koi bhi event edit nahi kar sakte |
| "403 aur 404 me kya farak?" | Capability ki kami = 403 (endpoint public knowledge hai). Existence chhupani ho = 404 (attacker ko pata na chale ki resource hai) |
| "Granular permissions kyu nahi?" | Teen roles wale system me wo over-engineering hai. Flat se granular jaana aasan hai, ulta mushkil |
| "Frontend me role check hai — wo secure hai?" | Bilkul nahi. Wo sirf UX hai, DevTools se bypass ho jata hai. Asli gate backend me hai |
| "Organizer delete kare to bookings ka kya?" | Delete tabhi allowed hai jab koi confirmed booking na ho — 409. Paid tickets kabhi gayab nahi honi chahiye |
| "N+1 se kaise bache?" | Sab events ke counts ek `GROUP BY` query me, har event ke liye alag query nahi |

---

## Common Problems

| Problem | Fix |
|---|---|
| `NotNullViolation` on event delete | Relationship pe `passive_deletes=True` chahiye |
| Organizer ko apne events nahi dikh rahe | `organizer_id` set hua? Purane events ka NULL hota hai |
| Sidebar me Organizer section nahi dikh raha | `/api/auth/me` me `role` aa raha hai? `UserOut` me field add ki thi |
| 403 aa raha hai jabki role sahi hai | Token purana ho sakta hai — role badalne ke baad dobara login karo |
| Migration fail — `role` NOT NULL | `server_default='attendee'` chahiye, existing rows hain |

---

## Files

```
backend/
├── models.py                   ← roles, Event.organizer_id, passive_deletes
├── auth.py                     ← require_role()
├── schemas.py                  ← EventCreate/Update, PriceTier, AdminStatsOut, role in UserOut
├── websocket.py                ← manager.rooms()
├── seed.py                     ← organizer + admin demo accounts
├── main.py                     ← naye routers
├── routers/
│   ├── organizer.py            ← naya ⭐ CRUD + ownership + seat generation
│   ├── admin.py                ← naya (platform stats)
│   └── auth.py                 ← role in response
├── tests/test_concurrency.py   ← 9 naye tests (20 → 29)
└── alembic/versions/...        ← role + organizer_id migration

frontend/src/
├── api.js                      ← organizer + admin calls
├── App.jsx                     ← RequireRole + naye routes
├── layout/
│   ├── Sidebar.jsx             ← role-gated sections
│   └── icons.jsx               ← IconPlus
└── pages/
    ├── organizer/
    │   ├── MyEvents.jsx        ← naya (sales bar, delete)
    │   └── CreateEvent.jsx     ← naya (price tiers, live preview)
    ├── admin/AdminStats.jsx    ← naya
    └── Profile.jsx             ← role badge
```

---

## Demo accounts

| Email | Password | Role |
|---|---|---|
| `demo@seatpulse.dev` | `demo1234` | attendee |
| `organizer@seatpulse.dev` | `demo1234` | organizer |
| `admin@seatpulse.dev` | `demo1234` | admin |

---

## Commit

```bash
git add .
git commit -m "Phase 10: RBAC and organizer portal

- Three flat roles with a DB check constraint
- require_role dependency; ownership checked separately from role
- Organizer event CRUD with price-tier seat generation
- Delete blocked while confirmed bookings exist
- Fix: passive_deletes so SQLAlchemy stops fighting ON DELETE CASCADE"
```

---

## Related

- [07-auth-google-oauth.md](07-auth-google-oauth.md) — authentication
- [../reference/testing.md](../reference/testing.md) — test commands
- [../roadmap.md](../roadmap.md) — aage kya
