# Targets: `dev` (default) and `prod`.
#
# Using a single Dockerfile ensures base layers (Python version, system deps, requirements) remain identical. Separate files often drift over time, leading to "it works on my machine" issues.

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

WORKDIR /app

# Configure Python for the container:
#   PYTHONDONTWRITEBYTECODE -> Prevents .pyc files from bloating the image.
#   PYTHONUNBUFFERED        -> Ensures logs are sent directly to stdout/stderr without buffering (prevents log loss on crash).
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM base AS dev

# Dev image requires testing tools like pytest and locust.
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
EXPOSE 8000

# --reload is for dev only. It runs a file watcher that consumes CPU and should never be used in production.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
FROM base AS prod

# ⚠️ Do not run as root. This prevents attackers from gaining root access if the container is compromised. A simple, high-impact security measure.
RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000

# Worker count is managed via the WORKERS env var, as it depends on machine cores. No hardcoding. No --reload or dev tools here.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-4}"]
