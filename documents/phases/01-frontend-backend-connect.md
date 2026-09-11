# Phase 1 — Frontend ↔ Backend Connect

Following [Git & GitHub setup](../setup/02-git-and-github.md). Previously, the backend and frontend operated independently; in this phase, they will communicate.

**Goal:** A browser-based card displaying the live status of the backend.

**Components:**
1. Backend `.env` support (pydantic-settings)
2. Frontend Tailwind CSS
3. API client (`api.js`)
4. Health check UI (`App.jsx`)

---

## Step 1 — Backend: Add `pydantic-settings`

Add to `backend/requirements.txt`:

```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
pydantic-settings>=2.2.0
```

**Reason:** CORS origins are currently hardcoded in `main.py`. Deploying to production would require code changes. `pydantic-settings` reads values from environment variables and **validates types**—if a value is incorrect, the app fails at startup rather than crashing randomly later.

---

## Step 2 — Backend: Create `config.py`

New file `backend/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # Read from .env file. Environment variables take priority over .env.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SeatPulse API"
    DEBUG: bool = True

    # Comma-separated values in .env
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
```

| Feature | Reason |
|---|---|
| `BaseSettings` | Automatically reads env variables; no `os.getenv()` required. |
| `= "SeatPulse API"` (default) | App runs without a `.env` file; prevents setup blockers for new developers. |
| `extra="ignore"` | Ignores extra variables in `.env` (Phase 2 will add DB variables). |
| `cors_origins_list` | Converts comma-separated strings from `.env` into a list. |
| `settings = Settings()` | Single instance imported throughout the app. |

> `DATABASE_URL` (Phase 2) and `REDIS_URL` (Phase 4) will be added here.

---

## Step 3 — Backend: Create `.env` and `.env.example`

**Why two files?**

| File | Git tracked? | Purpose |
|---|---|---|
| `.env` | ❌ No | Actual values (e.g., DB passwords). |
| `.env.example` | ✅ Yes | Template for new developers. |

`backend/.env.example`:
```
APP_NAME=SeatPulse API
DEBUG=True
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Copy to create `.env`:

**PowerShell**
```powershell
cd backend
Copy-Item .env.example .env
```

**Git Bash**
```bash
cd backend
cp .env.example .env
```

> `.gitignore` includes `.env.*` but excludes `!.env.example`, ensuring the template is tracked while the actual `.env` remains private.

---

## Step 4 — Backend: Update `main.py`

```python
from datetime import datetime, timezone

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from config import settings

app = FastAPI(
    title=settings.APP_NAME,
    description="High-concurrency event ticketing engine",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,   # No longer hardcoded
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/")
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}


@app.get("/api/health")
def health_check():
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "version": "0.1.0",
        "time": datetime.now(timezone.utc).isoformat(),
    }
```

**Changes:**

| Previous | Current | Reason |
|---|---|---|
| `allow_origins=["*"]` | `settings.cors_origins_list` | Browsers reject `["*"]` with `allow_credentials=True`. |
| `FastAPI()` | With `title`, `description`, `version` | Professional `/docs` page. |
| Health: `{"status": "healthy"}` | Includes service, version, time | Provides verifiable data for the frontend. |

---

## Step 5 — Frontend: Add Tailwind CSS

Add to `frontend/package.json` under `devDependencies`:

```json
"@tailwindcss/vite": "^4.1.0",
"tailwindcss": "^4.1.0",
```

> **Tailwind v4** does not require `tailwind.config.js` or `postcss.config.js`.

---

## Step 6 — Frontend: Update `vite.config.js`

```js
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import tailwindcss from '@tailwindcss/vite'

export default defineConfig({
  plugins: [react(), tailwindcss()],

  server: {
    host: true,
    port: 5173,
    watch: {
      usePolling: true,
    },
  },
})
```

| Line | Reason |
|---|---|
| `tailwindcss()` plugin | Standard Tailwind v4 setup. |
| `host: true` | Allows access from outside the container. |
| `usePolling: true` | **Required for hot reload on Docker + Windows.** |

> `usePolling` consumes more CPU; use only in development.

---

## Step 7 — Frontend: Replace `src/index.css`

```css
@import "tailwindcss";

body {
  margin: 0;
  min-height: 100vh;
}
```

> Tailwind v4 uses a single `@import` instead of multiple directives.

---

## Step 8 — Frontend: Create `src/api.js`

```js
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function getHealth() {
  const res = await fetch(`${API_URL}/api/health`);
  if (!res.ok) {
    throw new Error(`Backend returned ${res.status}`);
  }
  return res.json();
}

export { API_URL };
```

**Why a separate file:** Centralizes the URL for easier deployment updates.

**`VITE_` prefix:** Vite only exposes variables prefixed with `VITE_` to the frontend to prevent accidental exposure of sensitive secrets.

> ⚠️ Frontend code runs in the **browser**, not the container. `VITE_API_URL` must be `http://localhost:8000`, not `http://backend:8000`.

---

## Step 9 — Frontend: `.env` and `.env.example`

`frontend/.env.example`:
```
VITE_API_URL=http://localhost:8000
```

Copy to create `.env`:

**PowerShell**
```powershell
cd frontend
Copy-Item .env.example .env
```

**Git Bash**
```bash
cd frontend
cp .env.example .env
```

---

## Step 10 — Frontend: Write `src/App.jsx`

Health check UI. Full code: [../frontend/src/App.jsx](../../frontend/src/App.jsx)

**Core Logic:**

```jsx
const [status, setStatus] = useState('checking')   // checking | online | offline
const [data, setData] = useState(null)
const [error, setError] = useState(null)

async function checkBackend() {
  setStatus('checking')
  try {
    const json = await getHealth()
    setData(json)
    setStatus('online')
  } catch (err) {
    setError(err.message)
    setStatus('offline')
  }
}

useEffect(() => { checkBackend() }, [])   // Run once on mount
```

| Feature | Reason |
|---|---|
| Three states | Prevents "offline" flash during initial load. |
| `try/catch` | Handles `fetch` errors gracefully. |
| `useEffect(..., [])` | Ensures execution only on mount. |

---

## Step 11 — Delete `App.css`

```bash
rm frontend/src/App.css
```

Tailwind handles all styling now.

---

## Step 12 — Rebuild

Rebuild is required after modifying `package.json`.

```bash
docker compose down -v
docker compose up --build
```

### ⚠️ Why `-v` is critical
Compose creates an **anonymous volume** for `/app/node_modules`. `docker compose down` does not delete it. Without `-v`, the container will mount the old volume, causing "package not found" errors.

> **Always use `down -v` when adding/removing packages.**

> ⚠️ **After Phase 2**, do not use `down -v` as it will delete your PostgreSQL data. Use:
> ```bash
> docker compose up -d --build --force-recreate --renew-anon-volumes frontend
> ```

---

## ✅ Proof

| Check | Expected |
|---|---|
| http://localhost:5173 | Dark card, **"Backend — Online"** with green dot. |
| Card content | Service name, version, server time. |
| http://localhost:8000/docs | Title **"SeatPulse API"**. |

**Test:**
```bash
docker compose stop backend
```
Click **Recheck** in browser → **"Offline"** with red dot.

```bash
docker compose start backend
```
Click **Recheck** → **"Online"**.

---

## Common Problems

| Problem | Fix |
|---|---|
| Unstyled page | Run `docker compose up --build`. |
| `Cannot find package` | Old `node_modules` volume; run `docker compose down -v`. |
| "Offline" despite backend running | Check browser console (F12) for CORS errors; verify `CORS_ORIGINS` in `backend/.env`. |
| `ModuleNotFoundError: pydantic_settings` | Backend not rebuilt; run `docker compose up -d --build backend`. |
| No browser update on save | Ensure `usePolling: true` in `vite.config.js`. |

---

## Files Modified/Created

```
backend/
├── config.py          ← New
├── main.py            ← Update
├── requirements.txt   ← Update
├── .env               ← New (Not in Git)
└── .env.example       ← New

frontend/
├── vite.config.js     ← Update
├── package.json       ← Update
├── .env               ← New (Not in Git)
├── .env.example       ← New
└── src/
    ├── api.js         ← New
    ├── App.jsx        ← Update
    ├── index.css      ← Update
    └── App.css        ← Delete
```

---

## Commit

```bash
git add .
git status
git commit -m "Phase 1: connect frontend to backend with health check + Tailwind"
git push
```

---

**Next:** [roadmap.md](../roadmap.md) → Phase 2 (PostgreSQL + SQLAlchemy models)
