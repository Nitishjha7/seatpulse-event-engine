# Phase 8 — Dashboard UI Shell

[Phase 7 — Auth + Google OAuth](07-auth-google-oauth.md) ke baad ka kaam.

**Kya badla:** ek single-page app se **multi-page dashboard** — sidebar, topbar, routes, aur proper theme.

> Phase 1-7 me UI kaam chalau tha — ek page, sab kuch usi me. Ab jab aage organizer portal, reports, settings aane hain, to **shell pehle** banana zaroori tha. Baad me banate to har page ka layout dobara likhna padta.

---

## Design ka usool: sirf sach dikhana

Mockup me kuch tiles the jo dekhne me achhe the par **jhoothe** hote:

| Mockup me tha | Kyu nahi rakha | Uski jagah kya |
|---|---|---|
| "Multiple Payments — UPI, Cards, Wallets" | Payments **hain hi nahi** | "Redis Seat Locking — atomic SET NX EX" |
| "4.8 ★ (12.5K reviews)" | Koi review system nahi hai | Event ka asli seat count |
| "Loved by 50K+ Users" | 500 seeded test users hain | "Zero Overselling — 200 users, 1 booking" |
| "24/7 Customer Support" | Koi support nahi hai | "Verified by Load Tests — 8,154 requests, 0 failures" |
| "50,000+ Seats" | 100 seats hain | `seats.length` se asli count |

> ⚠️ **Portfolio project me jhoothe stats sabse bada red flag hain.** Interviewer `/docs` khol ke ya grid gin ke pakad lega, aur phir wo tumhare *asli* claims pe bhi shak karega.
>
> Aur imaandari se — "200 concurrent users, exactly 1 booking" **"Loved by 50K+ users" se zyada impressive hai.** Ek maapa hua number hai, dusra sticker.

---

## Step 1 — Router add karo

```json
"react-router-dom": "^7.1.0"
```

**Kyu router, sirf state se nav kyu nahi:**
- URL shareable hota hai — `/bookings` seedha bhej sakte ho
- Browser ka back button kaam karta hai
- Har page apna component — ek 400-line ka `App.jsx` nahi banta

---

## Step 2 — ⭐ BookingContext — sabse important decision

Pehle saara state `App.jsx` me tha. Ab multiple pages ko wahi data chahiye.

**Naive tarika (galat):** har page apna data fetch kare.
**Problem:** har page apna **WebSocket bhi kholega**. 4 pages = 4 connections per user, aur har update 4 baar aayega.

**Sahi tarika:** ek `BookingProvider` jo Routes ke **bahar** baitha ho.

```jsx
<BookingProvider key={user.id}>
  <Routes>
    <Route element={<AppShell />}>
      <Route index element={<Dashboard />} />
      ...
```

Isse:
- **WebSocket ek hi rehta hai** — page badalne par reconnect nahi hota
- Seat state, hold, countdown — sab pages pe same
- Dashboard se `/bookings` jao aur wapas aao, hold ka countdown **chalta rehta hai**

> `key={user.id}` — user badalne par poora provider naya banta hai. Bina iske pichhle user ki bookings nayi login me dikh jaati.

**Provider me kya hai:** event, seats, bookings, counts, selectedSeat, lockSecondsLeft, WebSocket, aur saare actions (`selectSeat`, `releaseHold`, `confirmBooking`, `cancel`).

---

## Step 3 — Layout

```
src/layout/
├── AppShell.jsx     sidebar + topbar + <Outlet />
├── Sidebar.jsx      nav + promo card
├── Topbar.jsx       health pills + user menu
└── icons.jsx        inline SVG icons
```

### `<Outlet />` kya karta hai

React Router current page yahan render karta hai. Faayda: **sidebar aur topbar re-mount nahi hote** page badalne par — sirf content badalta hai.

### Icons — library kyu nahi lagayi

```js
// icons.jsx — 15 inline SVGs, ~100 lines
```

Hume 15 icons chahiye the. Lucide/Heroicons poora package add karta, bundle badhata, aur ek aur dependency deta. Sab `currentColor` follow karte hain to Tailwind ki text color class se rang badal jata hai.

### Sidebar me "Coming soon"

```jsx
const NAV  = [Dashboard, Events, My Bookings, Profile]   // banaye hue
const SOON = [Reports, Settings]                          // disabled, greyed
```

Jo page nahi bana wo **disabled dikhta hai, link nahi hai**. Shell ready dikhta hai par koi tootа hua link nahi milta.

### Topbar

- **Health pills** — DB, Redis, Live (WebSocket). `sm` se neeche sirf dots, text chhup jata hai
- **User menu** — avatar + dropdown. Bahar click karne par band (`mousedown` listener)
- **Hamburger** — sirf mobile pe, sidebar slide karta hai

---

## Step 4 — Theme

`index.css` me CSS variables:

```css
:root {
  --bg: #08080c;        /* page — purple-black */
  --panel: #101018;     /* cards */
  --panel-2: #16161f;   /* card ke andar wale blocks */
  --border: #23232f;
  --accent: #7c3aed;    /* violet */
}
```

Tailwind me `bg-[var(--panel)]` se use hote hain. Color badalna ho to **ek jagah** badlo.

Aur do chhoti cheezein jo bada farak karti hain:

```css
background-image:
  radial-gradient(900px 400px at 15% -10%, rgba(124,58,237,0.10), transparent),
  radial-gradient(700px 350px at 95% 0%, rgba(37,99,235,0.07), transparent);
```

Flat black ke bajaye halka purple glow — screen zinda lagti hai.

Aur custom scrollbar — default wala dark theme me bhadda dikhta hai.

---

## Step 5 — Hero banner

Mockup me concert ki photo thi. Maine **koi image use nahi ki**:

- External image CSP me block ho sakti hai, offline me tooti dikhti hai
- Repo bhaari hota hai
- Licensing ka jhamela

Iski jagah **CSS gradient + inline SVG**:
- `<polygon>` se stage light beams
- `radial-gradient` repeat karke crowd silhouette
- Sab self-contained, zero network requests

```jsx
<polygon points="60,0 130,0 230,260 0,260" fill="url(#beam)" />
```

---

## Step 6 — Pages

| Page | Route | Kya |
|---|---|---|
| Dashboard | `/` | Hero + seat grid + right rail (summary, hold, bookings) + feature strip |
| Events | `/events` | Event cards, live counts |
| My Bookings | `/bookings` | Stats (confirmed/cancelled/spent) + full list |
| Profile | `/profile` | Account, session info, system health |
| API Docs | external | Backend ke `/docs` pe |

`*` route `/` pe redirect karta hai — galat URL pe blank page nahi.

### Responsive

```jsx
<div className="grid gap-5 xl:grid-cols-[1fr_380px]">
```

`xl` se neeche right rail grid ke **neeche** chala jata hai. Sidebar `lg` se neeche slide-over ban jata hai.

Seat grid `overflow-x-auto` me hai — chhoti screen pe scroll hota hai, squeeze nahi hota.

---

## Step 7 — Booking Confirmed modal

Booking hone ke baad sirf ek chhota sa green text dikhta tha. Ab poora success modal.

### Backend se kuch nahi chahiye

Modal ka saara data **already maujood** hai:

| Field | Kahan se |
|---|---|
| Seat No. | `selectedSeat` (hold ke waqt jo capture ki thi) |
| Price | `booking.amount` — DB se |
| Booking ID | `booking.id` — DB se, `SP00042` format me |
| Event | `event` — context se |

```js
// bookingRef(42) -> "SP00042"
export function bookingRef(id) {
  return `SP${String(id).padStart(5, '0')}`
}
```

> Ye **display format** hai, fake ID nahi. Asli integer id hi dikh rahi hai, bas ticket reference jaisa. Same reference `My Bookings` list me bhi dikhta hai — dono jagah match karega.

### Seat capture karna zaroori hai

```js
const created = await createBooking(selectedSeat.id)

// Modal ke liye ABHI capture karo
setLastBooking({ booking: created, seat: selectedSeat, event })

setSelectedSeat(null)
```

Agar baad me lete to problem hoti: `selectedSeat` null ho chuka hota, aur `refresh()` ke baad wo seat `booked` state me hoti.

### Confetti — library nahi

```jsx
const pieces = useMemo(() => Array.from({ length: 40 }, () => ({ ... })), [count])
```

40 chhote divs, har ek ka random direction/rotation/color/delay. CSS variables (`--x`, `--r`) se animation me jaate hain.

| Cheez | Kyu |
|---|---|
| Library nahi (canvas-confetti ~30KB) | 40 divs ke liye poora package faltu hai |
| `useMemo` | Bina iske har render pe naye random numbers bante aur confetti jhatke se jagah badalta |
| `pointer-events-none` | Confetti ke upar click karne pe button dabna chahiye |

### Modal ki basic zaroori cheezein

Ye chaar cheezein har modal me honi chahiye — inke bina modal "kacha" lagta hai:

```js
// 1. Escape se band
document.addEventListener('keydown', (e) => e.key === 'Escape' && onClose())

// 2. Background scroll lock
document.body.style.overflow = 'hidden'

// 3. Backdrop click se band, andar click se nahi
<div onClick={onClose}>
  <div onClick={(e) => e.stopPropagation()}>

// 4. Screen readers ke liye
role="dialog" aria-modal="true" aria-labelledby="..."
```

---

## Step 8 — Event Detail page

Route: `/events/:id`

### Model me do naye column

"About the Event" dikhane ke liye data hi nahi tha, isliye:

```python
description: Mapped[str | None] = mapped_column(Text, nullable=True)
category:    Mapped[str | None] = mapped_column(String(40), nullable=True)
```

`Text` use kiya `String(n)` nahi — description lambi ho sakti hai, uspe arbitrary limit lagane ka koi matlab nahi.

Migration:
```bash
docker compose exec backend alembic revision --autogenerate -m "add event description and category"
docker compose exec backend alembic upgrade head
```

> Purana event pehle se DB me tha, to uske liye ek `UPDATE` chalana pada. Naye setup me `seed.py` khud bhar deta hai.

### Price range — ek hi query me

```python
price_range = db.execute(
    select(func.min(Seat.price), func.max(Seat.price)).where(Seat.event_id == event_id)
).one()
```

Do alag queries (`min` aur `max`) maarne ki zaroorat nahi.

### ⚠️ Fake chips ki jagah asli data

Mockup me chips the: **"Live Performance"**, **"Top Hits"**, **"50K+ Audience"**.

Teeno banawati thi. Unki jagah:

| Chip | Data source |
|---|---|
| `🎵 Music` | `event.category` — DB se |
| `📍 Mumbai` | `event.venue` se city nikal ke |
| `🎫 100 seats` | `event.total_seats` |
| `💰 ₹800 – ₹2500` | `min_price` / `max_price` — asli seats se |
| `👥 99 available now` | Live count, WebSocket se update hota hai |

Aakhri wala sabse achha hai — **wo live badalta hai**. "50K+ Audience" ek sticker hai; "99 available now" ek jeeta jagta number hai.

### Description ke paragraphs

```jsx
{event.description.split('\n\n').map((para, i) => (
  <p key={i}>{para}</p>
))}
```

DB me `\n\n` se paragraphs alag kiye hain. `dangerouslySetInnerHTML` **nahi** use kiya — wo XSS ka darwaza khol deta.

---

## Step 9 — UI smoothness

Chhoti cheezein jo mila ke bada farak karti hain:

```css
@keyframes rise    { from { opacity:0; transform: translateY(8px); } }
@keyframes pop-in  { from { opacity:0; transform: translateY(12px) scale(0.96); } }
@keyframes fade-in { from { opacity:0; } }
```

| Kahan | Kya |
|---|---|
| Har page | `animate-rise` — content thoda upar slide karke aata hai |
| Modal | `pop-in` with `cubic-bezier(0.22, 1, 0.36, 1)` — halka sa bounce |
| Backdrop | `fade-in` |
| Seats | hover pe `-translate-y-0.5` — uthti hui lagti hain |
| List rows | hover pe halka background |
| Selected seat | `ring-offset` — panel se alag "uthi hui" dikhti hai |

### ⚠️ Accessibility — do cheezein jo log bhool jaate hain

```css
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after {
    animation-duration: 0.01ms !important;
    transition-duration: 0.01ms !important;
  }
}
```

Kuch logon ko motion se chakkar aata hai. OS setting me "reduce motion" on hai to saari animations band. **Ye ek block har project me hona chahiye.**

```css
:focus-visible {
  outline: 2px solid var(--accent);
  outline-offset: 2px;
}
```

`:focus` nahi, `:focus-visible` — keyboard users ko ring dikhta hai, mouse users ko nahi.

---

## Step 10 — Components

```
src/components/
├── EventHero.jsx              gradient banner
├── BookingConfirmedModal.jsx  success modal + bookingRef()
├── Confetti.jsx               CSS-only confetti
├── SeatGrid.jsx       10×10 grid (restyled)
├── EventSummary.jsx   counts + details
├── HoldCard.jsx       hold + countdown + confirm/release
├── BookingsList.jsx   dashboard aur bookings page dono me
└── FeatureStrip.jsx   4 tiles (sach wale)
```

`BookingsList` me `compact` prop hai — dashboard pe 3 dikhti hain "View all" ke saath, bookings page pe poori list.

`BookingPanel.jsx` **delete** kar diya — wo teen alag cheezein ek me kar raha tha.

---

## Step 11 — Rebuild

`package.json` badla hai (`react-router-dom`), to:

```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

> ⚠️ `--renew-anon-volumes` zaroori hai, warna purana `node_modules` volume chipka rehta hai aur `react-router-dom` "not found" aayega.
>
> **`down -v` mat karna** — wo Postgres ka data bhi uda dega. Detail: [Phase 2 — Postgres + Models](02-postgres-models.md)

---

## ✅ Proof

| Check | Expected |
|---|---|
| http://localhost:5173 | Sidebar + topbar wala dashboard |
| Topbar | DB · Redis · Live — teeno hare, Live pulse karta hua |
| Sidebar me click | Events, My Bookings, Profile — URL badalta hai |
| Browser back button | Kaam karta hai |
| **Hold karke `/bookings` jao, wapas aao** | **Countdown chalta rehta hai** ⭐ |
| Window chhoti karo | `xl` pe right rail neeche, `lg` pe sidebar hamburger me |
| Seat click | Neeli + countdown shuru |
| Do windows, alag users | Ek me hold → dusre me turant peeli |
| Galat URL (`/xyz`) | Dashboard pe redirect |
| **Confirm Booking** dabao | Confetti ke saath success modal — asli seat, price, `SP00042` ID |
| Modal me Escape / bahar click | Band ho jata hai; background scroll bhi lock tha |
| Modal me "View My Bookings" | `/bookings` khulta hai, **wahi** `SP00042` list me dikhta hai |
| Events → **Details** | Detail page — About, tags, price range, live counts |
| OS me "reduce motion" on karo | Saari animations band |
| Tab key se navigate | Violet focus ring dikhta hai |

**Sabse important test** wo `/bookings` wala hai — wahi prove karta hai ki WebSocket aur state provider me hain, page me nahi.

---

## Common Problems

| Problem | Fix |
|---|---|
| `Failed to resolve import "react-router-dom"` | `--renew-anon-volumes` ke saath rebuild |
| Page badalne pe WebSocket reconnect ho raha | `BookingProvider` `<Routes>` ke bahar hai? |
| Hold countdown page change pe reset | Same wajah — state provider me honi chahiye |
| Sidebar mobile pe band nahi hota | NavLink pe `onClick={onClose}` hai? |
| Avatar image nahi dikhti | Google avatars ko `referrerPolicy="no-referrer"` chahiye |
| User menu bahar click pe khula rehta | `mousedown` listener aur `ref` check karo |

---

## Files

```
frontend/
├── package.json                    ← react-router-dom
└── src/
    ├── index.css                   ← theme tokens, glow, scrollbar
    ├── main.jsx                    ← BrowserRouter
    ├── App.jsx                     ← routes + auth gate
    ├── booking/
    │   └── BookingContext.jsx      ← naya ⭐ shared state + ek WebSocket
    ├── layout/
    │   ├── AppShell.jsx            ← naya
    │   ├── Sidebar.jsx             ← naya
    │   ├── Topbar.jsx              ← naya
    │   └── icons.jsx               ← naya (15 inline SVG)
    ├── pages/
    │   ├── Dashboard.jsx           ← naya
    │   ├── Events.jsx              ← naya
    │   ├── EventDetail.jsx         ← naya
    │   ├── MyBookings.jsx          ← naya
    │   └── Profile.jsx             ← naya
    └── components/
        ├── EventHero.jsx           ← naya
        ├── BookingConfirmedModal.jsx ← naya
        ├── Confetti.jsx            ← naya
        ├── EventSummary.jsx        ← naya
        ├── HoldCard.jsx            ← naya
        ├── BookingsList.jsx        ← naya
        ├── FeatureStrip.jsx        ← naya
        ├── SeatGrid.jsx            ← restyle
        └── BookingPanel.jsx        ← DELETE
```

Backend me sirf itna:

```
backend/
├── models.py                       ← Event.description, Event.category
├── schemas.py                      ← EventOut me dono, EventDetail me price range
├── routers/events.py               ← min/max price ek query me
├── seed.py                         ← description + category
└── alembic/versions/...            ← nayi migration
```

Locking, auth, WebSocket — inme kuch nahi chhua.

---

## Commit

```bash
git add .
git commit -m "Phase 8: dashboard shell — sidebar, routes, shared booking context, theme"
git push
```

---

## Related

- [roadmap.md](../roadmap.md) — frontend kahan-kahan explain hua hai, uska index bhi wahan hai
- [Phase 5 — WebSockets](05-websockets.md) — WebSocket hook
- [testing.md](../reference/testing.md) — demo commands
