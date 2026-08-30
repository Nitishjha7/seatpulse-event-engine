# Phase 2 — PostgreSQL + SQLAlchemy Models

[Phase 1 — Frontend ↔ Backend](01-frontend-backend-connect.md) ke baad ka kaam.

> ⚠️ **Ye poore project ka sabse important phase hai.** Bullet 2 ("overselling prevention") ka poora daawa in tables ke design par tikta hai — Redis par nahi. Yahan galti hui to Phase 4 me Redis lagane se bhi nahi bachega.

**Kya banega:** Postgres container, 4 tables, migrations, aur 100 seats ka test data.

---

## Concept — Overselling kaise rukega (ye pehle samajh lo)

Teen layer hain. Upar wali sabse tez, neeche wali sabse pakki:

| # | Layer | Kab | Kaam |
|---|---|---|---|
| 1 | Redis lock | Phase 4 | **Speed** — 5000 me se 4999 request DB tak pahunchti hi nahi |
| 2 | `version` column | **Phase 2** | **Detection** — do parallel update me ek fail hoga |
| 3 | UNIQUE constraint | **Phase 2** | **Guarantee** — code me bug ho to bhi DB duplicate nahi hone dega |

Phase 2 me layer **2 aur 3** ban rahi hain. Ye asli safety hain. Redis sirf inke upar ka speed layer hai.

**Interview ka jawab:** "Sirf Redis se karta to Redis restart hone par overselling ho sakti thi. Sirf DB se karta to har request DB pe load daalti. Dono ek saath — Redis fast rejection ke liye, DB correctness ke liye."

---

## Step 1 — Root `.env` banao (compose ke liye)

Postgres ka username/password compose ko chahiye. Root me `.env.example`:

```
POSTGRES_USER=seatpulse
POSTGRES_PASSWORD=seatpulse_dev_password
POSTGRES_DB=seatpulse
POSTGRES_PORT=5432
```

Copy karke `.env` banao:

**PowerShell**
```powershell
Copy-Item .env.example .env
```

**Git Bash**
```bash
cp .env.example .env
```

> Ab teen `.env` files hain — root (compose ke liye), `backend/`, `frontend/`. Teeno alag kaam ke liye hain, teeno gitignored hain.

---

## Step 2 — `docker-compose.yml` me Postgres add karo

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
    # ... pehle jaisa ...
    environment:
      DATABASE_URL: postgresql+psycopg2://${POSTGRES_USER}:${POSTGRES_PASSWORD}@db:5432/${POSTGRES_DB}
    depends_on:
      db:
        condition: service_healthy

volumes:
  postgres_data:
```

| Line | Kyu |
|---|---|
| `postgres_data:/var/lib/postgresql/data` | **Named volume.** Iske bina `docker compose down` pe poora database ud jayega. Iske saath data bacha rehta hai |
| `healthcheck` + `pg_isready` | Postgres "start" hone aur "connections lene" me farak hai. Bina iske backend pehle start ho jata hai aur "connection refused" se crash hota hai |
| `condition: service_healthy` | Phase 0 wala simple `depends_on` sirf **start order** deta tha. Ye actually **ready** hone ka wait karta hai |
| `@db:5432` | `db` = compose service ka naam. **`localhost` yahan kaam nahi karega** — wo backend container ko khud ko point karta |
| `${POSTGRES_PORT}:5432` | Host pe expose, taki pgAdmin/DBeaver se connect kar sako |

> `postgresql+psycopg2://` — `+psycopg2` batata hai ki kaunsa driver use karna hai.

---

## Step 3 — Packages add karo

`backend/requirements.txt`:

```
fastapi>=0.110.0
uvicorn[standard]>=0.28.0
pydantic-settings>=2.2.0
sqlalchemy>=2.0.30
psycopg2-binary>=2.9.9
alembic>=1.13.0
```

| Package | Kaam |
|---|---|
| `sqlalchemy` | Python classes ↔ SQL tables. Raw SQL nahi likhna padta |
| `psycopg2-binary` | Asli Postgres driver. `-binary` = compile kiya hua, install fast |
| `alembic` | Migrations — schema change ka version control |

---

## Step 4 — `config.py` me DB settings

```python
DATABASE_URL: str = "postgresql+psycopg2://seatpulse:seatpulse_dev_password@db:5432/seatpulse"
DB_ECHO: bool = False
```

> Default value isliye hai ki `.env` na ho to bhi app chale. Docker me compose ka `environment:` isko override kar dega — **environment variable ki priority `.env` file se zyada hoti hai**.

---

## Step 5 — `backend/database.py` banao

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

| Cheez | Kyu |
|---|---|
| `pool_pre_ping=True` | Connection use karne se pehle check karo ki zinda hai. Bina iske DB restart hone par "stale connection" errors aate hain |
| `pool_size=10, max_overflow=20` | Max 30 parallel connections. **Phase 6 me 500 users aayenge — tab ye numbers matter karenge** |
| `autoflush=False` | SQLAlchemy khud-b-khud DB me na bheje. Phase 4 me locking ke waqt ye control chahiye |
| `get_db()` generator | `finally: db.close()` — request error se marey tab bhi session band ho. Warna connections leak hote hain aur pool khatam ho jata hai |

---

## Step 6 — `backend/models.py` — sabse important file

Poora code: [../backend/models.py](../../backend/models.py)

### Tables

| Table | Kya rakhta hai |
|---|---|
| `users` | id, email (unique), hashed_password, full_name |
| `events` | id, name, venue, starts_at, total_seats |
| `seats` | id, event_id, row_label, seat_number, price, **status**, **version**, locked_by, locked_until |
| `bookings` | id, user_id, seat_id, event_id, status, amount |

### `Seat` ke teen critical parts

**1. `version` — optimistic locking**

```python
version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
```

Kaise kaam karta hai:

```
User A                          User B
─────────────────────────────────────────────────
version=3 padha               version=3 padha
UPDATE ... WHERE version=3    UPDATE ... WHERE version=3
✅ jeeta, version ab 4         ❌ WHERE match nahi -> rowcount 0
                                 -> 409 Conflict
```

Isko "**optimistic**" isliye kehte hain kyunki hum row ko **lock nahi karte** (jo dheema hota hai). Bas maan ke chalte hain ki clash kam hoga — aur clash hone par **detect** kar lete hain.

**2. Unique seat position**

```python
UniqueConstraint("event_id", "row_label", "seat_number", name="uq_seat_position")
```

Ek event me ek hi "A-12" ho sakti hai. Seed script me bug ho ya API me — duplicate seat ban hi nahi sakti.

**3. Status check constraint**

```python
CheckConstraint("status IN ('available', 'locked', 'booked')", name="ck_seat_status")
```

Typo (`"Booked"`, `"bookd"`) database khud reject kar dega.

### `Booking` ka aakhri taala — partial unique index

```python
Index(
    "uq_one_confirmed_booking_per_seat",
    "seat_id",
    unique=True,
    postgresql_where=text("status = 'confirmed'"),
)
```

**Ye sabse strong guarantee hai.** Ek seat ki sirf **ek confirmed** booking ho sakti hai.

- Redis down ho jaye → phir bhi safe
- `version` check me bug ho → phir bhi safe
- Do backend server ek saath chalein → phir bhi safe

Postgres `IntegrityError` dega, jise hum Phase 4 me **409** me badal denge.

**"Partial" kyu:** condition sirf `status = 'confirmed'` par lagti hai. Isliye booking **cancel** hone ke baad wahi seat dubara bik sakti hai — cancelled rows par ye index lagu hi nahi hota.

---

## Step 7 — Alembic setup

Do file chahiye: `alembic.ini` aur `alembic/env.py`

### `alembic.ini` me ye zaroori hai

```ini
[alembic]
script_location = alembic
prepend_sys_path = .
file_template = %%(year)d_%%(month).2d_%%(day).2d_%%(hour).2d%%(minute).2d-%%(rev)s_%%(slug)s

# Jaan-boojh ke khali — URL env.py me settings se aata hai
sqlalchemy.url =
```

| Line | Kyu |
|---|---|
| `sqlalchemy.url =` khali | **Password is file me kabhi mat likhna** — ye Git me jati hai. URL `env.py` me settings se aayega |
| `file_template` | Migration ka naam date ke saath: `2026_08_10_1430-abc123_add_seats.py`. Default sirf random hash hota hai, history padhna mushkil |

### `alembic/env.py` me do line critical hain

```python
import models  # noqa: F401     <- bina iske Alembic ko tables dikhti hi nahi

config.set_main_option("sqlalchemy.url", settings.DATABASE_URL)
target_metadata = Base.metadata
```

Aur `context.configure()` me:
```python
compare_type=True,              # column ka type badla to detect karo
compare_server_default=True,    # default value badli to detect karo
```

> `import models` bhool jana Alembic ki **sabse common galti** hai — wo khali migration bana deta hai aur samajh nahi aata kyu.

### Folder structure

```
backend/
├── alembic.ini
└── alembic/
    ├── env.py
    ├── script.py.mako
    └── versions/          <- migrations yahan banengi
```

---

## Step 8 — Rebuild karo

`requirements.txt` badla hai, isliye **`--build` zaroori hai**. Sirf `up -d` se naye packages install nahi honge.

```bash
docker compose down
docker compose up --build -d
```

`docker compose ps` me teen containers dikhne chahiye: `seatpulse_db`, `fastapi_backend`, `react_frontend`.

DB healthy hone me 5-10 second lagte hain — backend uska wait karega (healthcheck ki wajah se).

### ⚠️ `--build` bhool gaye to ye hota hai

`localhost:8000` khulta hi nahi, aur logs me:

```
File "/app/main.py", line 5, in <module>
    from sqlalchemy import func, select, text
ModuleNotFoundError: No module named 'sqlalchemy'
```

**Confusing part:** `docker compose ps` me backend **"Up"** dikhega, phir bhi port kaam nahi karega. Wajah — `--reload` mode me uvicorn crash hone ke baad bhi container zinda rehta hai, bas app load nahi hota.

**Fix:**
```bash
docker compose up -d --build backend
```

> **Rule:** `requirements.txt` ya `package.json` badla = `--build` chahiye.
> Frontend me ek qadam aur — `down -v` bhi (anonymous volume ki wajah se, Phase 1 me dekha tha).

---

## Step 9 — Pehli migration banao

```bash
docker compose exec backend alembic revision --autogenerate -m "initial tables"
```

Ye `backend/alembic/versions/` me ek file banayega.

> ⚠️ **File kholo aur padho.** Autogenerate bewakoof hai — kabhi kabhi galat cheez generate karta hai. Usme `create_table('users')`, `create_table('events')`, `create_table('seats')`, `create_table('bookings')` dikhna chahiye.

Migration apply karo:

```bash
docker compose exec backend alembic upgrade head
```

| Command | Kaam |
|---|---|
| `alembic revision --autogenerate -m "msg"` | Models aur DB compare karke migration file banao |
| `alembic upgrade head` | Saari pending migrations apply karo |
| `alembic downgrade -1` | Ek migration undo karo |
| `alembic current` | Abhi kaunsi migration lagi hai |
| `alembic history` | Saari migrations ki list |

---

## Step 10 — Seed data daalo

```bash
docker compose exec backend python seed.py
```

Output:
```
✅ Demo user banaya
✅ Event banaya (id=1)
✅ 100 seats banayi

🎉 Seed complete
```

Script dubara chalao to duplicate nahi banega — pehle check karta hai.

---

## ✅ Proof — chala ya nahi?

**1. Seats count**
```bash
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```
→ `100`

**2. API se**

http://localhost:8000/api/stats
```json
{
  "events": 1,
  "seats_total": 100,
  "seats_by_status": { "available": 100 }
}
```

**3. Health me database**

http://localhost:8000/api/health → `"database": "connected"`

Browser me http://localhost:5173 — card me ab **Database: connected** bhi dikhega.

**4. Asli test — constraint kaam kar raha hai?**

```bash
docker compose exec db psql -U seatpulse -d seatpulse -c \
  "INSERT INTO seats (event_id, row_label, seat_number, price, status, version) VALUES (1, 'A', 1, 100, 'available', 0);"
```

Ye **fail** hona chahiye:
```
ERROR: duplicate key value violates unique constraint "uq_seat_position"
```

**Yehi Phase 2 ka asli proof hai.** Ye error matlab database khud duplicate rok raha hai — application code par bharosa nahi karna pad raha.

**5. Data persist ho raha hai?**
```bash
docker compose restart db
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```
→ phir bhi `100`. Named volume kaam kar raha hai.

---

## Step 11 — DB ko apne system se dekho (pgAdmin / DBeaver)

Saare psql commands, user banane, grants waqerah ke liye alag file hai:
**→ [postgres-commands.md](../reference/postgres-commands.md)**

Yahan sirf connection ki baat:

| Field | Value |
|---|---|
| Host | `localhost` |
| **Port** | **`5433`** ← 5432 nahi |
| Maintenance database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

**pgAdmin:** Servers pe right-click → *Register* → *Server* → **General** me naam `SeatPulse (Docker)` → **Connection** me upar wali details.

> Naam me "(Docker)" zaroor likhna, taki local PostgreSQL se confuse na ho.

### ⚠️ Port 5433 kyu — ye Phase 2 ka sabse confusing issue hai

Is system pe **PostgreSQL already installed hai** (Windows service ki tarah chalta hai) aur wo 5432 le chuka hai.

Docker ne bhi 5432 maanga tha. `docker compose ps` me mapping dikhti bhi thi:
```
0.0.0.0:5432->5432/tcp
```
**par asli port local Postgres ke paas tha.** pgAdmin `localhost:5432` pe gaya → wahan **local** Postgres mila → usme `seatpulse` user hai hi nahi:

```
FATAL: password authentication failed for user "seatpulse"
```

Error dekh ke lagta hai password galat hai. **Password bilkul sahi tha — DB hi galat tha.**

**Kaun port le raha hai, check karo (PowerShell):**
```powershell
Get-NetTCPConnection -LocalPort 5432 -State Listen |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    [PSCustomObject]@{ Port = $_.LocalPort; PID = $_.OwningProcess; Process = $p.ProcessName }
  }
```

`postgres` dikha = local Postgres hai. `com.docker.backend` dikha = Docker.

**Fix** — root `.env` me:
```
POSTGRES_PORT=5433
```
```bash
docker compose up -d db
```

`docker compose ps` me ab `0.0.0.0:5433->5432/tcp` dikhega.

> **Backend pe iska koi asar nahi padta.** Wo `db:5432` use karta hai — container-to-container network ke andar port hamesha 5432 hi rehta hai. `POSTGRES_PORT` sirf **host se dekhne** ke liye hai. Isiliye backend restart bhi nahi karna padta, aur data bhi safe rehta hai (named volume).

---

## Common Problems

| Problem | Fix |
|---|---|
| **`localhost:8000` khul hi nahi raha** | Sabse pehle logs padho: `docker compose logs --tail=40 backend`. 90% baar wajah neeche wali hai |
| `ModuleNotFoundError: No module named 'sqlalchemy'` (ya koi bhi naya package) | Image rebuild nahi hua — `docker compose up -d --build backend` |
| Backend `docker compose ps` me "Up" hai par port kaam nahi kar raha | App crash ho chuka hai, container zinda hai (`--reload` ki wajah se). Logs hi batayenge |
| `connection refused` / `could not connect to server` | DB ready nahi tha. Healthcheck sahi hai? `docker compose ps` me `db` **healthy** dikhna chahiye |
| **pgAdmin me `password authentication failed for user "seatpulse"`** | Galat port. **5433** use karo, 5432 nahi — Step 11 dekho |
| `password authentication failed` (backend se) | Root `.env` banaya? Purana volume purane password ke saath pada ho to: `docker compose down -v` |
| `port is already allocated` DB start pe | Root `.env` me `POSTGRES_PORT` aur badal do (5434, 5435...) |
| `alembic: command not found` | Rebuild nahi hua — `docker compose up -d --build backend` |
| Migration khali bani (`pass` likha hai) | `env.py` me `import models` missing hai |
| `Target database is not up to date` | `alembic upgrade head` pehle chalao |
| `relation "seats" does not exist` | Migration apply nahi hui — `alembic upgrade head` |
| `Can't locate revision` | `versions/` folder aur DB ka `alembic_version` table match nahi kar rahe. Dev me: `docker compose down -v` se fresh start |
| DB me purana data pada hai, chahiye nahi | `docker compose down -v` → `up -d` → migration → seed |

---

## ⚠️ `down -v` ke baad DB wapas kaise laayein

Phase 2 ke baad **`docker compose down -v` DB ka saara data delete kar deta hai** — seats, events, users, sab.

**Ghabrane ki baat nahi. 3 command me wapas:**

```bash
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

Fresh DB, 100 seats, sab wapas. **Isiliye `seed.py` banayi thi** — data kabhi bhi dubara ban sakta hai, isliye dev me DB udna koi aafat nahi.

### Confuse mat hona — do alag database hain

| | Kahan | `down -v` ka asar |
|---|---|---|
| **Local PostgreSQL 18** (port 5432) | System pe installed, Windows service | ❌ **Kuch nahi hota** |
| **Docker PostgreSQL 16** (port 5433) | `postgres_data` named volume | ✅ **Poora delete** |

Docker ke commands sirf Docker ki duniya me chalte hain. Tumhare system wale Postgres ko wo chhoo bhi nahi sakte.

### Volume gaya ya nahi, check karo

```bash
docker volume ls
docker volume ls -q | grep postgres     # kuch nahi mila = delete ho chuka
```

### Frontend ka volume reset karo, DB bachao

Phase 1 me `node_modules` ke liye `down -v` karte the. **Ab wo mat karna** — DB bhi ud jayega. Iski jagah:

```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

`--renew-anon-volumes` sirf **anonymous** volumes naye banata hai. `postgres_data` ek **named** volume hai — wo bacha rehta hai.

### Safe vs Destructive

| Command | Data |
|---|---|
| `docker compose down` | ✅ Safe |
| `docker compose restart` | ✅ Safe |
| `docker compose up -d --build backend` | ✅ Safe |
| `docker compose up -d --renew-anon-volumes frontend` | ✅ Safe |
| `docker compose down -v` | ❌ **DB delete** |
| `docker volume prune` | ❌ Delete |
| `docker system prune -a --volumes` | ❌ Sab kuch, har project ka |

> Asli data aa jaye to backup: `docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql`
> Detail: [postgres-commands.md](../reference/postgres-commands.md) section 6

---

## Files jo is phase me bane/badle

```
.env                        ← naya (Git me nahi)
.env.example                ← naya
docker-compose.yml          ← update (db service + volume)

backend/
├── database.py             ← naya
├── models.py               ← naya  ⭐ sabse important
├── seed.py                 ← naya
├── alembic.ini             ← naya
├── alembic/
│   ├── env.py              ← naya
│   ├── script.py.mako      ← naya
│   └── versions/           ← migrations yahan
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

`git status` me koi bhi `.env` **nahi** dikhna chahiye (teeno), `.env.example` dikhni chahiye.

---

## Related

- **[postgres-commands.md](../reference/postgres-commands.md)** — psql commands, user/database banana, grants, backup, constraint testing
- [docker-commands.md](../reference/docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — aage kya banana hai

---

**Agla:** [roadmap.md](../roadmap.md) → Phase 3 (Pydantic schemas + CRUD + Seat Grid UI)
