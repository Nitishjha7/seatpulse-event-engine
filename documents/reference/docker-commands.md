# Docker Commands — Full Reference

General Docker commands cheatsheet. For project-specific setup, see [Docker setup](../setup/01-docker-setup.md).

> **Note:** Wherever `<container>` is used, you can use the container **name** (`fastapi_backend`) or **ID** (`a1b2c3d4`). The first 3-4 characters of the ID are sufficient if unique.

---

## 1. Viewing Containers

| Action | Command |
|---|---|
| Running containers | `docker ps` |
| All containers (including stopped) | `docker ps -a` |
| Container IDs only | `docker ps -q` |
| All IDs (including stopped) | `docker ps -aq` |
| Last created container | `docker ps -l` |
| With size | `docker ps -s` |

**Output explanation:**

```
CONTAINER ID   IMAGE              COMMAND       CREATED       STATUS          PORTS                    NAMES
a1b2c3d4e5f6   seatpulse-backend  "uvicorn..."  2 hours ago   Up 2 hours      0.0.0.0:8000->8000/tcp   fastapi_backend
```

| Column | Meaning |
|---|---|
| `CONTAINER ID` | Unique ID — used in commands |
| `IMAGE` | Source image |
| `COMMAND` | Process running inside |
| `STATUS` | `Up` = running, `Exited (0)` = normal stop, `Exited (1)` = crash |
| `PORTS` | `host:container` mapping |
| `NAMES` | Container name |

**Clean output** (essential info only):

```bash
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
```

**Filtering:**

```bash
docker ps -f "status=running"
docker ps -a -f "status=exited"       # stopped only
docker ps -f "name=backend"           # name contains backend
docker ps -f "ancestor=python:3.11-slim"   # containers from this image
```

---

## 2. Container Start / Stop / Restart

| Action | Command |
|---|---|
| Stop container | `docker stop <container>` |
| Start stopped container | `docker start <container>` |
| Restart | `docker restart <container>` |
| Force kill (immediate) | `docker kill <container>` |
| Pause (freeze) | `docker pause <container>` |
| Unpause | `docker unpause <container>` |

```bash
docker stop fastapi_backend
docker stop fastapi_backend react_frontend     # multiple at once
docker stop $(docker ps -q)                    # stop ALL running containers
docker restart react_frontend
```

> `stop` performs a graceful shutdown (10s timeout), `kill` terminates immediately. Use `stop` by default.

**Increase timeout** (if a heavy app takes time to shut down):

```bash
docker stop -t 30 fastapi_backend
```

---

## 3. Deleting Containers

```bash
docker rm <container>                # delete stopped container
docker rm -f <container>             # force delete running container
docker rm $(docker ps -aq)           # delete all stopped containers
docker container prune               # delete all stopped containers (safe method)
```

> `rm` deletes the container only, **not the image**. Images are deleted separately (Section 4).

---

## 4. Images

| Action | Command |
|---|---|
| All images | `docker images` |
| Dangling images with IDs | `docker images -f "dangling=true"` |
| IDs only | `docker images -q` |
| Download image | `docker pull node:20-alpine` |
| Delete image | `docker rmi <image>` |
| Force delete | `docker rmi -f <image>` |
| Delete unused images | `docker image prune` |
| Image history (layers) | `docker history <image>` |

**Output:**

```
REPOSITORY          TAG        IMAGE ID       CREATED        SIZE
seatpulse-backend   latest     f1e2d3c4b5a6   2 hours ago    215MB
python              3.11-slim  9a8b7c6d5e4f   3 weeks ago    130MB
```

> Images named `<none>` are **dangling** (leftovers from old builds). Clean them with `docker image prune`.

**Building images:**

```bash
docker build -t myapp .                      # from Dockerfile in current folder
docker build -t myapp:v1 ./backend           # from specific folder
docker build --no-cache -t myapp .           # ignore cache
```

---

## 5. Accessing Containers / Running Commands

```bash
docker exec -it <container> bash        # open shell (debian/ubuntu based)
docker exec -it <container> sh          # for alpine images
docker exec <container> ls -la          # run command and exit
docker exec -it -u root <container> sh  # enter as root (for permissions)
```

> Alpine images (`node:20-alpine`) do not have `bash` — use `sh`.
> Use `exit` to leave the shell.

**Run a new temporary container** (without affecting existing ones):

```bash
docker run --rm -it python:3.11-slim bash        # auto-delete after exit
docker run --rm -v "${PWD}:/app" -w /app node:20-alpine npm install
```

| Flag | Meaning |
|---|---|
| `--rm` | Auto-delete container after task |
| `-it` | Interactive terminal |
| `-d` | Run in background |
| `-v host:container` | Mount folder |
| `-w /app` | Working directory inside container |
| `-p 8000:8000` | Port mapping (host:container) |
| `-e KEY=value` | Environment variable |
| `--name my-app` | Container name |

---

## 6. Logs

```bash
docker logs <container>                  # all logs
docker logs -f <container>               # live logs (Ctrl+C to exit)
docker logs --tail=50 <container>        # last 50 lines
docker logs -f --tail=50 <container>     # last 50 + live
docker logs --since 10m <container>      # logs from last 10 minutes
docker logs -t <container>               # with timestamps
```

> Container crashed? Run `docker logs <container>` — the error will be there.

---

## 7. Inspect — Extracting Details

```bash
docker inspect <container>               # full details (JSON)
docker inspect <image>                   # image details
docker stats                             # live CPU/RAM usage (all containers)
docker stats <container>                 # single container stats
docker top <container>                   # running processes
docker port <container>                  # port mappings
docker diff <container>                  # file changes from image
```

**Extract specific values:**

```bash
docker inspect -f '{{.State.Status}}' fastapi_backend           # running/exited
docker inspect -f '{{.State.ExitCode}}' fastapi_backend         # crash code
docker inspect -f '{{.NetworkSettings.IPAddress}}' fastapi_backend
docker inspect -f '{{.Config.Image}}' fastapi_backend           # image used
docker inspect -f '{{json .Config.Env}}' fastapi_backend        # env variables
```

---

## 8. Copying Files

```bash
docker cp <container>:/app/main.py ./main.py      # container to host
docker cp ./config.json <container>:/app/         # host to container
docker cp <container>:/app/logs ./logs            # copy entire folder
```

---

## 9. Volumes (Data)

```bash
docker volume ls                    # all volumes
docker volume inspect <volume>      # details
docker volume create my-data        # create new volume
docker volume rm <volume>           # delete
docker volume prune                 # delete unused volumes
```

> Volumes store database data. Deleting them results in data loss.

---

## 10. Networks

```bash
docker network ls                          # all networks
docker network inspect <network>           # connected containers
docker network create my-network           # create new network
docker network connect <network> <container>
docker network disconnect <network> <container>
docker network prune                       # delete unused networks
```

> Compose creates its own network. This allows the `backend` service to call the frontend via `http://backend:8000`.

---

## 11. Cleanup — Freeing Space

```bash
docker system df              # disk usage by Docker
docker container prune        # stopped containers
docker image prune            # dangling images
docker image prune -a         # all unused images
docker volume prune           # unused volumes
docker network prune          # unused networks
docker system prune           # everything (except volumes)
docker system prune -a        # everything + unused images
docker system prune -a --volumes    # NUCLEAR — includes volumes
```

> ⚠️ `prune` commands affect **all projects**, not just the current one.
> The `--volumes` flag is destructive — database data will be deleted.

---

## ⚠️ Data Safety — Destructive Commands

### Safe vs Destructive

| Action | Command | Data |
|---|---|---|
| Daily stop | `docker compose down` | ✅ Safe |
| Restart | `docker compose restart` | ✅ Safe |
| Backend rebuild | `docker compose up -d --build backend` | ✅ Safe |
| Reset frontend `node_modules` | `--renew-anon-volumes` (below) | ✅ Safe |
| **Fresh start** | `docker compose down -v` | ❌ **DB data deleted** |
| **Fresh start** | `docker volume prune` | ❌ Unused volumes deleted |
| **Fresh start** | `docker system prune -a --volumes` | ❌ Everything, all projects |

### Local database vs Docker

| | Location | `down -v` effect |
|---|---|---|
| **Local PostgreSQL** (system installed) | Windows service, `C:\Program Files\PostgreSQL\...` | ❌ **No effect.** Docker cannot access it |
| **Docker PostgreSQL** | Named volume (`postgres_data`) | ✅ **Fully deleted** |

Docker commands only affect the Docker environment. They cannot touch locally installed databases.

### Reset `node_modules` volume, keep DB

Problem: Added a new npm package, old anonymous volume persists — but `down -v` deletes the DB.

**Easiest way (recreate frontend anonymous volumes only):**
```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

**Or manually:**
```bash
docker compose down
docker volume ls                  # long hashes = anonymous volumes
docker volume rm <hash>
docker compose up -d --build
```

> `--renew-anon-volumes` only recreates **anonymous** volumes. `postgres_data` is a **named** volume and remains intact.

### Verify volume deletion

```bash
docker volume ls
docker volume ls -q | grep postgres        # empty = deleted
```

### Backup (for real data)

```bash
docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql
docker compose exec -T db psql -U seatpulse -d seatpulse < backup.sql   # restore
```

> This project uses seed data, so recovery after `down -v` is just 3 commands:
> `up -d` → `alembic upgrade head` → `python seed.py`.

---

## 12. Docker Compose

| Action | Command |
|---|---|
| Start (with build) | `docker compose up --build` |
| Start in background | `docker compose up -d` |
| Stop | `docker compose down` |
| Stop + delete volumes | `docker compose down -v` |
| Start one service | `docker compose up -d backend` |
| Rebuild + start one service | `docker compose up -d --build backend` |
| Restart service | `docker compose restart backend` |
| Status | `docker compose ps` |
| Live logs | `docker compose logs -f` |
| Logs for one service | `docker compose logs -f backend` |
| Run command inside | `docker compose exec backend bash` |
| Run in temp container | `docker compose run --rm backend python --version` |
| Build from scratch | `docker compose build --no-cache` |
| View final config (debug) | `docker compose config` |

---

## 13. Info / Version

```bash
docker version         # client + server version
docker info            # system info, container/image counts
docker --help
docker <command> --help    # e.g., docker run --help
```

---

## Common Cheat Combos

```bash
# Stop all running containers
docker stop $(docker ps -q)

# Delete all containers (stopped + running)
docker rm -f $(docker ps -aq)

# Delete all images
docker rmi -f $(docker images -q)

# Get image name for a container
docker inspect -f '{{.Config.Image}}' fastapi_backend

# Which container uses port 8000
docker ps --format "{{.Names}} {{.Ports}}" | grep 8000

# Why did a container crash? (exit code + logs)
docker ps -a -f "status=exited"
docker logs --tail=50 <container>
```

---

## 🔍 Debugging "Site not loading"

Follow these 3 commands in order. Do not guess, read the logs.

```bash
# 1. Is the container running?
docker compose ps

# 2. What is the error in the logs? (solves 90% of issues)
docker compose logs --tail=40 backend

# 3. Watch live
docker compose logs -f backend
```

### ⚠️ "Up" does not mean "Working"

`docker compose ps` might show `Up`, but the port could be dead:

```
NAME              STATUS         PORTS
fastapi_backend   Up 2 minutes   0.0.0.0:8000->8000/tcp     <- looks fine
```

But logs show:
```
File "/app/main.py", line 5, in <module>
    from sqlalchemy import func, select, text
ModuleNotFoundError: No module named 'sqlalchemy'
```

**Reason:** In `--reload` mode, uvicorn fails to load the app but the **process stays alive** (waiting for file changes). The container is "Up", but the app never started.

**Conclusion: Do not trust `ps`, always read `logs`.**

### How to read logs

Stack traces are **long**. Read the **last line** — that is where the actual error is. Everything above is internal framework code.

| Last line | Meaning | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'X'` | Package not installed | `docker compose up -d --build <service>` |
| `ImportError: cannot import name 'X' from 'Y'` | Incorrect import | Check file and name |
| `connection refused` / `could not connect` | DB/Redis not ready | Check `healthy` status in `docker compose ps` |
| `SyntaxError` / `IndentationError` | Typo in code | Check file and line number in logs |
| `Address already in use` | Port busy | Find process with `docker ps` |

### Golden rule

> **Changed `requirements.txt` or `package.json` = `--build` required.**
> For frontend, one extra step — `down -v` (due to anonymous volumes).

```bash
docker compose up -d --build backend      # for backend
docker compose down -v && docker compose up --build   # for frontend
```

---

## Troubleshooting

| Problem | Action |
|---|---|
| **`localhost:<port>` not loading** | `docker compose logs --tail=40 <service>` — read the **last line** |
| `ModuleNotFoundError` but file exists | Image not rebuilt — `docker compose up -d --build <service>` |
| Container "Up" but port dead | App crashed, process alive. Check logs |
| `port is already allocated` | Find process with `docker ps`, then `docker stop <container>` |
| `Cannot connect to the Docker daemon` | Docker Desktop is not running — start it |
| `exec: "bash": not found` | Alpine image — use `sh` |
| Container exits immediately `Exited (1)` | Check `docker logs <container>` |
| `no space left on device` | `docker system df` then `docker system prune -a` |
| Code changes not visible | Check volume mount, or `docker compose up -d --build <service>` |
| `Error response... is not running` | Use `docker compose run --rm` instead of `exec` |
| Image not rebuilding | `docker compose build --no-cache <service>` |
| New npm package "not found" | Old anonymous volume — `docker compose down -v` then `up --build`. `down` alone is **not enough** |
| `ERR_MODULE_NOT_FOUND` after build | Same reason — volume masking `node_modules` |
| Port mapping visible but unreachable | Host process (e.g., local PostgreSQL) is using the port. Change host-side port in Compose |

---

## Related

- [postgres-commands.md](postgres-commands.md) — psql, users, queries, backup
- [roadmap.md](../roadmap.md) — project plan
