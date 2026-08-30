# SeatPulse Event Engine — Project Setup Steps

FastAPI (backend) + React/Vite (frontend), dono Docker ke through. Host machine pe Node ya Python install karne ki zaroorat nahi — sirf **Docker Desktop** chahiye.

---

## Final Folder Structure

```
seatpulse-event-engine/
├── backend/
│   ├── main.py
│   ├── requirements.txt
│   └── Dockerfile
├── frontend/
│   ├── src/
│   ├── package.json
│   └── Dockerfile
└── docker-compose.yml
```

---

## Step 1 — Root folder banao

```
seatpulse-event-engine
```

Ye project ka main folder hai. Isi ke andar sab kuch banega. Terminal isi folder me kholo.

---

## Step 2 — Vite React app banao (Docker se)

Node install kiye bina Docker container ke andar Vite chalayenge. Command har shell me thodi alag hai kyunki current-directory ka syntax alag hota hai.

**PowerShell**
```powershell
docker run --rm -v "${PWD}:/app" -w /app node:22-alpine npm create vite@latest frontend -- --template react
```

**CMD**
```cmd
docker run --rm -v "%cd%:/app" -w /app node:22-alpine npm create vite@latest frontend -- --template react
```

**Git Bash / Linux / macOS**
```bash
docker run --rm -v $(pwd):/app -w /app node:22-alpine npm create vite@latest frontend -- --template react
```

> ⚠️ `$(pwd)` sirf bash me chalta hai — PowerShell/CMD me upar wali version use karo.

Ye command root ke andar `frontend/` folder bana degi.

### Ye command kaam kaise karti hai?

```
docker run --rm -v "${PWD}:/app" -w /app node:22-alpine npm create vite@latest frontend -- --template react
   │      │     │                   │        │            └─────────── container ke andar chalne wali command
   │      │     │                   │        └─ kaunsi image use karni hai
   │      │     │                   └─ working directory (container ke andar)
   │      │     └─ folder mount karo (host:container)
   │      └─ kaam khatam hote hi container delete kar do
   └─ naya container banao aur chalao
```

| Part | Kyu likha hai |
|---|---|
| `docker run` | Naya container banao aur usme command chalao |
| `--rm` | Kaam khatam hote hi container **delete** ho jaye. Ye ek baar ka kaam hai, container padha rehne ka koi matlab nahi |
| `-v "${PWD}:/app"` | Current folder ko container ke `/app` se **jod do**. Isi wajah se container jo `frontend/` folder banayega wo **tumhare asli folder me dikhega** — warna container delete hote hi sab udd jata |
| `-w /app` | Container ke andar terminal `/app` me khulega, isliye files sahi jagah banengi |
| `node:22-alpine` | Node.js ki ready-made image. **Isi wajah se tumhe apne PC pe Node install karne ki zaroorat nahi** |
| `npm create vite@latest frontend` | Vite ka project banao, folder ka naam `frontend` |
| `--` | Separator — iske baad ke flags npm ke nahi, **Vite ke** hain |
| `--template react` | React template use karo (Vue/Svelte nahi) |

**`${PWD}` / `%cd%` / `$(pwd)` alag kyu?** Teeno ka matlab ek hi hai — "current folder ka full path". Bas har shell ka apna syntax hai:

| Shell | Syntax |
|---|---|
| PowerShell | `${PWD}` |
| CMD | `%cd%` |
| Git Bash / Linux / macOS | `$(pwd)` |

---

## Step 3 — Frontend folder me jao

```bash
cd frontend
```

---

## Step 4 — Frontend ka Dockerfile banao

Content sab shells me same hai, bas file likhne ka tarika alag hai.

**Dockerfile content — samajhne ke liye (comments ke saath)**

```dockerfile
# FROM = base image. Har Dockerfile FROM se hi shuru hota hai.
# Ye ek ready-made Linux + Node.js 20 ka box hai — hume khud Node install nahi karna padta.
# "alpine" = sabse chhoti Linux (~50MB). Isme bash nahi hota, sirf sh.
FROM node:20-alpine

# WORKDIR = container ke andar kaam karne ka folder set karo.
# Iske baad ke saare commands (COPY, RUN, CMD) isi folder me chalenge.
# Folder na ho to Docker khud bana deta hai.
WORKDIR /app

# Sirf package.json aur package-lock.json copy karo — abhi poora code nahi.
# * ka matlab: package.json + package-lock.json dono.
# Ye alag se isliye kiya hai (neeche wali line dekho) taki Docker ka cache kaam kare.
COPY package*.json ./

# Dependencies install karo.
# Ye layer tabhi dubara chalegi jab package.json badlega.
# Sirf code badla to Docker ise cache se utha lega = build fast.
RUN npm install

# Ab baaki poora code copy karo (src/, index.html, vite.config.js waqerah).
# Pehla "." = tumhara folder, doosra "." = container ka /app
COPY . .

# Sirf documentation hai — "ye app 5173 port pe chalti hai".
# Ye khud port open NAHI karta, wo kaam docker-compose ki "ports:" line karti hai.
EXPOSE 5173

# Container start hote hi ye command chalegi — dev server on.
# --host isliye: bina iske Vite sirf 127.0.0.1 (container ke andar) sunta hai,
# aur tumhara browser use nahi kar paata. --host se wo 0.0.0.0 pe sunta hai.
# Beech wala -- npm ka separator hai: iske baad ka flag Vite ko jayega, npm ko nahi.
CMD ["npm", "run", "dev", "--", "--host"]
```

> Comments sirf samajhne ke liye hain. Neeche wale commands **bina comment** wali clean file banate hain — chahe to comments rakh bhi sakte ho, `#` Dockerfile me valid hai.

**Clean version (jo actually banegi)**
```dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
```

**PowerShell**
```powershell
@"
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
"@ | Out-File -Encoding utf8 Dockerfile
```

**Git Bash**
```bash
cat << 'EOF' > Dockerfile
FROM node:20-alpine

WORKDIR /app

COPY package*.json ./
RUN npm install

COPY . .

EXPOSE 5173

CMD ["npm", "run", "dev", "--", "--host"]
EOF
```

**CMD**
```cmd
(
echo FROM node:20-alpine
echo.
echo WORKDIR /app
echo.
echo COPY package*.json ./
echo RUN npm install
echo.
echo COPY . .
echo.
echo EXPOSE 5173
echo.
echo CMD ["npm", "run", "dev", "--", "--host"]
) > Dockerfile
```

---

## Step 5 — File check karo

**CMD**
```cmd
type Dockerfile
```

**PowerShell / Git Bash**
```powershell
cat Dockerfile
```

---

## Step 6 — Root folder me wapas jao

```bash
cd ..
```

---

## Step 7 — Backend folder banao aur usme jao

```bash
mkdir backend
cd backend
```

---

## Step 8 — `requirements.txt` banao

Ye file batati hai ki backend ko kaunse Python packages chahiye. `pip install -r requirements.txt` isi file ko padh ke sab install karta hai.

**Content — samajhne ke liye (comments ke saath)**

```python
# fastapi = wo framework jisse API banti hai (routes, validation, auto docs)
# >= ka matlab: 0.110.0 ya usse naya version chalega
# (== likhte to bilkul wahi version lock ho jata — production me aksar wahi karte hain)
fastapi>=0.110.0

# uvicorn = server jo FastAPI app ko actually chalata hai.
# FastAPI khud server nahi hai, sirf framework hai — chalane ke liye uvicorn chahiye.
# [standard] = extra packages ka bundle: fast websockets, better logs,
# aur watchfiles (jo --reload ko kaam karne deta hai)
uvicorn[standard]>=0.28.0
```

> `#` Python aur requirements.txt dono me comment hota hai, to comments rakhna safe hai.

**Clean version**
```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
```

**Naya package add karna ho** (jaise database ke liye):
```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
```
Add karne ke baad `docker compose up -d --build backend` chalana zaroori hai.

**CMD** (`>` ko escape karne ke liye `^` lagta hai)
```cmd
(
echo fastapi^>=0.110.0
echo uvicorn[standard]^>=0.28.0
) > requirements.txt
```

**Git Bash**
```bash
cat << 'EOF' > requirements.txt
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
EOF
```

---

## Step 9 — `main.py` banao

Backend ka entry point. Dockerfile me likha `main:app` isi file ko point karta hai — `main` = file ka naam, `app` = neeche banaya gaya variable. **Isliye file ka naam `main.py` aur variable ka naam `app` hi rakhna**, warna server start nahi hoga.

**Content — samajhne ke liye (comments ke saath)**

```python
# FastAPI class import — isi se app banega
from fastapi import FastAPI
# CORS middleware — browser ki security rule handle karne ke liye (neeche detail me)
from fastapi.middleware.cors import CORSMiddleware

# app = poori application ka object.
# Dockerfile me "main:app" likha hai — wo isi variable ko dhoondhta hai.
# Naam badla (jaise server = FastAPI()) to Dockerfile bhi badalna padega.
app = FastAPI()

# ---- CORS ----
# Problem: frontend port 5173 pe hai, backend 8000 pe. Browser inhe
# "alag websites" maanta hai aur by default API call block kar deta hai.
# Ye middleware browser ko batata hai "haan, ye call allowed hai".
app.add_middleware(
    CORSMiddleware,
    # kaun call kar sakta hai. ["*"] = koi bhi.
    # Development me theek hai. Production me: ["https://tumhara-domain.com"]
    allow_origins=["*"],
    # cookies / auth headers bhejne ki permission
    allow_credentials=True,
    # kaunse HTTP methods allowed — ["*"] = GET, POST, PUT, DELETE sab
    allow_methods=["*"],
    # kaunse headers allowed — ["*"] = sab (jaise Authorization, Content-Type)
    allow_headers=["*"],
)

# @app.get("/") = decorator. FastAPI ko batata hai:
# "jab koi GET request / pe aaye, to neeche wala function chalao"
@app.get("/")
def read_root():
    # dict return karo — FastAPI ise automatically JSON bana deta hai
    return {"message": "FastAPI Server Running Perfectly!"}

# Health check route. Ye batane ke liye ki server zinda hai.
# Deployment, monitoring aur load balancers isi tarah ka endpoint check karte hain.
@app.get("/api/health")
def health_check():
    return {"status": "healthy"}
```

> ⚠️ `allow_origins=["*"]` + `allow_credentials=True` saath me production me **kaam nahi karta** (browser reject karta hai) aur secure bhi nahi hai. Live jaane se pehle `allow_origins` me apna actual domain daalna.

**Clean version**
```python
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}
```

**Git Bash**
```bash
cat << 'EOF' > main.py
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.get("/")
def read_root():
    return {"message": "FastAPI Server Running Perfectly!"}

@app.get("/api/health")
def health_check():
    return {"status": "healthy"}
EOF
```

**CMD** (brackets `(` `)` escape karne padte hain)
```cmd
(
echo from fastapi import FastAPI
echo from fastapi.middleware.cors import CORSMiddleware
echo.
echo app = FastAPI^(^)
echo.
echo app.add_middleware^(
echo     CORSMiddleware,
echo     allow_origins=["*"],
echo     allow_credentials=True,
echo     allow_methods=["*"],
echo     allow_headers=["*"],
echo ^)
echo.
echo @app.get^("/"^)
echo def read_root^(^):
echo     return {"message": "FastAPI Server Running Perfectly!"}
echo.
echo @app.get^("/api/health"^)
echo def health_check^(^):
echo     return {"status": "healthy"}
) > main.py
```

---

## Step 10 — Backend ka Dockerfile banao

**Content — samajhne ke liye (comments ke saath)**

```dockerfile
# Base image: Linux + Python 3.11 pehle se installed.
# "slim" = chhoti version (~130MB), bina extra tools ke.
# alpine bhi hoti hai par Python me alpine slow build karti hai — isliye slim.
FROM python:3.11-slim

# Container ke andar kaam karne ka folder
WORKDIR /app

# Sirf requirements.txt copy karo — abhi baaki code nahi.
# Wajah: agar poora code pehle copy karte, to har chhote code change pe
# pip install dubara chalta (slow). Ab wo sirf requirements badalne pe chalega.
COPY requirements.txt .

# Packages install karo.
# --no-cache-dir = pip apni downloaded files save na kare.
# Image chhoti rehti hai, aur container me wo cache kisi kaam ka nahi hota.
# -r = "is file me se padh ke install kar"
RUN pip install --no-cache-dir -r requirements.txt

# Ab poora backend code copy karo (main.py waqerah)
COPY . .

# Documentation: ye app 8000 pe chalti hai. Port khud open nahi hota —
# wo docker-compose ki "ports:" line karti hai.
EXPOSE 8000

# Container start hote hi server chalu.
# main:app     -> main.py file ka "app" variable
# --host 0.0.0.0 -> ZAROORI. Default 127.0.0.1 hota hai jo sirf container ke
#                   andar sunta hai; tumhara browser connect hi nahi kar paata.
#                   0.0.0.0 = "sab network interfaces pe suno"
# --port 8000  -> kis port pe suno
# --reload     -> file save karte hi server restart. Sirf development ke liye.
#                 Production me ye hata dena (CPU khata hai aur risky hai).
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

**Clean version**
```dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
```

**Git Bash**
```bash
cat << 'EOF' > Dockerfile
FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
EOF
```

**CMD**
```cmd
(
echo FROM python:3.11-slim
echo.
echo WORKDIR /app
echo.
echo COPY requirements.txt .
echo RUN pip install --no-cache-dir -r requirements.txt
echo.
echo COPY . .
echo.
echo EXPOSE 8000
echo.
echo CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
) > Dockerfile
```

---

## Step 11 — Root folder me wapas jao

```bash
cd ..
```

---

## Step 12 — `docker-compose.yml` banao

Ab tak humne do alag Dockerfile banaye. Compose ka kaam: **dono ko ek saath, ek command se chalana** aur aapas me jodna.

**Content — samajhne ke liye (comments ke saath)**

```yaml
# services = kaun kaun se containers chalane hain.
# Yahan do hain: backend aur frontend.
services:

  # "backend" ye service ka naam hai. Ye naam do jagah kaam aata hai:
  #  1. commands me -> docker compose logs -f backend
  #  2. network me  -> frontend isko http://backend:8000 se call kar sakta hai
  backend:
    # is folder ke Dockerfile se image banao.
    # (agar ready-made image use karni ho to "build" ki jagah "image: postgres:16" likhte)
    build: ./backend

    # container ka fixed naam. Na dete to Docker random naam deta
    # (jaise seatpulse-backend-1). Fixed naam se docker exec likhna aasan.
    container_name: fastapi_backend

    # "host_port:container_port"
    # Left  8000 = tumhare PC ka port (browser me localhost:8000)
    # Right 8000 = container ke andar ka port (jahan uvicorn sun raha hai)
    # Port busy ho to left wala badal sakte ho: "8001:8000"
    ports:
      - "8000:8000"

    # host ka ./backend folder container ke /app se jod do.
    # Isi wajah se tum apne editor me main.py save karte ho aur --reload
    # turant server restart kar deta hai — dubara build karne ki zaroorat nahi.
    volumes:
      - ./backend:/app

  frontend:
    build: ./frontend
    container_name: react_frontend

    # 5173 = Vite ka default dev port
    ports:
      - "5173:5173"

    volumes:
      # code live sync — React file save karo, browser turant update
      - ./frontend:/app

      # ⚠️ Ye line SABSE important hai.
      # Upar wali line ne tumhara ./frontend container ke /app pe chipka diya —
      # isme node_modules hai hi nahi (wo build ke waqt container ke ANDAR bana tha).
      # Nateeja: container ka node_modules chhup jata aur app crash ho jati.
      # Ye line kehti hai "/app/node_modules ko host se mat jodo, container wala hi rakho".
      - /app/node_modules

    # backend pehle start hoga, phir frontend.
    # Note: ye sirf START ka order hai — ye guarantee NAHI karta ki
    # backend ready ho chuka hai. Poori guarantee ke liye healthcheck lagta hai.
    depends_on:
      - backend
```

> ⚠️ YAML me **indentation (spaces) hi sab kuch hai** — tabs kabhi mat use karna, warna error aayegi. Har level pe 2 spaces.

**Clean version**
```yaml
services:
  backend:
    build: ./backend
    container_name: fastapi_backend
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app

  frontend:
    build: ./frontend
    container_name: react_frontend
    ports:
      - "5173:5173"
    volumes:
      - ./frontend:/app
      - /app/node_modules
    depends_on:
      - backend
```

**Git Bash**
```bash
cat << 'EOF' > docker-compose.yml
services:
  backend:
    build: ./backend
    container_name: fastapi_backend
    ports:
      - "8000:8000"
    volumes:
      - ./backend:/app

  frontend:
    build: ./frontend
    container_name: react_frontend
    ports:
      - "5173:5173"
    volumes:
      - ./frontend:/app
      - /app/node_modules
    depends_on:
      - backend
EOF
```

**CMD**
```cmd
(
echo services:
echo   backend:
echo     build: ./backend
echo     container_name: fastapi_backend
echo     ports:
echo       - "8000:8000"
echo     volumes:
echo       - ./backend:/app
echo.
echo   frontend:
echo     build: ./frontend
echo     container_name: react_frontend
echo     ports:
echo       - "5173:5173"
echo     volumes:
echo       - ./frontend:/app
echo       - /app/node_modules
echo     depends_on:
echo       - backend
) > docker-compose.yml
```

---

## Step 13 — Sab kuch start karo

```bash
docker compose up --build
```

---

## Verify — sab chal raha hai?

| Kya | URL |
|---|---|
| Frontend (React) | http://localhost:5173 |
| Backend (FastAPI) | http://localhost:8000 |
| Health check | http://localhost:8000/api/health |
| API docs (Swagger) | http://localhost:8000/docs |

---

## Handy Commands

### Start / Stop

| Action | Command |
|---|---|
| Build karke start karo | `docker compose up --build` |
| Background me chalao (detached) | `docker compose up -d` |
| Stop containers | `docker compose down` |
| Stop + volumes bhi delete karo | `docker compose down -v` |
| Sirf ek service restart karo | `docker compose restart backend` |
| Ek hi service start karo | `docker compose up -d backend` |

> ⚠️ `down -v` volumes delete kar deta hai — database data bhi udd jayega. Soch ke chalana.

### Logs

| Action | Command |
|---|---|
| Live logs (sab services) | `docker compose logs -f` |
| Sirf backend ke logs | `docker compose logs -f backend` |
| Sirf frontend ke logs | `docker compose logs -f frontend` |
| Last 100 lines hi dikhao | `docker compose logs --tail=100 backend` |

### Build / Rebuild

| Action | Command |
|---|---|
| Single service rebuild + restart | `docker compose up -d --build backend` |
| Scratch se rebuild (cache ignore) | `docker compose build --no-cache` |
| Sirf frontend scratch se rebuild | `docker compose build --no-cache frontend` |

### Status

| Action | Command |
|---|---|
| Compose ke containers | `docker compose ps` |
| Saare running containers | `docker ps` |
| Stopped containers bhi | `docker ps -a` |
| Images list | `docker images` |

---

## `docker exec` — Container ke andar command chalao

Container **chalta hua** hona chahiye (`docker compose up -d` ke baad). Do tarike hain:

- `docker compose exec <service>` → service ka naam use karo (`backend`, `frontend`)
- `docker exec -it <container_name>` → container ka naam (`fastapi_backend`, `react_frontend`)

Dono same cheez karte hain. `-it` matlab interactive terminal.

### Container ke andar shell kholo

```bash
# Backend (python:3.11-slim me bash hota hai)
docker compose exec backend bash
docker exec -it fastapi_backend bash

# Frontend (node:20-alpine me bash NAHI hota — sh use karo)
docker compose exec frontend sh
docker exec -it react_frontend sh
```

> ⚠️ Alpine images me `bash` nahi hota, `sh` chalta hai. Isliye frontend ke liye hamesha `sh`.

Andar jaane ke baad normal terminal ki tarah kaam karo, `exit` likh ke bahar aao.

### Backend ke andar commands (bina shell khole)

```bash
docker compose exec backend python --version
docker compose exec backend pip list                    # installed packages
docker compose exec backend pip install requests        # temporary install
docker compose exec backend ls -la                      # files dekho
docker compose exec backend python -c "import fastapi; print(fastapi.__version__)"
docker compose exec backend cat requirements.txt
```

> ⚠️ `pip install` container ke andar **temporary** hai — `docker compose down` pe chala jayega.
> Permanent chahiye to `requirements.txt` me line add karo, phir `docker compose up -d --build backend`.

### Frontend ke andar commands

```bash
docker compose exec frontend npm install axios          # naya package
docker compose exec frontend npm list                   # installed packages
docker compose exec frontend node --version
docker compose exec frontend ls -la
docker compose exec frontend npm run build              # production build
```

> Package install karne ke baad `docker compose restart frontend` kar lena, taki Vite naya package pick kare.

### Root user ke taur pe andar jao

Permission error aa raha ho to:

```bash
docker compose exec -u root backend bash
docker exec -it -u root react_frontend sh
```

### Container band hai to?

`exec` sirf running container pe chalta hai. Band container me command chalani ho to `run` use karo (naya temporary container banega, kaam ke baad delete):

```bash
docker compose run --rm backend python --version
docker compose run --rm frontend npm install
```

---

## Cleanup Commands

```bash
docker compose down -v              # containers + volumes delete
docker system prune                 # unused containers/networks/images hatao
docker system prune -a              # aur aggressive (saari unused images bhi)
docker volume ls                    # volumes dekho
docker volume prune                 # unused volumes delete
```

> ⚠️ `prune -a` sabhi projects ki unused images delete karta hai, sirf is project ki nahi. Agli baar sab dubara download hoga.

---

## Common Problems

| Problem | Fix |
|---|---|
| `$(pwd)` PowerShell me kaam nahi kar raha | `${PWD}` use karo (CMD me `%cd%`) |
| Browser me frontend nahi khul raha | Dockerfile me `--host` flag check karo |
| Port already in use | `docker compose down` chalao, ya `docker ps` se purana container band karo |
| Frontend me code change dikh nahi raha | volume mount check karo `docker-compose.yml` me |
| Naya npm package install kiya, container me nahi mila | `docker compose build --no-cache frontend` |
| Frontend se API call CORS error de rahi | `main.py` me CORS middleware laga hai ya nahi, check karo |
