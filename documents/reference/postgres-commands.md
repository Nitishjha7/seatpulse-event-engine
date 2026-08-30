# PostgreSQL Commands — Full Reference

Database dekhne, users banane, aur queries chalane ka cheatsheet.

**Is project ki DB details:**

| Field | Value |
|---|---|
| Host (host machine se) | `localhost` |
| **Port (host machine se)** | **`5433`** — 5432 nahi! (wajah [neeche](#-port-5433-kyu-5432-nahi)) |
| Host (container se) | `db` |
| Port (container se) | `5432` |
| Database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

---

## 1. DB me ghusne ke 3 tarike

### A. psql — container ke andar (kuch install nahi karna)

```bash
docker compose exec db psql -U seatpulse -d seatpulse
```

Prompt aisa dikhega: `seatpulse=#`

> Ye sabse aasan hai — psql pehle se container me hota hai.

### B. Ek hi command, andar gaye bina

```bash
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```

`-c` = "ye command chalao aur bahar aa jao". Script me ya quick check me kaam aata hai.

### C. pgAdmin / DBeaver (GUI)

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5433` |
| Maintenance database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

**pgAdmin steps:** Servers pe right-click → *Register* → *Server* → **General** tab me naam `SeatPulse (Docker)` → **Connection** tab me upar wali details → Save.

Tables yahan milenge:
```
Servers → SeatPulse (Docker) → Databases → seatpulse → Schemas → public → Tables
```

> Naam me "(Docker)" zaroor likhna — warna tumhare local PostgreSQL se confuse ho jaoge.

---

## 2. psql ke Meta-Commands (backslash wale)

Ye SQL nahi hain — psql ke apne shortcuts hain. **Inme semicolon nahi lagta.**

| Command | Kaam |
|---|---|
| `\l` | Saare databases |
| `\c dbname` | Dusre database me switch karo |
| `\dt` | Saari tables |
| `\dt+` | Tables + size bhi |
| `\d tablename` | Table ka poora structure — columns, indexes, constraints |
| `\d+ tablename` | Aur zyada detail |
| `\du` | Saare users/roles |
| `\di` | Saare indexes |
| `\dn` | Saare schemas |
| `\df` | Saare functions |
| `\x` | Expanded view on/off (chaudi tables padhne ke liye) |
| `\timing` | Har query ka time dikhao |
| `\?` | Saare meta-commands ki list |
| `\h CREATE TABLE` | Kisi SQL command ki help |
| `\q` | Bahar |

> ⚠️ **SQL queries me `;` zaroori hai**, meta-commands me nahi. `SELECT * FROM seats` bina semicolon ke chalegi hi nahi — psql agli line ka wait karta rahega.

---

## 3. User aur Database banana (terminal se)

### Superuser ke taur pe ghuso

```bash
docker compose exec db psql -U seatpulse -d postgres
```

> Local (non-Docker) Postgres me `psql -U postgres` karo.

### Naya user banao

```sql
-- Simple user
CREATE USER analyst WITH PASSWORD 'strong_password_here';

-- Database bana sakne wala user
CREATE USER dev_user WITH PASSWORD 'pass123' CREATEDB;

-- Superuser (sab kuch kar sakta hai — soch ke dena)
CREATE USER admin_user WITH PASSWORD 'pass123' SUPERUSER CREATEDB CREATEROLE;
```

> `CREATE USER` aur `CREATE ROLE` almost same hain. Farak: `CREATE USER` ko login permission default milti hai, `CREATE ROLE` ko nahi.

### Naya database banao

```sql
CREATE DATABASE myapp;
CREATE DATABASE myapp OWNER dev_user;
```

### Permissions do (grant)

```sql
-- Poore database pe
GRANT ALL PRIVILEGES ON DATABASE seatpulse TO analyst;

-- Sirf padhne ki permission (reporting user ke liye)
\c seatpulse
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;

-- Aage banne wali tables pe bhi apne aap mile
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst;
```

> ⚠️ Aakhri line important hai. Bina iske, naya table banane par `analyst` ko uspe permission **nahi** milegi aur "permission denied" aayega.

### User badlo / hatao

```sql
ALTER USER analyst WITH PASSWORD 'naya_password';
ALTER USER analyst WITH SUPERUSER;
ALTER USER analyst WITH NOSUPERUSER;

REVOKE ALL PRIVILEGES ON DATABASE seatpulse FROM analyst;
DROP USER analyst;
```

> `DROP USER` fail hoga agar us user ke paas kuch objects hain. Pehle `REASSIGN OWNED BY analyst TO seatpulse;` phir `DROP OWNED BY analyst;` karo.

### Sab dekho

```sql
\du                                    -- saare users + unke roles
SELECT current_user, current_database();
SELECT usename FROM pg_user;
```

### Shell se seedha (psql ke andar gaye bina)

```bash
docker compose exec db createuser -U seatpulse --pwprompt analyst
docker compose exec db createdb -U seatpulse -O analyst myapp
docker compose exec db dropdb -U seatpulse myapp
```

---

## 4. Is project ki common queries

```sql
-- Saari tables
\dt

-- Seats ka structure (constraints yahan dikhenge)
\d seats

-- Kitni seats kis status me
SELECT status, count(*) FROM seats GROUP BY status;

-- Pehli 10 seats
SELECT id, row_label, seat_number, price, status, version
FROM seats ORDER BY id LIMIT 10;

-- Ek row ki saari seats
SELECT * FROM seats WHERE row_label = 'A' ORDER BY seat_number;

-- Events
SELECT id, name, venue, starts_at, total_seats FROM events;

-- Bookings (user aur seat ke naam ke saath)
SELECT b.id, u.email, s.row_label || '-' || s.seat_number AS seat, b.status, b.amount
FROM bookings b
JOIN users u ON u.id = b.user_id
JOIN seats s ON s.id = b.seat_id;

-- Locked seats jinka time nikal gaya (Phase 4 me kaam aayegi)
SELECT id, row_label, seat_number, locked_by, locked_until
FROM seats
WHERE status = 'locked' AND locked_until < now();

-- Sab seats wapas available (testing ke liye reset)
UPDATE seats SET status = 'available', locked_by = NULL, locked_until = NULL, version = version + 1;

-- Saari bookings hatao (testing reset)
DELETE FROM bookings;
```

---

## 5. Constraints verify karna

Phase 2 ka asli proof — ye chala ke dekho:

```sql
\d seats
```

Neeche ye dikhna chahiye:
```
Indexes:
    "uq_seat_position" UNIQUE CONSTRAINT, btree (event_id, row_label, seat_number)
    "ix_seat_event_status" btree (event_id, status)
Check constraints:
    "ck_seat_status" CHECK (status::text = ANY (ARRAY['available', 'locked', 'booked']))
```

```sql
\d bookings
```
```
Indexes:
    "uq_one_confirmed_booking_per_seat" UNIQUE, btree (seat_id) WHERE status = 'confirmed'
```

### Constraint ko tod ke dekho (asli test)

```sql
-- Duplicate seat — fail hona chahiye
INSERT INTO seats (event_id, row_label, seat_number, price, status, version)
VALUES (1, 'A', 1, 100, 'available', 0);
```
Expected:
```
ERROR:  duplicate key value violates unique constraint "uq_seat_position"
```

```sql
-- Galat status — fail hona chahiye
UPDATE seats SET status = 'Booked' WHERE id = 1;
```
Expected:
```
ERROR:  new row for relation "seats" violates check constraint "ck_seat_status"
```

**Ye errors aana achhi baat hai** — matlab database khud galat data rok raha hai, application code par bharosa nahi karna pad raha.

---

## 6. Backup aur Restore

```bash
# Poora database ek file me
docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql

# Sirf schema (data ke bina)
docker compose exec -T db pg_dump -U seatpulse --schema-only seatpulse > schema.sql

# Sirf data
docker compose exec -T db pg_dump -U seatpulse --data-only seatpulse > data.sql

# Wapas restore
docker compose exec -T db psql -U seatpulse -d seatpulse < backup.sql
```

> `-T` flag zaroori hai — bina iske Docker TTY attach karta hai aur file me kachra aa jata hai.

### `down -v` ke baad recovery

Docker DB ka data `postgres_data` named volume me hai. `docker compose down -v` use delete kar deta hai.

**Wapas laane ke 3 command:**
```bash
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

> **Local PostgreSQL par koi asar nahi padta.** Wo system pe installed hai, Docker uske paas ja hi nahi sakta. Sirf Docker wala DB (port 5433) delete hota hai.

**Volume gaya ya nahi:**
```bash
docker volume ls -q | grep postgres     # kuch nahi mila = delete ho chuka
```

**Frontend ka `node_modules` volume reset karo bina DB udaye:**
```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

---

## 7. Alembic (migrations)

Schema kabhi haath se mat badalna — hamesha migration se.

```bash
docker compose exec backend alembic revision --autogenerate -m "add column X"
docker compose exec backend alembic upgrade head      # apply
docker compose exec backend alembic downgrade -1      # ek step peeche
docker compose exec backend alembic current           # abhi kaunsi lagi hai
docker compose exec backend alembic history           # saari migrations
```

> Migration file banne ke baad **kholo aur padho**. Autogenerate kabhi-kabhi galat cheez banata hai.

---

## ⚠️ Port 5433 kyu, 5432 nahi?

Is system pe **PostgreSQL already installed hai** (Windows service ki tarah chalta hai) aur wo 5432 le chuka hai.

Docker bhi 5432 maangta to compose me mapping dikh jati thi, par asli port local Postgres ke paas rehta. Nateeja — pgAdmin `localhost:5432` pe **local** Postgres se connect hota, jisme `seatpulse` user hai hi nahi:

```
FATAL: password authentication failed for user "seatpulse"
```

Isliye root `.env` me `POSTGRES_PORT=5433` set hai.

**Kaun sa process port le raha hai, ye check karo (PowerShell):**

```powershell
Get-NetTCPConnection -LocalPort 5432 -State Listen |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    [PSCustomObject]@{ Port = $_.LocalPort; PID = $_.OwningProcess; Process = $p.ProcessName }
  }
```

Output me `postgres` dikha = local Postgres hai, `com.docker.backend` dikha = Docker.

> **Yaad rakho:** ye badlav sirf **host se dekhne** ke liye hai. Backend `db:5432` use karta hai — container network ke andar port hamesha 5432 hi rehta hai. Isliye `POSTGRES_PORT` badalne se backend pe koi asar nahi padta, restart bhi nahi karna padta.

---

## Common Problems

| Problem | Fix |
|---|---|
| `FATAL: password authentication failed for user "seatpulse"` | Galat port pe connect ho rahe ho — pgAdmin me **5433** daalo, 5432 nahi |
| `could not connect to server` / `connection refused` | DB chal raha hai? `docker compose ps` me `db` **healthy** dikhna chahiye |
| `port is already allocated` (compose start pe) | Root `.env` me `POSTGRES_PORT` badal do (5434, 5435...) |
| `relation "seats" does not exist` | Migration nahi chali — `docker compose exec backend alembic upgrade head` |
| `permission denied for table X` | Grant nahi diya — section 3 dekho, aur `ALTER DEFAULT PRIVILEGES` bhi |
| Query chal hi nahi rahi, cursor atka hai | Semicolon `;` bhool gaye. Lagao aur Enter |
| Purana password kaam kar raha hai `.env` badalne ke baad bhi | Volume me purana data hai. `docker compose down -v` (⚠️ **data delete hoga**) |
| Table chaudi hai, padha nahi ja raha | `\x` chala ke expanded mode on karo |
| `psql: command not found` (host pe) | Host pe psql install nahi hai — `docker compose exec db psql ...` use karo |

---

## Related

- [Phase 2 — Postgres + Models](../phases/02-postgres-models.md) — tables ka design aur kyu
- [docker-commands.md](docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — aage kya banana hai
