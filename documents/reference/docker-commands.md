# Docker Commands — Full Reference

General Docker commands ka cheatsheet. Project-specific setup ke liye [Docker setup](../setup/01-docker-setup.md) dekho.

> **Note:** Jahan bhi `<container>` likha hai, wahan container ka **naam** (`fastapi_backend`) ya **ID** (`a1b2c3d4`) dono chalte hain. ID ke pehle 3-4 characters kaafi hote hain agar unique ho.

---

## 1. Containers Dekhna

| Kya karna hai | Command |
|---|---|
| Running containers | `docker ps` |
| Saare containers (stopped bhi) | `docker ps -a` |
| Sirf container IDs | `docker ps -q` |
| Saari IDs (stopped bhi) | `docker ps -aq` |
| Last banaya hua container | `docker ps -l` |
| Size ke saath | `docker ps -s` |

**Output kya batata hai:**

```
CONTAINER ID   IMAGE              COMMAND       CREATED       STATUS          PORTS                    NAMES
a1b2c3d4e5f6   seatpulse-backend  "uvicorn..."  2 hours ago   Up 2 hours      0.0.0.0:8000->8000/tcp   fastapi_backend
```

| Column | Matlab |
|---|---|
| `CONTAINER ID` | Unique ID — commands me use hoti hai |
| `IMAGE` | Kis image se bana hai |
| `COMMAND` | Andar kya chal raha hai |
| `STATUS` | `Up` = chal raha, `Exited (0)` = normally band, `Exited (1)` = crash |
| `PORTS` | `host:container` mapping |
| `NAMES` | Container ka naam |

**Clean output** (sirf kaam ki cheezein):

```bash
docker ps --format "table {{.Names}}\t{{.Image}}\t{{.Status}}\t{{.Ports}}"
```

**Filter karke dekho:**

```bash
docker ps -f "status=running"
docker ps -a -f "status=exited"       # sirf band wale
docker ps -f "name=backend"           # naam me backend ho
docker ps -f "ancestor=python:3.11-slim"   # is image se bane containers
```

---

## 2. Container Start / Stop / Restart

| Kya karna hai | Command |
|---|---|
| Container band karo | `docker stop <container>` |
| Band container chalu karo | `docker start <container>` |
| Restart karo | `docker restart <container>` |
| Force kill (turant) | `docker kill <container>` |
| Pause (freeze) | `docker pause <container>` |
| Unpause | `docker unpause <container>` |

```bash
docker stop fastapi_backend
docker stop fastapi_backend react_frontend     # ek saath multiple
docker stop $(docker ps -q)                    # SAARE running containers band
docker restart react_frontend
```

> `stop` politely band karta hai (10 sec deta hai), `kill` turant maar deta hai. Normally `stop` hi use karo.

**Timeout badhao** (heavy app ko band hone me time lage to):

```bash
docker stop -t 30 fastapi_backend
```

---

## 3. Container Delete Karna

```bash
docker rm <container>                # band container delete
docker rm -f <container>             # chalta hua bhi force delete
docker rm $(docker ps -aq)           # saare stopped containers delete
docker container prune               # saare stopped containers delete (safe tarika)
```

> `rm` sirf container delete karta hai, **image nahi**. Image alag se delete hoti hai (section 4).

---

## 4. Images

| Kya karna hai | Command |
|---|---|
| Saari images | `docker images` |
| IDs ke saath dangling images | `docker images -f "dangling=true"` |
| Sirf IDs | `docker images -q` |
| Image download karo | `docker pull node:20-alpine` |
| Image delete | `docker rmi <image>` |
| Force delete | `docker rmi -f <image>` |
| Unused images delete | `docker image prune` |
| Image ka history (layers) | `docker history <image>` |

**Output:**

```
REPOSITORY          TAG        IMAGE ID       CREATED        SIZE
seatpulse-backend   latest     f1e2d3c4b5a6   2 hours ago    215MB
python              3.11-slim  9a8b7c6d5e4f   3 weeks ago    130MB
```

> `<none>` naam wali images = **dangling** (purane build ke leftovers). `docker image prune` se saaf ho jaati hain.

**Image build karna:**

```bash
docker build -t myapp .                      # current folder ke Dockerfile se
docker build -t myapp:v1 ./backend           # specific folder
docker build --no-cache -t myapp .           # cache ignore karke
```

---

## 5. Container ke andar jaana / command chalana

```bash
docker exec -it <container> bash        # shell kholo (debian/ubuntu based)
docker exec -it <container> sh          # alpine images me
docker exec <container> ls -la          # ek command chalao, bahar hi raho
docker exec -it -u root <container> sh  # root banke jao (permission issues)
```

> Alpine images (`node:20-alpine`) me `bash` nahi hota — `sh` use karo.
> Shell se bahar aane ke liye `exit`.

**Naya temporary container chalao** (existing ko chhede bina):

```bash
docker run --rm -it python:3.11-slim bash        # kaam khatam, container delete
docker run --rm -v "${PWD}:/app" -w /app node:20-alpine npm install
```

| Flag | Matlab |
|---|---|
| `--rm` | Kaam ke baad container auto-delete |
| `-it` | Interactive terminal |
| `-d` | Background me chalao |
| `-v host:container` | Folder mount karo |
| `-w /app` | Container ke andar working directory |
| `-p 8000:8000` | Port mapping (host:container) |
| `-e KEY=value` | Environment variable |
| `--name mera-app` | Container ka naam |

---

## 6. Logs

```bash
docker logs <container>                  # saare logs
docker logs -f <container>               # live logs (Ctrl+C se bahar)
docker logs --tail=50 <container>        # last 50 lines
docker logs -f --tail=50 <container>     # last 50 + live
docker logs --since 10m <container>      # last 10 minute ke
docker logs -t <container>               # timestamp ke saath
```

> Container crash ho gaya? `docker logs <container>` chalao — error wahin milega.

---

## 7. Inspect — Details Nikalna

```bash
docker inspect <container>               # poori details (JSON)
docker inspect <image>                   # image ki details
docker stats                             # live CPU/RAM usage (sab containers)
docker stats <container>                 # ek container ka
docker top <container>                   # andar chal rahe processes
docker port <container>                  # port mappings
docker diff <container>                  # image se kya files change hui
```

**Specific value nikalo:**

```bash
docker inspect -f '{{.State.Status}}' fastapi_backend           # running/exited
docker inspect -f '{{.State.ExitCode}}' fastapi_backend         # crash ka code
docker inspect -f '{{.NetworkSettings.IPAddress}}' fastapi_backend
docker inspect -f '{{.Config.Image}}' fastapi_backend           # kaunsi image
docker inspect -f '{{json .Config.Env}}' fastapi_backend        # env variables
```

---

## 8. Files Copy Karna

```bash
docker cp <container>:/app/main.py ./main.py      # container se host pe
docker cp ./config.json <container>:/app/         # host se container me
docker cp <container>:/app/logs ./logs            # poora folder
```

---

## 9. Volumes (Data)

```bash
docker volume ls                    # saare volumes
docker volume inspect <volume>      # details
docker volume create mera-data      # naya volume
docker volume rm <volume>           # delete
docker volume prune                 # unused volumes delete
```

> Volumes me database ka data rehta hai. Delete karoge to data chala jayega.

---

## 10. Networks

```bash
docker network ls                          # saare networks
docker network inspect <network>           # kaun se containers judey hain
docker network create mera-network         # naya network
docker network connect <network> <container>
docker network disconnect <network> <container>
docker network prune                       # unused networks delete
```

> Compose apna network khud banata hai. Isi wajah se `backend` service ko frontend se `http://backend:8000` naam se call kar sakte ho.

---

## 11. Cleanup — Space Khali Karna

```bash
docker system df              # kitni jagah kis cheez ne li hai
docker container prune        # stopped containers
docker image prune            # dangling images
docker image prune -a         # saari unused images
docker volume prune           # unused volumes
docker network prune          # unused networks
docker system prune           # sab kuch (volumes chhod ke)
docker system prune -a        # sab kuch + unused images
docker system prune -a --volumes    # NUCLEAR — volumes bhi
```

> ⚠️ `prune` commands **saare projects** pe asar karti hain, sirf is project pe nahi.
> `--volumes` wali sabse khatarnaak hai — databases ka data delete ho jayega.

---

## ⚠️ Data Safety — kaunsi command data udati hai

### Safe vs Destructive

| Kaam | Command | Data |
|---|---|---|
| Roz band karna | `docker compose down` | ✅ Safe |
| Restart | `docker compose restart` | ✅ Safe |
| Backend rebuild | `docker compose up -d --build backend` | ✅ Safe |
| Frontend ka `node_modules` reset | `--renew-anon-volumes` (neeche) | ✅ Safe |
| **Sab fresh chahiye** | `docker compose down -v` | ❌ **DB ka data delete** |
| **Sab fresh chahiye** | `docker volume prune` | ❌ Unused volumes delete |
| **Sab fresh chahiye** | `docker system prune -a --volumes` | ❌ Sab kuch, har project ka |

### Local database Docker se alag hai

Confuse mat hona — do alag database ho sakte hain:

| | Kahan rehta hai | `down -v` ka asar |
|---|---|---|
| **Local PostgreSQL** (system pe installed) | Windows service, `C:\Program Files\PostgreSQL\...` | ❌ **Kuch nahi hota.** Docker uske paas ja hi nahi sakta |
| **Docker PostgreSQL** | Named volume (`postgres_data`) | ✅ **Poora delete** |

Docker ke commands sirf Docker ki duniya me chalte hain. Tumhare system pe installed database, uske users aur data ko wo chhoo bhi nahi sakte.

### `node_modules` volume reset karo, DB bachao

Problem: frontend me naya npm package add kiya, purana anonymous volume chipka hua hai — par `down -v` karoge to DB bhi ud jayega.

**Sabse aasan (sirf frontend ke anonymous volumes naye banao):**
```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

**Ya manually:**
```bash
docker compose down
docker volume ls                  # lambe hash wale = anonymous volumes
docker volume rm <hash>
docker compose up -d --build
```

> `--renew-anon-volumes` sirf **anonymous** volumes naye banata hai. `postgres_data` ek **named** volume hai, wo bacha rehta hai.

### Volume gaya ya nahi, check karo

```bash
docker volume ls
docker volume ls -q | grep postgres        # kuch nahi mila = delete ho chuka
```

### Backup lo (asli data ho to)

```bash
docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql
docker compose exec -T db psql -U seatpulse -d seatpulse < backup.sql   # restore
```

> Is project me seed data hai, isliye `down -v` ke baad recovery bas 3 command hai —
> `up -d` → `alembic upgrade head` → `python seed.py`. Isiliye seed script likhi thi.

---

## 12. Docker Compose

| Kya karna hai | Command |
|---|---|
| Start (build karke) | `docker compose up --build` |
| Background me start | `docker compose up -d` |
| Stop | `docker compose down` |
| Stop + volumes delete | `docker compose down -v` |
| Ek service start | `docker compose up -d backend` |
| Ek service rebuild + start | `docker compose up -d --build backend` |
| Restart | `docker compose restart backend` |
| Status | `docker compose ps` |
| Live logs | `docker compose logs -f` |
| Ek service ke logs | `docker compose logs -f backend` |
| Andar command chalao | `docker compose exec backend bash` |
| Temporary container me chalao | `docker compose run --rm backend python --version` |
| Scratch se build | `docker compose build --no-cache` |
| Final config dekho (debug) | `docker compose config` |

---

## 13. Info / Version

```bash
docker version         # client + server version
docker info            # system info, kitne containers/images hain
docker --help
docker <command> --help    # jaise: docker run --help
```

---

## Common Cheat Combos

```bash
# Saare running containers band karo
docker stop $(docker ps -q)

# Saare containers delete (stopped + running)
docker rm -f $(docker ps -aq)

# Saari images delete
docker rmi -f $(docker images -q)

# Ek container ki image ka naam pata karo
docker inspect -f '{{.Config.Image}}' fastapi_backend

# Kaunsa container port 8000 use kar raha hai
docker ps --format "{{.Names}} {{.Ports}}" | grep 8000

# Crash hua container kis wajah se? (exit code + logs)
docker ps -a -f "status=exited"
docker logs --tail=50 <container>
```

---

## 🔍 "Site khul hi nahi rahi" — debug ka sahi tarika

Har baar yahi 3 command, isi order me. Guess mat karo, logs padho.

```bash
# 1. Container chal raha hai ya nahi?
docker compose ps

# 2. Logs me error kya hai? (ye 90% baar jawab de deta hai)
docker compose logs --tail=40 backend

# 3. Live dekhna ho to
docker compose logs -f backend
```

### ⚠️ "Up" dikhne ka matlab "kaam kar raha hai" nahi hota

`docker compose ps` me `Up` dikh raha ho, phir bhi port dead ho sakta hai:

```
NAME              STATUS         PORTS
fastapi_backend   Up 2 minutes   0.0.0.0:8000->8000/tcp     <- dikhne me theek
```

Par logs me:
```
File "/app/main.py", line 5, in <module>
    from sqlalchemy import func, select, text
ModuleNotFoundError: No module named 'sqlalchemy'
```

**Wajah:** `--reload` mode me uvicorn app load karne me fail hota hai par **process zinda rehta hai** (wo file changes ka wait karta rehta hai). Container "Up" hai, app chalu hi nahi hua.

**Isliye: `ps` par bharosa mat karo, hamesha `logs` padho.**

### Logs padhne ka tareeka

Stack trace **lamba** hota hai. **Sabse aakhri line** padho — asli error wahin hoti hai. Upar ka sab uvicorn ka internal code hai, uska koi matlab nahi.

| Aakhri line | Matlab | Fix |
|---|---|---|
| `ModuleNotFoundError: No module named 'X'` | Package install nahi hua | `docker compose up -d --build <service>` |
| `ImportError: cannot import name 'X' from 'Y'` | Code me galat import | Wo file kholo, naam check karo |
| `connection refused` / `could not connect` | DB/Redis ready nahi | `docker compose ps` me `healthy` dekho |
| `SyntaxError` / `IndentationError` | Code me typo | File aur line number logs me likha hai |
| `Address already in use` | Port busy hai | `docker ps` se dekho kaun use kar raha |

### Golden rule

> **`requirements.txt` ya `package.json` badla = `--build` chahiye.**
> Frontend me ek qadam aur — `down -v` bhi (anonymous volume ki wajah se).

```bash
docker compose up -d --build backend      # backend ke liye
docker compose down -v && docker compose up --build   # frontend ke liye
```

---

## Troubleshooting

| Problem | Kya karo |
|---|---|
| **`localhost:<port>` khul hi nahi raha** | `docker compose logs --tail=40 <service>` — stack trace ki **aakhri line** padho |
| `ModuleNotFoundError` / package "not found" par file me hai | Image rebuild nahi hua — `docker compose up -d --build <service>` |
| Container "Up" hai par port dead hai | App crash ho chuka hai, process zinda hai. Logs hi batayenge |
| `port is already allocated` | `docker ps` se dekho kaun use kar raha, phir `docker stop <container>` |
| `Cannot connect to the Docker daemon` | Docker Desktop start nahi hai — chalu karo |
| `exec: "bash": not found` | Alpine image hai — `sh` use karo |
| Container turant `Exited (1)` ho jata hai | `docker logs <container>` me error dekho |
| `no space left on device` | `docker system df` phir `docker system prune -a` |
| Code change container me nahi dikh raha | Volume mount check karo, ya `docker compose up -d --build <service>` |
| `Error response... is not running` | `exec` ki jagah `docker compose run --rm` use karo |
| Image dubara build hi nahi ho rahi | `docker compose build --no-cache <service>` |
| Naya npm package `package.json` me hai par container me "not found" | Purana anonymous volume chipka hua hai — `docker compose down -v` phir `up --build`. Sirf `down` kaafi **nahi** |
| `ERR_MODULE_NOT_FOUND` build successful hone ke baad bhi | Same wajah — volume ne naye image ka `node_modules` dhak diya |
| Port mapping dikhti hai par connect nahi ho raha | Host pe koi aur process wo port le chuka hai (jaise local PostgreSQL). Compose me host-side port badal do |

---

## Related

- [postgres-commands.md](postgres-commands.md) — psql, users, queries, backup
- [roadmap.md](../roadmap.md) — project ka plan
