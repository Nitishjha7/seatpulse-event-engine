# Phase 8 — Dashboard UI Shell

[Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) follow-up.

**Changes:** Transitioned from a single-page app to a **multi-page dashboard** — including a sidebar, topbar, routes, and a cohesive theme.

> In Phases 1-7, the UI was a prototype — everything on one page. As we add the organizer portal, reports, and settings, a **shell** is required now. Building it later would have forced a rewrite of every page layout.

---

## Design Principle: Show Only Truth

The mockup contained tiles that looked good but were **false**:

| Mockup Content | Why Removed | Replacement |
|---|---|---|
| "Multiple Payments — UPI, Cards, Wallets" | Payments **do not exist** | "Redis Seat Locking — atomic SET NX EX" |
| "4.8 ★ (12.5K reviews)" | No review system | Real event seat count |
| "Loved by 50K+ Users" | Only 500 seeded test users | "Zero Overselling — 200 users, 1 booking" |
| "24/7 Customer Support" | No support system | "Verified by Load Tests — 8,154 requests, 0 failures" |
| "50,000+ Seats" | Only 100 seats | `seats.length` (actual count) |

> ⚠️ **Fake stats are a major red flag in portfolio projects.** An interviewer will check `/docs` or count the grid, and then doubt your *real* claims.
>
> Honestly, "200 concurrent users, exactly 1 booking" is more impressive than "Loved by 50K+ users." One is a measured metric; the other is a sticker.

---

## Step 1 — Add Router

```json
"react-router-dom": "^7.1.0"
```

**Why a router instead of state-based navigation:**
- URLs are shareable (e.g., `/bookings` can be sent directly).
- Browser back button functionality.
- Each page is its own component, avoiding a 400-line `App.jsx`.

---

## Step 2 — ⭐ BookingContext — The Most Important Decision

Previously, all state lived in `App.jsx`. Now, multiple pages require the same data.

**Naive approach (incorrect):** Each page fetches its own data.
**Problem:** Each page opens its own **WebSocket**. 4 pages = 4 connections per user, with updates firing 4 times.

**Correct approach:** A `BookingProvider` placed **outside** the `Routes`.

```jsx
<BookingProvider key={user.id}>
  <Routes>
    <Route element={<AppShell />}>
      <Route index element={<Dashboard />} />
      ...
```

Benefits:
- **Single WebSocket connection** — no reconnection on page navigation.
- Seat state, holds, and countdowns are synchronized across all pages.
- Navigating from Dashboard to `/bookings` and back keeps the hold countdown **active**.

> `key={user.id}` — The provider re-initializes when the user changes. Without this, the previous user's bookings would persist after a new login.

**Provider contents:** event, seats, bookings, counts, selectedSeat, lockSecondsLeft, WebSocket, and all actions (`selectSeat`, `releaseHold`, `confirmBooking`, `cancel`).

---

## Step 3 — Layout

```
src/layout/
├── AppShell.jsx     sidebar + topbar + <Outlet />
├── Sidebar.jsx      nav + promo card
├── Topbar.jsx       health pills + user menu
└── icons.jsx        inline SVG icons
```

### What `<Outlet />` does
React Router renders the current page here. Benefit: **Sidebar and topbar do not re-mount** when changing pages — only the content changes.

### Icons — Why no library?

```js
// icons.jsx — 15 inline SVGs, ~100 lines
```

We only needed 15 icons. Lucide/Heroicons would add a full package, increase bundle size, and add a dependency. Using `currentColor` allows Tailwind text color classes to handle styling.

### Sidebar "Coming Soon"

```jsx
const NAV  = [Dashboard, Events, My Bookings, Profile]   // implemented
const SOON = [Reports, Settings]                          // disabled, greyed
```

Unimplemented pages are **disabled and unlinked**. The shell looks complete without broken links.

### Topbar

- **Health pills** — DB, Redis, Live (WebSocket). On `sm` screens, text is hidden, showing only dots.
- **User menu** — Avatar + dropdown. Closes on outside click (`mousedown` listener).
- **Hamburger** — Mobile only; triggers sidebar slide-over.

---

## Step 4 — Theme

CSS variables in `index.css`:

```css
:root {
  --bg: #08080c;        /* page — purple-black */
  --panel: #101018;     /* cards */
  --panel-2: #16161f;   /* inner card blocks */
  --border: #23232f;
  --accent: #7c3aed;    /* violet */
}
```

Used in Tailwind via `bg-[var(--panel)]`. Change colors in **one place**.

Two small additions for visual impact:

```css
background-image:
  radial-gradient(900px 400px at 15% -10%, rgba(124,58,237,0.10), transparent),
  radial-gradient(700px 350px at 95% 0%, rgba(37,99,235,0.07), transparent);
```

A subtle purple glow instead of flat black makes the screen feel "alive." Also, a custom scrollbar replaces the default dark theme eyesore.

---

## Step 5 — Hero Banner

The mockup had a concert photo. I used **no images**:

- External images can be blocked by CSP or break offline.
- Keeps the repo lightweight.
- Avoids licensing issues.

Used **CSS gradient + inline SVG** instead:
- `<polygon>` for stage light beams.
- `radial-gradient` repeats for crowd silhouettes.
- Self-contained, zero network requests.

```jsx
<polygon points="60,0 130,0 230,260 0,260" fill="url(#beam)" />
```

---

## Step 6 — Pages

| Page | Route | Purpose |
|---|---|---|
| Dashboard | `/` | Hero + seat grid + right rail (summary, hold, bookings) + feature strip |
| Events | `/events` | Event cards, live counts |
| My Bookings | `/bookings` | Stats (confirmed/cancelled/spent) + full list |
| Profile | `/profile` | Account, session info, system health |
| API Docs | external | Backend `/docs` |

The `*` route redirects to `/` — no blank pages on invalid URLs.

### Responsive

```jsx
<div className="grid gap-5 xl:grid-cols-[1fr_380px]">
```

Below `xl`, the right rail moves **below** the grid. Sidebar becomes a slide-over below `lg`.
The seat grid uses `overflow-x-auto` — it scrolls on small screens instead of squeezing.

---

## Step 7 — Booking Confirmed Modal

Replaced a simple green text notification with a full success modal.

### No backend data fetching required
All modal data is **already present**:

| Field | Source |
|---|---|
| Seat No. | `selectedSeat` (captured during hold) |
| Price | `booking.amount` — from DB |
| Booking ID | `booking.id` — from DB, formatted as `SP00042` |
| Event | `event` — from context |

```js
// bookingRef(42) -> "SP00042"
export function bookingRef(id) {
  return `SP${String(id).padStart(5, '0')}`
}
```

> This is a **display format**, not a fake ID. It uses the actual integer ID, just styled as a ticket reference.

### Seat capture is mandatory

```js
const created = await createBooking(selectedSeat.id)

// Capture NOW for the modal
setLastBooking({ booking: created, seat: selectedSeat, event })

setSelectedSeat(null)
```

If captured later, `selectedSeat` would be null, and the seat would be in the `booked` state after a `refresh()`.

### Confetti — No library

```jsx
const pieces = useMemo(() => Array.from({ length: 40 }, () => ({ ... })), [count])
```

40 small divs with random direction/rotation/color/delay. CSS variables (`--x`, `--r`) drive the animation.

| Item | Why |
|---|---|
| No library (canvas-confetti ~30KB) | A full package is overkill for 40 divs |
| `useMemo` | Prevents random numbers from regenerating on every render |
| `pointer-events-none` | Allows clicking buttons behind the confetti |

### Essential Modal Requirements

```js
// 1. Close on Escape
document.addEventListener('keydown', (e) => e.key === 'Escape' && onClose())

// 2. Background scroll lock
document.body.style.overflow = 'hidden'

// 3. Close on backdrop click, but not on inner click
<div onClick={onClose}>
  <div onClick={(e) => e.stopPropagation()}>

// 4. Screen readers
role="dialog" aria-modal="true" aria-labelledby="..."
```

---

## Step 8 — Event Detail Page

Route: `/events/:id`

### New Model Columns

```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
category:    Mapped[str | None] = mapped_column(String(40), nullable=True)
```

Used `Text` instead of `String(n)` for descriptions.

Migration:
```bash
docker compose exec backend alembic revision --autogenerate -m "add event description and category"
docker compose exec backend alembic upgrade head
```

### Price Range — Single Query

```python
price_range = db.execute(
    select(func.min(Seat.price), func.max(Seat.price)).where(Seat.event_id == event_id)
).one()
```

### ⚠️ Real Data vs. Fake Chips

| Chip | Data Source |
|---|---|
| `🎵 Music` | `event.category` |
| `📍 Mumbai` | City from `event.venue` |
| `🎫 100 seats` | `event.total_seats` |
| `💰 ₹800 – ₹2500` | `min_price` / `max_price` |
| `👥 99 available now` | Live count (WebSocket) |

The last one is best — it updates live.

### Description Paragraphs

```jsx
{event.description.split('\n\n').map((para, i) => (
  <p key={i}>{para}</p>
))}
```

Used `\n\n` in the DB to separate paragraphs. Avoided `dangerouslySetInnerHTML` to prevent XSS.

---

## Step 9 — UI Smoothness

```css
@keyframes rise    { from { opacity:0; transform: translateY(8px); } }
@keyframes pop-in  { from { opacity:0; transform: translateY(12px) scale(0.96); } }
@keyframes fade-in { from { opacity:0; } }
```

- **Pages:** `animate-rise` for entry.
- **Modal:** `pop-in` with `cubic-bezier` for a slight bounce.
- **Seats:** Hover effect `-translate-y-0.5`.

### ⚠️ Accessibility

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

**This block should be in every project.**

```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
```

Uses `:focus-visible` so keyboard users see the ring, but mouse users don't.

---

## Step 10 — Components

`BookingsList` includes a `compact` prop for the dashboard view. `BookingPanel.jsx` was **deleted** as its logic was consolidated.

---

## Step 11 — Rebuild

```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

> `--renew-anon-volumes` is required to refresh `node_modules`. **Do not use `down -v`**, as it will wipe the Postgres data.

---

## ✅ Proof

- **Hold and navigate:** Countdown persists across pages (proves state provider works).
- **Responsive:** Right rail moves, sidebar becomes hamburger.
- **Booking:** Confetti, success modal, correct ID (`SP00042`).
- **Accessibility:** Reduced motion and focus rings implemented.

---

## Commit

```bash
git add .
git commit -m "Phase 8: dashboard shell — sidebar, routes, shared booking context, theme"
git push
```
