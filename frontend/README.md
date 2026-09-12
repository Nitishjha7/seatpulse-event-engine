# SeatPulse — Frontend

React 19 + Vite + Tailwind v4 single-page app. Talks to the FastAPI backend
over REST and holds a live WebSocket per event for seat-state updates.

```
src/
  api.js                fetch wrapper — attaches the access token, refreshes on 401
  auth/AuthContext.jsx  the access token lives in memory, never localStorage
                        (XSS cannot read it)
  booking/              seat-hold state shared across the booking flow
  hooks/useWebSocket.js live seat updates, with reconnect
  pages/                route components
  components/           seat grid, seat search, layout builder, modals
  layout/               app shell, sidebar, topbar
```

## Run

```bash
npm install
npm run dev        # http://localhost:5173
```

`VITE_API_URL` points at the backend (see `.env.example`). Vite inlines it at
**build time**, so the production image takes it as a build arg — changing the
environment after the container starts has no effect.

## Commands

| Command | Purpose |
|---|---|
| `npm run dev` | Dev server with HMR |
| `npm run build` | Production bundle into `dist/` |
| `npm run lint` | Oxlint |
| `npm run preview` | Serve the built bundle locally |

Docker usage and the full architecture are covered in the [root README](../README.md).
