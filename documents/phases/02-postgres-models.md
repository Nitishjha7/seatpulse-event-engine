# Phase 2 — PostgreSQL + SQLAlchemy Models

Follows [Phase 1 — Frontend ↔ Backend](01-frontend-backend-connect.md).

> ⚠️ **This is the most important phase of the entire project.** The entire claim regarding "overselling prevention" (Bullet 2) relies on these table designs, not Redis. If you fail here, adding Redis in Phase 4 will not save you.

**Deliverables:** Postgres container, 4 tables, migrations, and 100-seat test data.

---

## Concept — How to prevent overselling (understand this first)

There are three layers. The top one is the fastest, the bottom one is the most reliable:

| # | Layer | When | Purpose |
|---|---|---|---|
| 1 | Redis lock | Phase 4 | **Speed** — 4999 out of 5000 requests never reach the DB |
| 2 | `version` column | **Phase 2** | **Detection** — one of two parallel updates will fail |
| 3 | UNIQUE constraint | **Phase 2** | **Guarantee** — even with a code bug, the DB prevents duplicates |

Phase 2 implements layers **2 and 3**. These are the true safety mechanisms. Redis is merely a speed layer on top of them.

**Interview Answer:** "If I used only Redis, overselling could occur during a Redis restart. If I used only the DB, every request would load the DB. I use both: Redis for fast rejection, and the DB for correctness."

---

## Step 1 — Create root `.env` (for compose)

Compose requires the Postgres username/password. Create `.env.example` in the root:

```
POSTGRES_USER=seatpulse
POSTGRES_PASSWORD=seatpulse_dev_password
POSTGRES_DB=seatpulse
POSTGRES_PORT=5432
```

Copy it to create `.env`:

**PowerShell**
```powershell
Copy-Item .env.example .env
```

**Git Bash**
```bash
cp .env.example .env
```

> There are now three `.env` files — root (for compose), `backend/`, and `frontend/`. They serve different purposes and are all gitignored.

---

## Step 2 — Add Postgres to `docker-compose.yml`

```yaml
services:
  db:
    image: postgres:16-alpine
    container_name: seatpulse_db
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
    ports:
      - "${POSTGRES_PORT}:5432"
    volumes:
      - postgres_data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 5

  backend:
    # ... same as before ...
    environment:
      DATABASE_URL: postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres_data:
```

| Line | Why |
|---|---|
| `postgres_data:/var/lib/postgresql/data` | **Named volume.** Without this, `docker compose down` wipes the entire database. This persists the data. |
| `healthcheck` + `pg_isready` | There is a difference between Postgres "starting" and "accepting connections." Without this, the backend starts too early and crashes with "connection refused." |
| `condition: service_healthy` | The simple `depends_on` from Phase 0 only controlled **start order**. This waits until it is actually **ready**. |
| `@db:5432` | `db` = compose service name. **`localhost` will not work here** — it would point the backend container to itself. |
| `${POSTGRES_PORT}:5432` | Expose to host so you can connect via pgAdmin/DBeaver. |

> `postgresql+psycopg2://` — `+psycopg2` specifies the driver to use.

---

## Step 3 — Add packages

`backend/requirements.txt`:

```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
pydantic-settings>=2.2.0
sqlalchemy>=2.0.30
psycopg2-binary>=2.9.9
alembic>=1.13.0
```

| Package | Purpose |
|---|---|
| `sqlalchemy` | Python classes ↔ SQL tables. Eliminates raw SQL. |
| `psycopg2-binary` | The actual Postgres driver. `-binary` = pre-compiled, installs fast. |
| `alembic` | Migrations — version control for schema changes. |

---

## Step 4 — DB settings in `config.py`

```python
DATABASE_URL: str = "postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse"
DB_ECHO: bool = False
```

> The default value allows the app to run without an `.env` file. In Docker, the compose `environment:` will override this — **environment variables have higher priority than `.env` files.**

---

## Step 5 — Create `backend/database.py`

```python
from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from config import settings

engine = create_engine(
    settings.DATABASE_URL,
    echo=settings.DB_ECHO,
    pool_pre_ping=True,
    pool_size=10,
    max_overflow=20,
)

SessionLocal = sessionmaker(bind=engine, autocommit=False, autoflush=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
```

| Item | Why |
|---|---|
| `pool_pre_ping=True` | Check if the connection is alive before using it. Prevents "stale connection" errors after DB restarts. |
| `pool_size=10, max_overflow=20` | Max 30 parallel connections. **These numbers will matter when 500 users arrive in Phase 6.** |
| `autoflush=False` | Prevents SQLAlchemy from automatically pushing to the DB. Needed for locking control in Phase 4. |
| `get_db()` generator | `finally: db.close()` — ensures the session closes even if the request fails. Otherwise, connections leak and the pool exhausts. |

---

## Step 6 — `backend/models.py` — the most important file

Full code: [../backend/models.py](../../backend/models.py)

### Tables

| Table | Contents |
|---|---|
| `users` | id, email (unique), hashed_password, full_name |
| `events` | id, name, venue, starts_at, total_seats |
| `seats` | id, event_id, row_label, seat_number, price, **status**, **version**, locked_by, locked_until |
| `bookings` | id, user_id, seat_id, event_id, status, amount |

### Three critical parts of `Seat`

**1. `version` — optimistic locking**

```python
version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```

How it works:

```
User A                          User B
─────────────────────────────────────────────────
Read version=3                Read version=3
UPDATE ... WHERE version=3    UPDATE ... WHERE version=3
✅ Success, version is now 4   ❌ WHERE match fails -> rowcount 0
                                 -> 409 Conflict
```

This is called "**optimistic**" because we **do not lock** the row (which is slow). We assume clashes are rare — and **detect** them if they occur.

**2. Unique seat position**

```python
UniqueConstraint("event_id", "row_label", "seat_number", name="uq_seat_position")
```

Only one "A-12" can exist per event. Whether there is a bug in the seed script or the API — duplicate seats cannot be created.

**3. Status check constraint**

```python
CheckConstraint("status IN ('available', 'locked', 'booked')", name="ck_seat_status")
```

The database will reject typos (`"Booked"`, `"bookd"`) automatically.

### The final lock for `Booking` — partial unique index

```python
Index(
    "uq_one_confirmed_booking_per_seat",
    "seat_id",
    unique=True,
    postgresql_where=text("status = 'confirmed'"),
)
```

**This is the strongest guarantee.** A seat can have only **one confirmed** booking.

- If Redis goes down → still safe
- If there is a bug in the `version` check → still safe
- If two backend servers run simultaneously → still safe

Postgres will throw an `IntegrityError`, which we will convert to a **409** in Phase 4.

**Why "partial":** The condition only applies to `status = 'confirmed'`. Therefore, a seat can be sold again after a booking is **cancelled** — this index does not apply to cancelled rows.

---

## Step 7 — Alembic setup

Two files required: `alembic.ini` and `alembic/env.py`

### Required in `alembic.ini`

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s

# Intentionally empty — URL comes from settings in env.py
sqlalchemy.url =
```

| Line | Why |
|---|---|
| `sqlalchemy.url =` empty | **Never write the password in this file** — it goes into Git. The URL comes from settings in `env.py`. |
| `file_template` | Migration name with date: `2026_08_10_1430-abc123_add_seats.py`. Default is just a random hash, making history hard to read. |

### Two critical lines in `alembic/env.py`

```python
import models  # noqa: F401     <- Alembic cannot see tables without this

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

And in `context.configure()`:
```python
compare_type=True,              # detect column type changes
compare_server_default=True,    # detect default value changes
```

> Forgetting `import models` is the **most common Alembic mistake** — it generates an empty migration and you won't know why.

### Folder structure

```
backend/
├── alembic.ini
└── alembic/
    ├── env.py
    ├── script.py.mako
    └── versions/          <- migrations generated here
```

---

## Step 8 — Rebuild

Since `requirements.txt` changed, **`--build` is mandatory**. `up -d` alone will not install new packages.

```bash
docker compose down
docker compose up --build -d
```

`docker compose ps` should show three containers: `seatpulse_db`, `fastapi_backend`, `react_frontend`.

It takes 5-10 seconds for the DB to become healthy — the backend will wait for it (due to the healthcheck).

### ⚠️ What happens if you forget `--build`

`localhost:8000` won't open, and logs will show:

```
File "/app/main.py", line 5, in <module>
    from sqlalchemy import func, select, text
ModuleNotFoundError: No module named 'sqlalchemy'
```

**Confusing part:** The backend will appear **"Up"** in `docker compose ps`, but the port won't work. Reason — uvicorn stays alive in `--reload` mode even after crashing, but the app fails to load.

**Fix:**
```bash
docker compose up -d --build backend
```

> **Rule:** Changed `requirements.txt` or `package.json` = `--build` required.
> For frontend, one extra step — `down -v` as well (due to anonymous volumes, seen in Phase 1).

---

## Step 9 — Create first migration

```bash
docker compose exec backend alembic revision --autogenerate -m "initial tables"
```

This creates a file in `backend/alembic/versions/`.

> ⚠️ **Open and read the file.** Autogenerate is not perfect — it sometimes generates incorrect code. It should show `create_table('users')`, `create_table('events')`, `create_table('seats')`, and `create_table('bookings')`.

Apply the migration:

```bash
docker compose exec backend alembic upgrade head
```

| Command | Purpose |
|---|---|
| `alembic revision --autogenerate -m "msg"` | Compare models and DB to create a migration file. |
| `alembic upgrade head` | Apply all pending migrations. |
| `alembic downgrade -1` | Undo one migration. |
| `alembic current` | Show currently applied migration. |
| `alembic history` | List all migrations. |

---

## Step 10 — Seed data

```bash
docker compose exec backend python seed.py
```

Output:
```
✅ Demo user created
✅ Event created (id=1)
✅ 100 seats created

🎉 Seed complete
```

Running the script again won't create duplicates — it checks first.

---

## ✅ Proof — did it work?

**1. Seats count**
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```
→ `100`

**2. Via API**

http://localhost:8000/api/stats
```json
{
  "events": 1,
  "seats_total": 100,
  "seats_by_status": { "available": 100 }
}
```

**3. Database in health check**

http://localhost:8000/api/health → `"database": "connected"`

In the browser, http://localhost:5173 — the card will now show **Database: connected**.

**4. Real test — is the constraint working?**

```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "INSERT INTO seats (event_id, row_label, seat_number, price, status, version) VALUES (1, 'A', 1, 100, 'available', 0);"
```

This should **fail**:
```
ERROR: duplicate key value violates unique constraint "uq_seat_position"
```

**This is the true proof of Phase 2.** This error means the database is preventing duplicates itself — we don't have to rely on application code.

**5. Is data persisting?**
```bash
docker compose restart db
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```
→ still `100`. The named volume is working.

---

## Step 11 — View DB from your system (pgAdmin / DBeaver)

Separate file for psql commands, user creation, grants, etc.:
**→ [postgres-commands.md](../reference/postgres-commands.md)**

Connection details:

| Field | Value |
|---|---|
| Host | `localhost` |
| **Port** | **`5433`** ← not 5432 |
| Maintenance database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

**pgAdmin:** Right-click Servers → *Register* → *Server* → **General** name `SeatPulse (Docker)` → **Connection** details above.

> Include "(Docker)" in the name to avoid confusion with local PostgreSQL.

### ⚠️ Why port 5433 — the most confusing issue in Phase 2

**PostgreSQL is already installed** on this system (as a Windows service) and has claimed 5432.

Docker also requested 5432. The mapping appeared in `docker compose ps`:
```
0.0.0.0:5432->5432/tcp
```
**but the actual port was held by local Postgres.** pgAdmin went to `localhost:5432` → found **local** Postgres → which does not have the `seatpulse` user:

```
FATAL: password authentication failed for user "seatpulse"
```

The error makes it look like the password is wrong. **The password was correct — the DB was wrong.**

**Check who is using the port (PowerShell):**
```powershell
Get-NetTCPConnection -LocalPort 5432 -State Listen |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    [PSCustomObject]@{ Port = $_.LocalPort; PID = $_.OwningProcess; Process = $p.ProcessName }
  }
```

`postgres` shown = local Postgres. `com.docker.backend` shown = Docker.

**Fix** — in root `.env`:
```
POSTGRES_PORT=5433
```
```bash
docker compose up -d db
```

`docker compose ps` will now show `0.0.0.0:5433->5432/tcp`.

> **This has no effect on the backend.** It uses `db:5432` — the port inside the container-to-container network is always 5432. `POSTGRES_PORT` is only for **host access**. Therefore, the backend doesn't need to restart, and data remains safe (named volume).

---

## Common Problems

| Problem | Fix |
|---|---|
| **`localhost:8000` won't open** | Read logs first: `docker compose logs --tail=40 backend`. 90% of the time it's the issue below. |
| `ModuleNotFoundError: No module named 'sqlalchemy'` | Image not rebuilt — `docker compose up -d --build backend` |
| Backend is "Up" but port doesn't work | App crashed, container is alive (due to `--reload`). Logs will tell you why. |
| `connection refused` / `could not connect to server` | DB not ready. Is healthcheck correct? `db` should be **healthy** in `docker compose ps`. |
| **pgAdmin `password authentication failed`** | Wrong port. Use **5433**, not 5432 — see Step 11. |
| `password authentication failed` (from backend) | Did you create root `.env`? If an old volume exists with an old password: `docker compose down -v` |
| `port is already allocated` on DB start | Change `POSTGRES_PORT` in root `.env` (5434, 5435...) |
| `alembic: command not found` | Not rebuilt — `docker compose up -d --build backend` |
| Migration generated empty (`pass` written) | `import models` missing in `env.py` |
| `Target database is not up to date` | Run `alembic upgrade head` first. |
| `relation "seats" does not exist` | Migration not applied — `alembic upgrade head` |
| `Can't locate revision` | `versions/` folder and DB `alembic_version` table mismatch. In dev: `docker compose down -v` for a fresh start. |
| DB has old data, don't want it | `docker compose down -v` → `up -d` → migration → seed |

---

## ⚠️ How to restore DB after `down -v`

After Phase 2, **`docker compose down -v` deletes all DB data** — seats, events, users, everything.

**Don't panic. Restore in 3 commands:**

```bash
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

Fresh DB, 100 seats, all back. **This is why `seed.py` was created** — data can be regenerated at any time, so losing the DB in dev is not a disaster.

### Don't get confused — there are two separate databases

| | Where | Effect of `down -v` |
|---|---|---|
| **Local PostgreSQL 18** (port 5432) | Installed on system, Windows service | ❌ **Nothing happens** |
| **Docker PostgreSQL 16** (port 5433) | `postgres_data` named volume | ✅ **Completely deleted** |

Docker commands only run in the Docker world. They cannot touch your system's Postgres.

### Check if volume is gone

```bash
docker volume ls
docker volume ls -q | grep postgres     # nothing found = deleted
```

### Reset frontend volume, save DB

In Phase 1, we used `down -v` for `node_modules`. **Don't do that now** — it will wipe the DB. Instead:

```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

`--renew-anon-volumes` only recreates **anonymous** volumes. `postgres_data` is a **named** volume — it remains safe.

### Safe vs Destructive

| Command | Data |
|---|---|
| `docker compose down` | ✅ Safe |
| `docker compose restart` | ✅ Safe |
| `docker compose up -d --build backend` | ✅ Safe |
| `docker compose up -d --renew-anon-volumes frontend` | ✅ Safe |
| `docker compose down -v` | ❌ **DB deleted** |
| `docker volume prune` | ❌ Deleted |
| `docker system prune -a --volumes` | ❌ Everything, every project |

> Backup real data: `docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql`
> Detail: [postgres-commands.md](../reference/postgres-commands.md) section 6

---

## Files created/modified in this phase

```
.env                        ← new (not in Git)
.env.example                ← new
docker-compose.yml          ← update (db service + volume)

backend/
├── database.py             ← new
├── models.py               ← new  ⭐ most important
├── seed.py                 ← new
├── alembic.ini             ← new
├── alembic/
│   ├── env.py              ← new
│   ├── script.py.mako      ← new
│   └── versions/           ← migrations here
├── config.py               ← update (DATABASE_URL)
├── main.py                 ← update (DB health + /api/stats)
├── requirements.txt        ← update
└── .env / .env.example     ← update

frontend/src/App.jsx        ← update (Database row)
```

---

## Commit

```bash
git add .
git status
git commit -m "Phase 2: PostgreSQL, SQLAlchemy models, Alembic migrations, seed data"
git push
```

No `.env` files should appear in `git status` (all three), only `.env.example`.

---

## Related

- **[postgres-commands.md](../reference/postgres-commands.md)** — psql commands, creating users/databases, grants, backup, constraint testing
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — what to build next

---

**Next:** [roadmap.md](../roadmap.md) → Phase 3 (Pydantic schemas + CRUD + Seat Grid UI)
