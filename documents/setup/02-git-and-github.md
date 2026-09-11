# SeatPulse — Git & GitHub Setup Steps

Steps to follow **after** [Docker setup](01-docker-setup.md). Now that Docker is configured, add the code to Git and push it to GitHub.

**Order:** `.gitignore` → `.dockerignore` → `README.md` → `git init` → GitHub repo → `git push`

> ⚠️ It is **essential** to create `.gitignore` first. If you run `git add .` before this, thousands of `node_modules` files will be staged, and removing them from history later is difficult.

---

## Step 1 — Create `.gitignore` (in the root folder)

This file tells Git which folders/files should **not** be uploaded to GitHub.

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

### Folders that will NOT go to GitHub

| Folder / File | Reason for exclusion |
|---|---|
| `node_modules/` | Thousands of files, ~200MB+. Unnecessary — can be regenerated via `npm install` using `package.json` |
| `__pycache__/` | Python compiled cache files. Auto-generated and machine-specific |
| `venv/` `.venv/` | Python virtual environment. Machine-specific; will not work on other PCs |
| `dist/` `build/` | Build output. Can be regenerated from source code |
| **`.env`** | **CRITICAL** — contains database passwords and API keys. Pushing this makes them public |
| `*.db` `*.sqlite3` | Local database files containing test data |
| `.vscode/` `.idea/` | Editor settings. Not required by other developers |
| `.DS_Store` `Thumbs.db` | OS junk files |
| `documents/` | Personal setup notes and references. Not needed in a public repository |

> `!.env.example` means: the `.env.example` file **will be included**. It contains dummy values to inform new developers which variables are required.

---

## Step 2 — Create `.dockerignore` (in both folders)

This file tells Docker which files to exclude during `COPY . .`.

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

> ⚠️ **`node_modules/` is the most important line here.** Without it, your **Windows `node_modules` are copied into the Linux container**. These binaries are incompatible with Linux, and the build process becomes extremely slow. The container generates its own `node_modules` via `RUN npm install`.

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

### `.gitignore` vs `.dockerignore` — What is the difference?

| | `.gitignore` | `.dockerignore` |
|---|---|---|
| Target | Git | Docker |
| Purpose | Prevents files from being pushed to GitHub | Prevents files from being copied into the container |
| Quantity | 1 in root | 1 per Dockerfile folder (2 here) |

Both share `node_modules` and `.env`, but serve different purposes. Both are required.

---

## Step 3 — Return to root folder and create `README.md`

```bash
cd ..
```

This is the **face** of your project on GitHub. Recruiters look at this first.

**README content**

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

> The full README is already created — see [../README.md](../../README.md). It includes the Quick Start, folder structure, and roadmap.

---

## Step 4 — Initialize Git repository

```bash
git init -b main
```

| Part | Meaning |
|---|---|
| `git init` | Initialize this folder as a Git repo (creates a hidden `.git` folder) |
| `-b main` | Set the initial branch name to `main` (replaces the legacy `master` default) |

---

## Step 5 — Stage files and VERIFY

```bash
git add .
git status
```

⚠️ **Stop and check.** The `git status` output must **not** show:

- ❌ `node_modules/`
- ❌ `__pycache__/`
- ❌ `.env`
- ❌ `venv/`

If they appear, your `.gitignore` is either in the wrong place or incorrectly configured. Fix it, then:

```bash
git rm -r --cached .
git add .
git status
```

Proceed only if everything looks correct.

---

## Step 6 — First commit

```bash
git commit -m "Initial commit: Dockerized FastAPI + React skeleton"
```

If using Git for the first time, configure your identity:

```bash
git config --global user.name "Your Name"
git config --global user.email "your@email.com"
```

---

## Step 7 — Create repository on GitHub

Go to [github.com/new](https://github.com/new):

| Field | Value |
|---|---|
| **Repository name** | `seatpulse-event-engine` |
| **Description** | `High-concurrency event ticketing & real-time seat locking engine built with FastAPI, WebSockets, Redis, PostgreSQL, and React.` |
| **Public / Private** | Public (for portfolio) |
| **Add README** | ❌ **Do not check** |
| **Add .gitignore** | ❌ **Do not check** |
| **Add license** | ❌ Do not check (can be added later) |

> ⚠️ **Do not check** these three options. We already have these files; if GitHub creates them, it will cause conflicts and reject your push.

---

## Step 8 — Add remote and push

```bash
git remote add origin https://github.com/Nitishjha7/seatpulse-event-engine.git
git push -u origin main
```

| Part | Meaning |
|---|---|
| `remote add origin <url>` | Save the GitHub address with the short name `origin` |
| `push` | Send local commits to GitHub |
| `-u origin main` | Link local `main` to GitHub's `main`. **Required only once** — subsequent pushes only need `git push` |

**Check remote:**
```bash
git remote -v
```

**Wrong URL?**
```bash
git remote set-url origin <correct-url>
```

---

## Step 9 — Add Topics to GitHub

On the repo page → click the ⚙️ icon next to **About** → add **Topics**:

```
fastapi  react  redis  websockets  concurrency  postgresql  fullstack  python
```

This improves repository discoverability and highlights your tech stack to recruiters.

---

## Step 10 — Rebuild Docker

Since `.dockerignore` was added, the old image may still contain `node_modules`. Clean it up:

```bash
docker compose down
docker compose up --build
```

The build should be **significantly faster** now.

---

## ✅ Checklist

- [ ] `.gitignore` created in root
- [ ] `frontend/.dockerignore` created (`node_modules/` at the top)
- [ ] `backend/.dockerignore` created
- [ ] `README.md` created and username updated
- [ ] `git init -b main`
- [ ] `git status` does not show `node_modules` / `.env`
- [ ] First commit completed
- [ ] GitHub repo created (without README/gitignore)
- [ ] `git push -u origin main` completed
- [ ] Topics added
- [ ] `docker compose up --build` re-run

---

## Daily Git commands

```bash
git status                    # check changes
git add .                     # stage all changes
git add backend/main.py       # stage specific file
git commit -m "message"       # commit
git push                      # push to GitHub
git log --oneline             # commit history
git diff                      # line-by-line changes
```

## Common Problems

| Problem | Fix |
|---|---|
| `node_modules` appears in `git status` | Ensure `.gitignore` is in root. Then run `git rm -r --cached .` → `git add .` |
| `remote origin already exists` | `git remote set-url origin <url>` |
| `failed to push some refs` / rejected | README was created on GitHub. Run `git pull --rebase origin main` then `git push` |
| `src refspec main does not match any` | No commit exists. Run `git commit -m "..."` first |
| `.env` pushed by mistake | Change passwords/keys **immediately**, then `git rm --cached .env` → commit → push |
| Push asks for password | GitHub passwords are deprecated — go to Settings → Developer settings → **Personal Access Token** |
