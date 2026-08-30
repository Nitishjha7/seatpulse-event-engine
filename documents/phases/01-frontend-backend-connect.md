# Phase 1 — Frontend ↔ Backend Connect

[Git & GitHub setup](../setup/02-git-and-github.md) ke baad ka kaam. Ab tak backend aur frontend alag-alag chal rahe the — is phase me dono aapas me baat karenge.

**Kya banega:** browser me ek card jo live batayega backend online hai ya nahi.

**Isme 4 cheezein hain:**
1. Backend me `.env` support (pydantic-settings)
2. Frontend me Tailwind CSS
3. API client (`api.js`)
4. Health check UI (`App.jsx`)

---

## Step 1 — Backend: `pydantic-settings` add karo

`backend/requirements.txt` me ek line add:

```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
pydantic-settings>=2.2.0
```

**Kyu:** abhi CORS origins `main.py` me hardcoded hain. Production me deploy karoge to code badalna padega. `pydantic-settings` environment variables se values padhta hai aur **types validate** karta hai — galat value di to app start hote hi error dega, baad me kahin random jagah crash nahi hoga.

---

## Step 2 — Backend: `config.py` banao

Nayi file `backend/config.py`:

```python
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    # .env file se padho. Environment variable ki priority .env se zyada hoti hai.
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    APP_NAME: str = "SeatPulse API"
    DEBUG: bool = True

    # Comma se alag karke .env me likhenge
    CORS_ORIGINS: str = "http://localhost:5173,http://127.0.0.1:5173"

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.CORS_ORIGINS.split(",") if o.strip()]


settings = Settings()
```

| Cheez | Kyu |
|---|---|
| `BaseSettings` | Env variables apne aap padh leta hai, koi `os.getenv()` nahi likhna padta |
| `= "SeatPulse API"` (default) | `.env` na ho to bhi app chalega. Naye developer ko setup me atkna nahi padta |
| `extra="ignore"` | `.env` me extra variables ho to error mat do (Phase 2 me DB ke variables aayenge) |
| `cors_origins_list` | `.env` me list nahi likh sakte, isliye comma-string ko list me todte hain |
| `settings = Settings()` | Ek hi instance, poore app me wahi import hoga |

> Phase 2 me `DATABASE_URL` aur Phase 4 me `REDIS_URL` bhi yahin aayenge. Ye pattern abhi set kar rahe hain.

---

## Step 3 — Backend: `.env` aur `.env.example` banao

**Do file kyu?**

| File | Git me jayegi? | Kaam |
|---|---|---|
| `.env` | ❌ Nahi | Asli values. Aage isme DB password aayega |
| `.env.example` | ✅ Haan | Template — naye developer ko pata chale kaunse variables chahiye |

`backend/.env.example`:
```
APP_NAME=SeatPulse API
DEBUG=True
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173
```

Phir usse copy karke `.env` banao:

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

> `.gitignore` me `.env.*` hai par `!.env.example` bhi hai — isliye example wali file Git me jayegi, asli `.env` nahi.

---

## Step 4 — Backend: `main.py` update karo

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
    allow_origins=settings.cors_origins_list,   # ab hardcoded nahi
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

**Kya badla:**

| Pehle | Ab | Kyu |
|---|---|---|
| `allow_origins=["*"]` | `settings.cors_origins_list` | `["*"]` + `allow_credentials=True` production me browser reject karta hai. Ab specific origins hain |
| `FastAPI()` | `title`, `description`, `version` ke saath | `/docs` page professional dikhta hai |
| health sirf `{"status": "healthy"}` | service, version, time bhi | Frontend ko dikhane ke liye kuch asli data chahiye — sirf "healthy" se pata nahi chalta ki naya response hai ya cached |

---

## Step 5 — Frontend: Tailwind CSS add karo

`frontend/package.json` ke `devDependencies` me do line:

```json
"@tailwindcss/vite": "^4.1.0",
"tailwindcss": "^4.1.0",
```

> **Tailwind v4** me `tailwind.config.js` ya `postcss.config.js` **nahi banana padta**. Purane tutorials me `npx tailwindcss init -p` likha hota hai — v4 me wo zaroorat nahi.

---

## Step 6 — Frontend: `vite.config.js` update karo

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

| Line | Kyu |
|---|---|
| `tailwindcss()` plugin | Tailwind v4 ka setup bas itna hi hai |
| `host: true` | Container ke bahar se access. Dockerfile me `--host` hai, ye local pe bhi same behaviour deta hai |
| `usePolling: true` | **Docker + Windows pe hot reload ke liye zaroori.** Volume mount pe file-change events reliably nahi aate — polling se Vite khud check karta rehta hai |

> `usePolling` thoda CPU khata hai. Sirf development me use karna, production build me iska koi role nahi.

---

## Step 7 — Frontend: `src/index.css` replace karo

```css
@import "tailwindcss";

body {
  margin: 0;
  min-height: 100vh;
}
```

> Tailwind v3 me teen line lagti thi (`@tailwind base;` etc.). **v4 me sirf ye ek `@import`.**

---

## Step 8 — Frontend: `src/api.js` banao

```js
const API_URL = import.meta.env.VITE_API_URL || "http://localhost:8000";

export async function getHealth() {
  const res = await fetch(`${API_URL}/api/health`);
  if (!res.ok) {
    throw new Error(`Backend ne ${res.status} return kiya`);
  }
  return res.json();
}

export { API_URL };
```

**Alag file kyu:** URL har component me likhoge to deploy ke waqt 20 jagah badalna padega. Yahan ek jagah hai.

**`VITE_` prefix zaroori hai** — Vite sirf `VITE_` se shuru hone wale variables hi frontend code tak pahunchata hai. Ye jaan-boojh ke hai, taaki galti se koi secret browser me na chala jaye. `DATABASE_PASSWORD` naam ka variable kabhi frontend me nahi aayega.

> ⚠️ Frontend ka code **browser me** chalta hai, container me nahi. Isliye `VITE_API_URL` me `http://backend:8000` **nahi** chalega — wo naam sirf container-to-container network me kaam karta hai. Browser ke liye `http://localhost:8000` hi sahi hai.

---

## Step 9 — Frontend: `.env` aur `.env.example`

`frontend/.env.example`:
```
VITE_API_URL=http://localhost:8000
```

Copy karke `.env` banao:

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

## Step 10 — Frontend: `src/App.jsx` likho

Health check UI. Poora code project me hai — [../frontend/src/App.jsx](../../frontend/src/App.jsx)

**Logic ka core:**

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

useEffect(() => { checkBackend() }, [])   // page load pe ek baar
```

| Cheez | Kyu |
|---|---|
| Teen states (`checking/online/offline`) | Sirf true/false rakhte to page load pe pehle second "offline" flash hota. Ab "Checking…" dikhta hai |
| `try/catch` | Backend band ho to `fetch` throw karta hai — crash hone ke bajaye handle karo |
| `useEffect(..., [])` | Khali array = sirf ek baar chalo, mount pe. Array na do to har render pe chalega = infinite loop |
| Recheck button | Backend restart karke turant test kar sako, page refresh kiye bina |

---

## Step 11 — `App.css` delete karo

```bash
rm frontend/src/App.css
```

Vite ki default styling ab kaam ki nahi — Tailwind sab sambhal raha hai.

---

## Step 12 — Rebuild karo

`package.json` badla hai, to **rebuild zaroori hai**. Sirf restart se naya package install nahi hoga.

```bash
docker compose down -v
docker compose up --build
```

> `--build` isliye: naye packages (`tailwindcss`, `pydantic-settings`) tabhi install honge jab image dubara bane.

### ⚠️ `-v` yahan kyu zaroori hai (Phase 1 ka sabse bada trap)

Compose me `- /app/node_modules` ek **anonymous volume** hai. Wo ek baar ban jaye to `docker compose down` use delete **nahi** karta.

Nateeja: image me Tailwind install ho chuka hoga, par container purana volume mount kar lega jisme Tailwind hai hi nahi — aur ye error aayega:

```
Cannot find package '@tailwindcss/vite' imported from /app/node_modules/...
react_frontend exited with code 1
```

Build successful dikhta hai, phir bhi package "nahi milta" — kyunki naye image ka `node_modules` **purane volume ke neeche dab gaya**.

`-v` wo volume delete kar deta hai, phir container fresh `node_modules` use karta hai.

> **Jab bhi `package.json` me kuch add/remove karo, `down -v` karna hai — sirf `down` kaafi nahi.**

> ⚠️ **Phase 2 ke baad ye `-v` wala tarika mat use karna** — tab tak PostgreSQL aa chuka hoga aur `-v` uska saara data delete kar dega.
>
> **Phase 2 ke baad iski jagah ye:**
> ```bash
> docker compose up -d --build --force-recreate --renew-anon-volumes frontend
> ```
> Ye sirf **anonymous** volumes (`node_modules`) naye banata hai. `postgres_data` ek **named** volume hai, wo bacha rehta hai.
>
> Detail: [Phase 2 — Postgres + Models](02-postgres-models.md) → "`down -v` ke baad DB wapas kaise laayein"

---

## ✅ Proof — chala ya nahi?

| Check | Expected |
|---|---|
| http://localhost:5173 | Dark card, **"Backend — Online"** hara dot ke saath |
| Card me | Service name, version, server time |
| http://localhost:8000/docs | Title **"SeatPulse API"** dikhe (default "FastAPI" nahi) |
| **Asli test** ↓ | |

**Asli test:**
```bash
docker compose stop backend
```
Browser me **Recheck** dabao → **"Offline"** laal dot ke saath aana chahiye.

```bash
docker compose start backend
```
Phir **Recheck** → wapas **Online**.

Ye dono taraf kaam kare, tab Phase 1 done hai.

---

## Common Problems

| Problem | Fix |
|---|---|
| Page bilkul unstyled dikh raha (Tailwind nahi laga) | `docker compose up --build` chalaya? Sirf restart kaafi nahi |
| `Cannot find package '@tailwindcss/vite'` / `ERR_MODULE_NOT_FOUND` | Purana `node_modules` volume chipka hua hai — `docker compose down -v` phir `up --build` |
| `react_frontend exited with code 1` | Upar wala hi karan. Logs padho: `docker compose logs frontend` |
| Card me "Offline" par backend chal raha hai | Browser console (F12) kholo. CORS error hai to `backend/.env` me `CORS_ORIGINS` check karo |
| `ModuleNotFoundError: pydantic_settings` | Backend rebuild nahi hua — `docker compose up -d --build backend` |
| Code save karne pe browser update nahi ho raha | `vite.config.js` me `usePolling: true` hai? |
| `.env` change kiya par asar nahi | Container restart karo: `docker compose restart backend` |
| Frontend me `http://backend:8000` daala aur kaam nahi kar raha | Browser container network me nahi hai. `http://localhost:8000` use karo |

---

## Files jo is phase me bane/badle

```
backend/
├── config.py          ← naya
├── main.py            ← update
├── requirements.txt   ← update
├── .env               ← naya (Git me nahi)
└── .env.example       ← naya

frontend/
├── vite.config.js     ← update
├── package.json       ← update
├── .env               ← naya (Git me nahi)
├── .env.example       ← naya
└── src/
    ├── api.js         ← naya
    ├── App.jsx        ← update
    ├── index.css      ← update
    └── App.css        ← delete
```

---

## Commit karo

```bash
git add .
git status
git commit -m "Phase 1: connect frontend to backend with health check + Tailwind"
git push
```

`git status` me `.env` **nahi** dikhna chahiye, `.env.example` dikhna chahiye.

---

**Agla:** [roadmap.md](../roadmap.md) → Phase 2 (PostgreSQL + SQLAlchemy models)
