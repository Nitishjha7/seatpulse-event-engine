# SeatPulse Event Engine — Project Setup Steps

FastAPI (backend) + React/Vite (frontend), both via Docker. No need to install Node or Python on the host machine — only **Docker Desktop** is required.

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

## Step 1 — Create the root folder

```
seatpulse-event-engine
```

This is the main project folder. Everything will be created inside it. Open your terminal in this folder.

---

## Step 2 — Create the Vite React app (via Docker)

We will run Vite inside a Docker container without installing Node. The command syntax varies by shell due to how they handle the current directory.

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

> ⚠️ `$(pwd)` only works in bash — use the versions above for PowerShell/CMD.

This command creates a `frontend/` folder inside the root.

### How does this command work?

```
docker run --rm -v "${PWD}:/app" -w /app node:22-alpine npm create vite@latest frontend -- --template react
   │      │     │                   │        │            └─────────── Command to run inside the container
   │      │     │                   │        └─ Image to use
   │      │     │                   └─ Working directory (inside the container)
   │      │     └─ Mount folder (host:container)
   │      └─ Delete container after completion
   └─ Create and run a new container
```

| Part | Purpose |
|---|---|
| `docker run` | Create and run a new container |
| `--rm` | **Delete** the container after completion. This is a one-time task. |
| `-v "${PWD}:/app"` | **Bind** the current folder to the container's `/app`. This ensures the `frontend/` folder created by the container appears in your local directory. |
| `-w /app` | Set the terminal working directory to `/app` inside the container. |
| `node:22-alpine` | Ready-made Node.js image. **No need to install Node on your PC.** |
| `npm create vite@latest frontend` | Create a Vite project named `frontend`. |
| `--` | Separator — flags following this belong to **Vite**, not npm. |
| `--template react` | Use the React template. |

**Why different syntax for `${PWD}` / `%cd%` / `$(pwd)`?** They all represent the "full path of the current folder," but each shell has its own syntax.

| Shell | Syntax |
|---|---|
| PowerShell | `${PWD}` |
| CMD | `%cd%` |
| Git Bash / Linux / macOS | `$(pwd)` |

---

## Step 3 — Navigate to the frontend folder

```bash
cd frontend
```

---

## Step 4 — Create the frontend Dockerfile

**Dockerfile content**

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

## Step 5 — Verify the file

**CMD**
```cmd
type Dockerfile
```

**PowerShell / Git Bash**
```powershell
cat Dockerfile
```

---

## Step 6 — Return to the root folder

```bash
cd ..
```

---

## Step 7 — Create and enter the backend folder

```bash
mkdir backend
cd backend
```

---

## Step 8 — Create `requirements.txt`

This file lists the Python packages required by the backend.

**Content**
```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
```

**Adding new packages** (e.g., for database):
```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
sqlalchemy>=2.0.0
psycopg2-binary>=2.9.0
```
After adding, run `docker compose up -d --build backend`.

**CMD**
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

## Step 9 — Create `main.py`

The backend entry point. The Dockerfile points to `main:app` — `main` is the filename, `app` is the variable.

**Content**
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

**CMD**
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

## Step 10 — Create the backend Dockerfile

**Content**
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

## Step 11 — Return to the root folder

```bash
cd ..
```

---

## Step 12 — Create `docker-compose.yml`

**Content**
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

## Step 13 — Start everything

```bash
docker compose up --build
```

---

## Verify — Is everything running?

| Service | URL |
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
| Build and start | `docker compose up --build` |
| Run in background (detached) | `docker compose up -d` |
| Stop containers | `docker compose down` |
| Stop + delete volumes | `docker compose down -v` |
| Restart a service | `docker compose restart backend` |
| Start a single service | `docker compose up -d backend` |

> ⚠️ `down -v` deletes volumes — database data will be lost.

### Logs

| Action | Command |
|---|---|
| Live logs (all services) | `docker compose logs -f` |
| Backend logs | `docker compose logs -f backend` |
| Frontend logs | `docker compose logs -f frontend` |
| Last 100 lines | `docker compose logs --tail=100 backend` |

### Build / Rebuild

| Action | Command |
|---|---|
| Rebuild + restart single service | `docker compose up -d --build backend` |
| Rebuild from scratch (ignore cache) | `docker compose build --no-cache` |
| Rebuild frontend from scratch | `docker compose build --no-cache frontend` |

### Status

| Action | Command |
|---|---|
| Compose containers | `docker compose ps` |
| All running containers | `docker ps` |
| All containers (including stopped) | `docker ps -a` |
| List images | `docker images` |

---

## `docker exec` — Run commands inside a container

The container must be running.

### Open a shell inside a container

```bash
# Backend (python:3.11-slim includes bash)
docker compose exec backend bash

# Frontend (node:20-alpine does NOT include bash — use sh)
docker compose exec frontend sh
```

### Run commands without opening a shell

```bash
docker compose exec backend python --version
docker compose exec backend pip list
docker compose exec backend ls -la
```

### Run as root user

```bash
docker compose exec -u root backend bash
docker exec -it -u root react_frontend sh
```

### If the container is stopped

Use `run` to create a temporary container:

```bash
docker compose run --rm backend python --version
```

---

## Cleanup Commands

```bash
docker compose down -v              # Delete containers + volumes
docker system prune                 # Remove unused containers/networks/images
docker system prune -a              # Aggressive cleanup (includes unused images)
docker volume ls                    # List volumes
docker volume prune                 # Delete unused volumes
```

---

## Common Problems

| Problem | Fix |
|---|---|
| `$(pwd)` not working in PowerShell | Use `${PWD}` |
| Frontend not loading | Check `--host` flag in Dockerfile |
| Port already in use | Run `docker compose down` |
| Code changes not reflecting | Check volume mounts in `docker-compose.yml` |
| New npm package not found | `docker compose build --no-cache frontend` |
| CORS error | Ensure CORS middleware is configured in `main.py` |
