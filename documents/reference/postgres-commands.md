# PostgreSQL Commands — Full Reference

Cheatsheet for viewing databases, creating users, and running queries.

**Project DB details:**

| Field | Value |
|---|---|
| Host (from host machine) | `localhost` |
| **Port (from host machine)** | **`5433`** — not 5432! (reason [below](#-why-port-5433-and-not-5432)) |
| Host (from container) | `db` |
| Port (from container) | `5432` |
| Database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

---

## 1. 3 ways to access the DB

### A. psql — inside the container (no installation required)

```bash
docker compose exec db psql -U seatpulse -d seatpulse
```

The prompt will appear as: `seatpulse=#`

> This is the easiest method — psql is pre-installed in the container.

### B. Single command, without entering the shell

```bash
docker compose exec db psql -U seatpulse -d seatpulse -c "SELECT count(*) FROM seats;"
```

`-c` = "run this command and exit". Useful for scripts or quick checks.

### C. pgAdmin / DBeaver (GUI)

| Field | Value |
|---|---|
| Host | `localhost` |
| Port | `5433` |
| Maintenance database | `seatpulse` |
| Username | `seatpulse` |
| Password | `seatpulse_dev_password` |

**pgAdmin steps:** Right-click *Servers* → *Register* → *Server* → **General** tab, name it `SeatPulse (Docker)` → **Connection** tab, enter the details above → Save.

Tables are located here:
```
Servers → SeatPulse (Docker) → Databases → seatpulse → Schemas → public → Tables
```

> Ensure you include "(Docker)" in the name to avoid confusion with your local PostgreSQL instance.

---

## 2. psql Meta-Commands (backslash commands)

These are not SQL — they are psql shortcuts. **Do not use a semicolon.**

| Command | Description |
|---|---|
| `\l` | List all databases |
| `\c dbname` | Switch to another database |
| `\dt` | List all tables |
| `\dt+` | List tables + size |
| `\d tablename` | Full table structure — columns, indexes, constraints |
| `\d+ tablename` | Detailed structure |
| `\du` | List all users/roles |
| `\di` | List all indexes |
| `\dn` | List all schemas |
| `\df` | List all functions |
| `\x` | Toggle expanded view (for wide tables) |
| `\timing` | Show execution time for each query |
| `\?` | List all meta-commands |
| `\h CREATE TABLE` | Help for a specific SQL command |
| `\q` | Exit |

> ⚠️ **Semicolons `;` are required for SQL queries**, but not for meta-commands. `SELECT * FROM seats` will not execute without a semicolon; psql will simply wait for the next line.

---

## 3. Creating Users and Databases (via terminal)

### Log in as superuser

```bash
docker compose exec db psql -U seatpulse -d postgres
```

> For local (non-Docker) Postgres, use `psql -U postgres`.

### Create a new user

```sql
-- Simple user
CREATE USER analyst WITH PASSWORD 'strong_password_here';

-- User with database creation rights
CREATE USER dev_user WITH PASSWORD 'pass123' CREATEDB;

-- Superuser (full access — grant with caution)
CREATE USER admin_user WITH PASSWORD 'pass123' SUPERUSER CREATEDB CREATEROLE;
```

> `CREATE USER` and `CREATE ROLE` are nearly identical. The difference: `CREATE USER` has login permissions by default, `CREATE ROLE` does not.

### Create a new database

```sql
CREATE DATABASE myapp;
CREATE DATABASE myapp OWNER dev_user;
```

### Grant permissions

```sql
-- On the entire database
GRANT ALL PRIVILEGES ON DATABASE seatpulse TO analyst;

-- Read-only access (for reporting users)
\c seatpulse
GRANT USAGE ON SCHEMA public TO analyst;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO analyst;

-- Automatically grant access to future tables
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO analyst;
```

> ⚠️ The last line is critical. Without it, the `analyst` will not have access to newly created tables, resulting in "permission denied" errors.

### Modify / Remove users

```sql
ALTER USER analyst WITH PASSWORD 'new_password';
ALTER USER analyst WITH SUPERUSER;
ALTER USER analyst WITH NOSUPERUSER;

REVOKE ALL PRIVILEGES ON DATABASE seatpulse FROM analyst;
DROP USER analyst;
```

> `DROP USER` will fail if the user owns objects. First run `REASSIGN OWNED BY analyst TO seatpulse;` then `DROP OWNED BY analyst;`.

### View status

```sql
\du                                    -- all users + roles
SELECT current_user, current_database();
SELECT usename FROM pg_user;
```

### Via shell (without entering psql)

```bash
docker compose exec db createuser -U seatpulse --pwprompt analyst
docker compose exec db createdb -U seatpulse -O analyst myapp
docker compose exec db dropdb -U seatpulse myapp
```

---

## 4. Common project queries

```sql
-- List all tables
\dt

-- Structure of seats (constraints visible here)
\d seats

-- Count seats by status
SELECT status, count(*) FROM seats GROUP BY status;

-- First 10 seats
SELECT id, row_label, seat_number, price, status, version
FROM seats ORDER BY id LIMIT 10;

-- All seats in a specific row
SELECT * FROM seats WHERE row_label = 'A' ORDER BY seat_number;

-- Events
SELECT id, name, venue, starts_at, total_seats FROM events;

-- Bookings (with user email and seat label)
SELECT b.id, u.email, s.row_label || '-' || s.seat_number AS seat, b.status, b.amount
FROM bookings b
JOIN users u ON u.id = b.user_id
JOIN seats s ON s.id = b.seat_id;

-- Locked seats that have expired (Phase 4)
SELECT id, row_label, seat_number, locked_by, locked_until
FROM seats
WHERE status = 'locked' AND locked_until < now();

-- Reset all seats to available (for testing)
UPDATE seats SET status = 'available', locked_by = NULL, locked_until = NULL, version = version + 1;

-- Remove all bookings (for testing)
DELETE FROM bookings;
```

---

## 5. Verifying Constraints

Proof of Phase 2 — run these:

```sql
\d seats
```

You should see:
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

### Test constraint violations

```sql
-- Duplicate seat — should fail
INSERT INTO seats (event_id, row_label, seat_number, price, status, version)
VALUES (1, 'A', 1, 100, 'available', 0);
```
Expected:
```
ERROR:  duplicate key value violates unique constraint "uq_seat_position"
```

```sql
-- Invalid status — should fail
UPDATE seats SET status = 'Booked' WHERE id = 1;
```
Expected:
```
ERROR:  new row for relation "seats" violates check constraint "ck_seat_status"
```

**These errors are desirable** — they confirm the database is enforcing data integrity independently of the application code.

---

## 6. Backup and Restore

```bash
# Full database backup
docker compose exec -T db pg_dump -U seatpulse seatpulse > backup.sql

# Schema only (no data)
docker compose exec -T db pg_dump -U seatpulse --schema-only seatpulse > schema.sql

# Data only
docker compose exec -T db pg_dump -U seatpulse --data-only seatpulse > data.sql

# Restore
docker compose exec -T db psql -U seatpulse -d seatpulse < backup.sql
```

> The `-T` flag is required to prevent Docker from attaching a TTY, which would corrupt the output file.

### Recovery after `down -v`

Docker DB data is stored in the `postgres_data` volume. `docker compose down -v` deletes this volume.

**3 commands to restore:**
```bash
docker compose up -d
docker compose exec backend alembic upgrade head
docker compose exec backend python seed.py
```

> **Local PostgreSQL is unaffected.** It is installed on the system; Docker cannot access it. Only the Docker DB (port 5433) is deleted.

**Check if volume exists:**
```bash
docker volume ls -q | grep postgres     # no output = deleted
```

**Reset frontend `node_modules` without deleting DB:**
```bash
docker compose up -d --build --force-recreate --renew-anon-volumes frontend
```

---

## 7. Alembic (migrations)

Never modify the schema manually — always use migrations.

```bash
docker compose exec backend alembic revision --autogenerate -m "add column X"
docker compose exec backend alembic upgrade head      # apply
docker compose exec backend alembic downgrade -1      # revert one step
docker compose exec backend alembic current           # check current version
docker compose exec backend alembic history           # view migration history
```

> Always **review the generated migration file**. Autogenerate can occasionally produce incorrect code.

---

## ⚠️ Why port 5433 and not 5432?

**PostgreSQL is already installed** on this system (as a Windows service) and occupies port 5432.

If Docker also requested 5432, the mapping would conflict, and the local Postgres instance would take precedence. Consequently, pgAdmin would connect to the **local** Postgres on `localhost:5432`, which lacks the `seatpulse` user:

```
FATAL: password authentication failed for user "seatpulse"
```

Therefore, `POSTGRES_PORT=5433` is set in the root `.env`.

**Check which process is using the port (PowerShell):**

```powershell
Get-NetTCPConnection -LocalPort 5432 -State Listen |
  Select-Object LocalAddress, LocalPort, OwningProcess |
  ForEach-Object {
    $p = Get-Process -Id $_.OwningProcess -ErrorAction SilentlyContinue
    [PSCustomObject]@{ Port = $_.LocalPort; PID = $_.OwningProcess; Process = $p.ProcessName }
  }
```

If the output shows `postgres`, it is the local instance; `com.docker.backend` indicates Docker.

> **Note:** This change only affects **host-to-container** connections. The backend uses `db:5432` — the port inside the container network is always 5432. Changing `POSTGRES_PORT` does not affect the backend and does not require a restart.

---

## Common Problems

| Problem | Fix |
|---|---|
| `FATAL: password authentication failed for user "seatpulse"` | Wrong port — use **5433** in pgAdmin, not 5432 |
| `could not connect to server` / `connection refused` | Is the DB running? `docker compose ps` should show `db` as **healthy** |
| `port is already allocated` (on compose start) | Change `POSTGRES_PORT` in root `.env` (e.g., 5434, 5435...) |
| `relation "seats" does not exist` | Migrations not applied — run `docker compose exec backend alembic upgrade head` |
| `permission denied for table X` | Missing grant — see section 3, and `ALTER DEFAULT PRIVILEGES` |
| Query hangs / no output | Missing semicolon `;`. Add it and press Enter |
| Old password persists after `.env` change | Volume contains old data. Run `docker compose down -v` (⚠️ **data will be deleted**) |
| Table is too wide to read | Run `\x` to toggle expanded mode |
| `psql: command not found` (on host) | psql is not installed on host — use `docker compose exec db psql ...` |

---

## Related

- [Phase 2 — Postgres + Models](../phases/02-postgres-models.md) — table design and rationale
- [docker-commands.md](docker-commands.md) — container commands
- [roadmap.md](../roadmap.md) — future development roadmap
