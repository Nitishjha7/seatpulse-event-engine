#!/bin/sh
# Entrypoint for hosts with no shell/pre-deploy access (e.g. Render's free
# tier) — runs migrations and seeding before starting the server, since
# there's nowhere else to run them one-off. Both are idempotent, so this
# is safe to run on every cold start, not just the first one.
set -e

alembic upgrade head
python -m scripts.seed

exec uvicorn main:app --host 0.0.0.0 --port 8000 --workers "${WORKERS:-1}"
