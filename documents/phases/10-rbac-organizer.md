# Phase 10 — RBAC + Organizer Portal

Follow-up to [09-rate-limit-idempotency.md](09-rate-limit-idempotency.md).

**Implemented:** Three roles, organizer event creation, and admin platform statistics.

---

## Three distinct concepts often confused

| | Question | Solved in |
|---|---|---|
| **Authentication** | Who are you? | Phase 7 — via token |
| **Authorization** | What can you do? | **This phase** — via role |
| **Ownership** | Is this YOUR resource? | **This phase** — via `organizer_id` |

> ⭐ Ownership is the most frequently overlooked. **Passing a role check does not imply ownership of a resource.** Having an organizer role does not grant permission to edit any event — only your own.
>
> This is a common interview question; many candidates only mention the first two.

---

## Step 1 — Roles

```python
ROLE_ATTENDEE  = "attendee"     # view and book seats
ROLE_ORGANIZER = "organizer"    # create and manage your events
ROLE_ADMIN     = "admin"        # full platform access
```

### Why flat roles instead of a permission matrix?

Granular permissions (`event.create`, `event.delete`, `stats.read`...) are necessary for large-scale systems. Here, they would be **over-engineering**.

Practical note: **Moving from flat roles to granular permissions is easy; the reverse is difficult.** Today we use a `role` column; tomorrow we can add a `permissions` table and map roles to it.

### Column check constraint

```python
CheckConstraint(f"role IN ({', '.join(repr(r) for r in ALL_ROLES)})", name="ck_user_role")
```

This prevents typos like `"Organizer"` or `"orgnizer"` — the database enforces the constraint. This follows the same pattern used for `seats.status`.

---

## Step 2 — `require_role` dependency

```python
def require_role(*roles: str):
    def dependency(user: User = Depends(get_current_user)) -> User:
        if user.role not in roles:
            raise HTTPException(403, f"Requires {' or '.join(roles)} role")
        return user
    return dependency
```

Usage:
```python
user: User = Depends(require_role(ROLE_ORGANIZER, ROLE_ADMIN))
```

### ⚠️ Why 403 for roles, but 404 for bookings?

| Case | Status | Reason |
|---|---|---|
| Attendee on organizer route | **403** | Nothing to hide. The endpoint is visible in `/docs`. Access is simply denied. |
| Organizer A, accessing Organizer B's event | **404** | We must hide the fact that the event **exists**. |
| User A, accessing User B's booking (Phase 7) | **404** | Same reason. |

> Rule: Lack of **capability** = 403. Need to hide **existence** = 404.

---

## Step 3 — Ownership check

```python
def _owned_event(event_id: int, user: User, db: Session) -> Event:
    event = db.get(Event, event_id)
    if event is None:
        raise HTTPException(404, "Event not found")

    if user.role != ROLE_ADMIN and event.organizer_id != user.id:
        raise HTTPException(404, "Event not found")   # not 403

    return event
```

Every organizer endpoint uses this to retrieve the event. Without this, any organizer could call `/api/organizer/events/5` to edit another user's event — the **role check would pass, but ownership would fail.**

Admins are exempt and can view/edit everything.

---

## Step 4 — Event + seats generation

Organizers do not create seats individually. They define **price tiers**:

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

Tiers are applied **top-to-bottom** — the first tier starts at row A. Example: A-B @2500, C-E @1200, F-J @800. Total 10 rows × 10 = **100 seats**.

```python
row_index = 0
for tier in payload.price_tiers:
    for _ in range(tier.rows):
        label = ROW_LABELS[row_index]
        seats.extend(Seat(...) for n in range(1, payload.seats_per_row + 1))
        row_index += 1

db.bulk_save_objects(seats)
```

`bulk_save_objects` is significantly faster than 2000 individual INSERTs.

### Limits — preventing database bloat

```python
if total_rows > 26:            # A-Z max
if total_seats > 2000:         # max per event
```

Pydantic validation: `seats_per_row: int = Field(..., gt=0, le=50)`, `price_tiers: max_length=10`.

### Why exclude layout and pricing from `EventUpdate`?

```python
class EventUpdate(BaseModel):
    name: str | None = None
    venue: str | None = None
    starts_at: datetime | None = None
    description: str | None = None
    category: str | None = None
    # seats_per_row and price_tiers are intentionally omitted
```

Changing seats or pricing after tickets have been sold is invalid. To change the layout, the event must be deleted and recreated — and deletion is only permitted if there are no confirmed bookings.

**Omitting the fields from the schema** is the cleanest approach, superior to adding `if` logic in the route.

### The impact of `exclude_unset`

```python
for field, value in payload.model_dump(exclude_unset=True).items():
    setattr(event, field, value)
```

Without `exclude_unset`, fields **not sent** by the client would be set to `None` — meaning `{"name": "New"}` would inadvertently set the description to NULL.

---

## Step 5 — Delete guard

```python
confirmed = db.scalar(
    select(func.count(Booking.id)).where(
        Booking.event_id == event_id, Booking.status == BOOKING_CONFIRMED
    )
)
if confirmed:
    raise HTTPException(409, f"{confirmed} confirmed bookings exist — cannot delete")
```

⚠️ **This is a critical business rule.** Since cascade delete is enabled, this check prevents a single DELETE command from wiping out **purchased tickets**.

---

## ⭐ Bug: SQLAlchemy's over-eager DB management

Test failure:

```
AssertionError: assert 500 == 204
```

Logs:
```
sqlalchemy.exc.IntegrityError: (psycopg2.errors.NotNullViolation)
null value in column "seat_id" of relation "bookings" violates not-null constraint
```

### The cause

The database FK has `ON DELETE CASCADE`:
```python
seat_id: Mapped[int] = mapped_column(ForeignKey("seats.id", ondelete="CASCADE"))
```

However, SQLAlchemy does not wait for the DB during `db.delete(event)`. It tries to be "helpful":

1. Loads all event seats into memory.
2. Loads all bookings for those seats.
3. Executes `UPDATE bookings SET seat_id = NULL`.

Since `seat_id` is NOT NULL, this triggers a violation.

### Fix — `passive_deletes=True`

```python
seats: Mapped[list["Seat"]] = relationship(
    back_populates="event", cascade="all, delete-orphan", passive_deletes=True
)
```

This instructs SQLAlchemy: *"Do nothing; the DB's `ON DELETE` will handle it."*

> **Rule:** Whenever using `ondelete="CASCADE"` or `"SET NULL"` on an FK, set `passive_deletes=True` on the relationship. Otherwise, both attempt to cascade, causing a conflict.
>
> Bonus: It is faster — SQLAlchemy no longer loads thousands of child rows into memory.

Applied to: `Event.seats`, `Seat.bookings`, and `User.bookings`.

---

## Step 6 — Admin stats

```python
active_locks = sum(1 for _ in redis_client.scan_iter("seat:*:lock"))
live = sum(manager.count(event_id) for event_id in manager.rooms())
```

Data is aggregated from **three sources**: Postgres (users, events, bookings, revenue), Redis (active locks), and worker memory (WebSocket clients).

> ⚠️ Used `scan_iter`, not `KEYS`. `KEYS` blocks the entire Redis instance — never use in production. `scan` is cursor-based.

> ⚠️ `live_connections` is **worker-specific**. In a multi-worker setup, each worker reports its own count. For an accurate total, this must be moved to Redis — not required yet, but noted as a limitation.

### Avoiding N+1

We need counts for 20 organizer events. Naive approach: one query per event = 20 queries.

```python
seat_rows = db.execute(
    select(Seat.event_id, Seat.status, func.count(Seat.id))
    .where(Seat.event_id.in_(event_ids))
    .group_by(Seat.event_id, Seat.status)
).all()
```

One query, all counts. **N+1 is the most common performance bug** and a frequent interview topic.

---

## Step 7 — Frontend

### Role-gated navigation

```jsx
const isOrganizer = user?.role === 'organizer' || user?.role === 'admin'
const isAdmin = user?.role === 'admin'
```

Sidebar sections "Organizer" and "Admin" are rendered based on the user's role.

### ⚠️ Client-side gates are NOT security

```jsx
function RequireRole({ roles, children }) {
  const { user } = useAuth()
  return roles.includes(user.role) ? children : <Navigate to="/" replace />
}
```

This is **UX only**. State manipulation via React DevTools is trivial. The real gate is the backend `require_role` — bypassing the UI will still result in a 403.

> Interview tip: State clearly: *"Frontend role checks are for UX, preventing access to pages that would return a 403 anyway. Security is enforced on the backend."*

### Pages

| Route | Role | Purpose |
|---|---|---|
| `/organizer/events` | organizer, admin | Events list + sales bar + revenue |
| `/organizer/events/new` | organizer, admin | Create form with live seat preview |
| `/admin` | admin | Platform stats, 10s refresh |

**Create form live preview** — as tiers change, `A–B`, `C–E` labels and "40 seats" update instantly. Backend limits (26 rows, 2000 seats) are checked here too, providing immediate feedback.

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
# Organizer 2 attempts to edit Organizer 1's event
curl -X PATCH -H "Authorization: Bearer $ORG2" -d '{"name":"HACKED"}' \
  http://localhost:8000/api/organizer/events/2
# -> 404

curl -X DELETE -H "Authorization: Bearer $ORG2" http://localhost:8000/api/organizer/events/2
# -> 404

# Organizer 2 sees nothing in their list
curl -H "Authorization: Bearer $ORG2" http://localhost:8000/api/organizer/events
# -> []

# Admin sees everything
curl -H "Authorization: Bearer $ADM" http://localhost:8000/api/organizer/events
# -> "Test Comedy Night", "Arijit Singh Live"

# Owner edits their own event
curl -X PATCH -H "Authorization: Bearer $ORG" -d '{"venue":"Habitat World"}' ...
# -> 200
```

### 4. Delete guard

```bash
# After booking
curl -X DELETE -H "Authorization: Bearer $ORG" http://localhost:8000/api/organizer/events/2
# {"detail":"1 confirmed booking exists — event cannot be deleted"}
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

9 new tests: role in `/me`, attendee blocked, organizer blocked from admin, admin sees all, price tiers, attendee can't create, **ownership isolation**, **delete guard**, layout limits.

### 7. Browser

Login with three accounts — the sidebar updates accordingly:

| Login | Sidebar |
|---|---|
| `demo@seatpulse.dev` | Dashboard, Events, My Bookings, Profile |
| `organizer@seatpulse.dev` | + **Organizer** section (My Events, Create Event) |
| `admin@seatpulse.dev` | + **Admin** section (Platform Stats) |

Profile page displays a role badge — admin red, organizer violet.

---

## Interview Q&A

| Question | Answer |
|---|---|
| "How did you implement RBAC?" | `role` column + `require_role` dependency. Role alone is insufficient — **ownership** is checked separately. An organizer role does not grant edit access to all events. |
| "Difference between 403 and 404?" | Lack of capability = 403 (endpoint is public knowledge). Hiding existence = 404 (attacker shouldn't know the resource exists). |
| "Why no granular permissions?" | Over-engineering for a three-role system. Moving from flat to granular is easy; the reverse is hard. |
| "Is the frontend role check secure?" | Absolutely not. It is UX only, bypassable via DevTools. The real gate is the backend. |
| "What happens to bookings if an organizer deletes an event?" | Deletion is blocked if confirmed bookings exist — 409. Paid tickets must never disappear. |
| "How to avoid N+1?" | Aggregate counts for all events in a single `GROUP BY` query, rather than one query per event. |

---

## Common Problems

| Problem | Fix |
|---|---|
| `NotNullViolation` on event delete | Relationship requires `passive_deletes=True`. |
| Organizer cannot see their events | Check if `organizer_id` is set. Older events may have NULL. |
| Organizer section missing in sidebar | Check if `role` is returned in `/api/auth/me` and added to `UserOut`. |
| 403 despite correct role | Token may be stale — re-login after role change. |
| Migration fail — `role` NOT NULL | Requires `server_default='attendee'` for existing rows. |

---

## Files

```
backend/
├── models.py                   ← roles, Event.organizer_id, passive_deletes
├── auth.py                     ← require_role()
├── schemas.py                  ← EventCreate/Update, PriceTier, AdminStatsOut, role in UserOut
├── websocket.py                ← manager.rooms()
├── seed.py                     ← organizer + admin demo accounts
├── main.py                     ← new routers
├── routers/
│   ├── organizer.py            ← new ⭐ CRUD + ownership + seat generation
│   ├── admin.py                ← new (platform stats)
│   └── auth.py                 ← role in response
├── tests/test_concurrency.py   ← 9 new tests (20 → 29)
└── alembic/versions/...        ← role + organizer_id migration

frontend/src/
├── api.js                      ← organizer + admin calls
├── App.jsx                     ← RequireRole + new routes
├── layout/
│   ├── Sidebar.jsx             ← role-gated sections
│   └── icons.jsx               ← IconPlus
└── pages/
    ├── organizer/
    │   ├── MyEvents.jsx        ← new (sales bar, delete)
    │   └── CreateEvent.jsx     ← new (price tiers, live preview)
    ├── admin/AdminStats.jsx    ← new
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
- [../roadmap.md](../roadmap.md) — next steps
