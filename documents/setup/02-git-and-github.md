# SeatPulse — Git & GitHub Setup Steps

[Docker setup](01-docker-setup.md) ke **baad** ke steps. Docker setup ho chuka hai, ab code ko Git me daalna hai aur GitHub par push karna hai.

**Order:** `.gitignore` → `.dockerignore` → `README.md` → `git init` → GitHub repo → `git push`

> ⚠️ `.gitignore` **sabse pehle** banana zaroori hai. Agar `git add .` pehle chala diya, to `node_modules` ki hazaaron files stage ho jaayengi aur baad me history se hatana dard hai.

---

## Step 1 — `.gitignore` banao (root folder me)

Ye file Git ko batati hai ki **kaunse folders/files GitHub par nahi jaane**.

**PowerShell**
```powershell
@"
# ---------- Python / Backend ----------
__pycache__/
*.py[cod]
venv/
.venv/
.pytest_cache/
*.db
*.sqlite3

# ---------- Node / Frontend ----------
node_modules/
dist/
build/
.vite/
npm-debug.log*

# ---------- Environment / Secrets ----------
.env
.env.*
!.env.example
*.pem
*.key

# ---------- Personal Notes ----------
documents/

# ---------- Editors / OS ----------
.vscode/
.idea/
.DS_Store
Thumbs.db

# ---------- Logs ----------
logs/
*.log
"@ | Out-File -Encoding utf8 .gitignore
```

**Git Bash**
```bash
cat << 'EOF' > .gitignore
__pycache__/
*.py[cod]
venv/
.venv/
node_modules/
dist/
build/
.env
.env.*
!.env.example
.vscode/
.idea/
.DS_Store
Thumbs.db
*.log
EOF
```

### Ye folders GitHub pe NAHI jaayenge

| Folder / File | Kyu nahi jaana chahiye |
|---|---|
| `node_modules/` | Hazaaron files, ~200MB+. Iski zaroorat hi nahi — `package.json` se koi bhi `npm install` karke dubara bana sakta hai |
| `__pycache__/` | Python ki compiled cache files. Auto-generate hoti hain, har machine pe alag |
| `venv/` `.venv/` | Python virtual environment. Machine-specific hai, doosre PC pe kaam hi nahi karega |
| `dist/` `build/` | Build ka output. Source code se dubara ban jata hai |
| **`.env`** | **SABSE ZAROORI** — isme database password, API keys hote hain. Ek baar push ho gaya to public ho gaya |
| `*.db` `*.sqlite3` | Local database file. Isme test data hota hai, kisi kaam ka nahi |
| `.vscode/` `.idea/` | Tumhare editor ki settings. Doosre developer ko iski zaroorat nahi |
| `.DS_Store` `Thumbs.db` | OS ki junk files |
| `documents/` | Tumhare personal setup notes aur command references. Sirf local reference ke liye, repo public me inki zaroorat nahi |

> `!.env.example` ka matlab: `.env.example` file **jayegi**. Usme dummy values rakhte hain taaki naye developer ko pata chale kaunse variables chahiye.

---

## Step 2 — `.dockerignore` banao (dono folders me)

Ye file Docker ko batati hai ki `COPY . .` ke waqt **kya container me copy nahi karna**.

### 2a. `frontend/.dockerignore`

```bash
cd frontend
```

**PowerShell**
```powershell
@"
node_modules/
dist/
build/
.vite/
.env
.env.*
.git
.gitignore
Dockerfile
.dockerignore
*.md
npm-debug.log*
.vscode/
.DS_Store
"@ | Out-File -Encoding utf8 .dockerignore
```

> ⚠️ **`node_modules/` yahan sabse important line hai.** Bina iske tumhara **Windows ka `node_modules` Linux container me copy ho jata hai**. Wo binaries Linux pe chalti hi nahi, aur build bahut slow ho jata hai. Container apna `node_modules` khud `RUN npm install` se banata hai.

### 2b. `backend/.dockerignore`

```bash
cd ../backend
```

**PowerShell**
```powershell
@"
__pycache__/
*.py[cod]
venv/
.venv/
.pytest_cache/
.env
.env.*
*.pem
*.key
.git
.gitignore
Dockerfile
.dockerignore
*.md
*.db
*.sqlite3
.vscode/
.DS_Store
"@ | Out-File -Encoding utf8 .dockerignore
```

### `.gitignore` vs `.dockerignore` — farak kya hai?

| | `.gitignore` | `.dockerignore` |
|---|---|---|
| Kise batata hai | Git ko | Docker ko |
| Kya rokta hai | Files GitHub par jaane se | Files container me copy hone se |
| Kitni files | Root me 1 | Har Dockerfile ke folder me 1 (yahan 2) |

Dono me `node_modules` aur `.env` common hain, par kaam alag hai. Dono chahiye.

---

## Step 3 — Root folder me wapas jao aur `README.md` banao

```bash
cd ..
```

Ye GitHub par project ka **face** hai. Interviewer sabse pehle yahi dekhta hai.

**README ka content**

````markdown
# 🎟️ SeatPulse — High-Concurrency Event Booking Engine

SeatPulse is a full-stack event ticketing platform designed to handle high-concurrency flash sales. It prevents overselling using Redis key-locking and streams real-time seat state changes via WebSockets.

## 🚀 Tech Stack
- **Backend:** FastAPI (ASGI), Python 3.11, Pydantic v2
- **Database & Cache:** PostgreSQL, Redis (Distributed Locking)
- **Frontend:** React (Vite), Tailwind CSS, WebSockets
- **DevOps:** Docker, Docker Compose

## ⚡ Quick Start
```bash
git clone https://github.com/Nitishjha7/seatpulse-event-engine.git
cd seatpulse-event-engine
docker compose up --build
```
````

> Poora README already bana hua hai — [../README.md](../../README.md) dekh lo. Usme Quick Start, folder structure aur roadmap bhi hai.

---

## Step 4 — Git repo initialize karo

```bash
git init -b main
```

| Part | Matlab |
|---|---|
| `git init` | Is folder ko Git repo banao (ek chhupa hua `.git` folder banega) |
| `-b main` | Pehli branch ka naam `main` rakho (purana default `master` tha, GitHub ab `main` use karta hai) |

---

## Step 5 — Files stage karo aur CHECK karo

```bash
git add .
git status
```

⚠️ **Yahan ruk ke dekho.** `git status` ke output me ye **nahi** dikhna chahiye:

- ❌ `node_modules/`
- ❌ `__pycache__/`
- ❌ `.env`
- ❌ `venv/`

Dikhe? Matlab `.gitignore` galat jagah hai ya galat likha hai. Fix karke:

```bash
git rm -r --cached .
git add .
git status
```

Sahi lag raha hai to hi aage badho.

---

## Step 6 — Pehla commit

```bash
git commit -m "Initial commit: Dockerized FastAPI + React skeleton"
```

Pehli baar Git use kar rahe ho to naam/email set karna padega:

```bash
git config --global user.name "Tumhara Naam"
git config --global user.email "tumhara@email.com"
```

---

## Step 7 — GitHub par repo banao

[github.com/new](https://github.com/new) pe jao:

| Field | Value |
|---|---|
| **Repository name** | `seatpulse-event-engine` |
| **Description** | `High-concurrency event ticketing & real-time seat locking engine built with FastAPI, WebSockets, Redis, PostgreSQL, and React.` |
| **Public / Private** | Public (portfolio ke liye) |
| **Add README** | ❌ **Nahi** |
| **Add .gitignore** | ❌ **Nahi** |
| **Add license** | ❌ Nahi (baad me add kar sakte ho) |

> ⚠️ Ye teeno **check mat karna**. Apne paas already hain — GitHub bhi bana dega to conflict aayega aur push reject ho jayega.

---

## Step 8 — Remote add karo aur push karo

```bash
git remote add origin https://github.com/Nitishjha7/seatpulse-event-engine.git
git push -u origin main
```

| Part | Matlab |
|---|---|
| `remote add origin <url>` | GitHub ka address save karo, uska short naam `origin` |
| `push` | Local commits GitHub par bhejo |
| `-u origin main` | Local `main` ko GitHub ke `main` se jod do. **Ek hi baar lagta hai** — agli baar sirf `git push` kaafi hai |

**Remote check karna ho:**
```bash
git remote -v
```

**Galat URL daal diya?**
```bash
git remote set-url origin <sahi-url>
```

---

## Step 9 — GitHub par Topics add karo

Repo page → dayin taraf **About** ke paas ⚙️ icon → **Topics** me daalo:

```
fastapi  react  redis  websockets  concurrency  postgresql  fullstack  python
```

Isse repo GitHub search me aata hai aur recruiter ko turant tech stack dikh jata hai.

---

## Step 10 — Docker dubara build karo

`.dockerignore` ab add hui hai, to purani image me abhi bhi `node_modules` pada hai. Ek baar saaf karo:

```bash
docker compose down
docker compose up --build
```

Build pehle se **kaafi fast** hona chahiye.

---

## ✅ Checklist

- [ ] `.gitignore` root me bana
- [ ] `frontend/.dockerignore` bana (`node_modules/` sabse upar)
- [ ] `backend/.dockerignore` bana
- [ ] `README.md` bana, username update kiya
- [ ] `git init -b main`
- [ ] `git status` me `node_modules` / `.env` nahi dikha
- [ ] Pehla commit ho gaya
- [ ] GitHub repo bana (README/gitignore ke bina)
- [ ] `git push -u origin main` ho gaya
- [ ] Topics add kiye
- [ ] `docker compose up --build` dubara chalaya

---

## Aage ke liye — roz ke Git commands

```bash
git status                    # kya-kya badla hai
git add .                     # sab changes stage karo
git add backend/main.py       # sirf ek file
git commit -m "message"       # commit
git push                      # GitHub par bhejo (-u ek baar lag chuka hai)
git log --oneline             # commit history
git diff                      # kya badla, line by line
```

## Common Problems

| Problem | Fix |
|---|---|
| `git status` me `node_modules` dikh raha | `.gitignore` root me hai? Phir `git rm -r --cached .` → `git add .` |
| `remote origin already exists` | `git remote set-url origin <url>` |
| `failed to push some refs` / rejected | GitHub pe README bana diya tha. `git pull --rebase origin main` phir `git push` |
| `src refspec main does not match any` | Abhi commit nahi hua. Pehle `git commit -m "..."` karo |
| `.env` galti se push ho gaya | Turant password/keys **badlo**, phir `git rm --cached .env` → commit → push |
| Push pe password maang raha | GitHub password nahi chalta — Settings → Developer settings → **Personal Access Token** banao |
