# 📓 SeatPulse — Project Log

Is project me jo bhi kaam hoga, sab yahin likha jayega — date wise, kya kiya, kyu kiya.

**Kaise use karna:** har kaam ke baad neeche naya entry add karo (sabse naya sabse neeche). Setup ke detailed commands [steps_to_build.md](steps_to_build.md) me hain, ye file sirf **journal** hai.

---

## 📊 Status

| Phase | Kaam | Status |
|---|---|---|
| 0 | Docker + FastAPI + React skeleton | ✅ Done |
| 0 | Git repo + ignore files | ✅ Done |
| 0 | GitHub par push | ⬜ Pending |
| 1 | Frontend ↔ backend integration | ⬜ Pending |
| 2 | PostgreSQL + models | ⬜ Pending |
| 3 | Redis seat locking | ⬜ Pending |
| 4 | WebSocket real-time updates | ⬜ Pending |
| 5 | JWT auth | ⬜ Pending |
| 6 | Load testing | ⬜ Pending |

---

## 2026-08-09 — Phase 0: Project Setup

### Kya banaya

```
seatpulse-event-engine/
├── backend/          FastAPI + Dockerfile
├── frontend/         Vite React + Dockerfile
└── docker-compose.yml
```

- **Frontend:** Vite React app, Docker ke through banaya (`node:22-alpine` container se) — local machine pe Node install nahi kiya.
- **Backend:** FastAPI + Uvicorn, `python:3.11-slim` base image.
- **Compose:** dono services ek saath, volume mounts se live code reload.

### Decisions aur unki wajah

| Decision | Kyu |
|---|---|
| Sab kuch Docker me, local install nahi | Machine saaf rehti hai; "mere PC pe to chal raha tha" wali problem khatam |
| `python:3.11-slim` (alpine nahi) | Alpine me Python packages source se build hote hain — bahut slow |
| `node:20-alpine` frontend ke liye | Node me alpine theek chalta hai, image chhoti rehti hai |
| `COPY requirements.txt` / `package*.json` pehle, code baad me | Docker layer cache — code change pe install dubara nahi chalta |
| Uvicorn me `--host 0.0.0.0` | Default `127.0.0.1` sirf container ke andar sunta hai, browser connect nahi kar paata |
| Vite me `--host` flag | Wahi wajah — bina iske browser me page nahi khulta |
| Compose me `- /app/node_modules` | `./frontend:/app` mount container ka node_modules chhupa deta; ye line usse bachati hai |
| CORS `allow_origins=["*"]` | Dev me 5173 → 8000 call ke liye. **Production me domain daalna hai** |

### Docs banaye

- [steps_to_build.md](steps_to_build.md) — poora setup, har Dockerfile line comment ke saath explained
- [docker_commands.md](docker_commands.md) — Docker command reference + troubleshooting

### Verify kiya

| URL | Expected |
|---|---|
| http://localhost:5173 | React default page |
| http://localhost:8000 | `{"message": "FastAPI Server Running Perfectly!"}` |
| http://localhost:8000/api/health | `{"status": "healthy"}` |
| http://localhost:8000/docs | Swagger UI |

---

## 2026-08-09 — Phase 0: Git + Ignore Files

### Kya add kiya

| File | Kaam |
|---|---|
| `.gitignore` (root) | `node_modules/`, `__pycache__/`, `.env`, `dist/`, venv, editor files |
| `backend/.dockerignore` | `__pycache__`, venv, `.env`, `.git` container me nahi jaayenge |
| `frontend/.dockerignore` | **`node_modules/`** container me nahi jayega |
| `README.md` | GitHub landing page |
| `PROJECT_LOG.md` | Yehi file |

### Kyu zaroori tha

- **`.gitignore`** — `node_modules/` me hazaaron files hoti hain. Ek baar commit ho gaya to repo bhaari ho jata hai aur history se hatana dard hai. `.env` commit hona sabse bada risk — passwords public ho jaate hain.
- **`frontend/.dockerignore`** — sabse bada asli fayda yahin hai. Dockerfile me `COPY . .` likha hai, matlab bina iske **Windows ka `node_modules` Linux container me copy ho raha tha**. Wo binaries Linux pe chalti hi nahi, aur build bhi bahut slow hota hai.
- **`backend/.dockerignore`** — `.env` aur `.git` image me jaane se rok deta hai (security + size).

### Git setup

```bash
git init
git add .
git commit -m "Initial commit: Dockerized FastAPI + React skeleton"
```

---

## 📝 Agla Entry Yahan Se (template)

```markdown
## YYYY-MM-DD — Phase N: <title>

### Kya kiya
-

### Kyu (decisions)
| Decision | Wajah |
|---|---|

### Problems aur fix
| Problem | Fix |
|---|---|

### Verify kaise kiya
-
```
