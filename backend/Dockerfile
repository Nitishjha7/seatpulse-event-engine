# one Dockerfile, two targets (dev / prod) — keeps the base layers (python
# version, system deps, requirements) identical instead of two files
# slowly drifting apart

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM base AS dev

COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
FROM base AS prod

RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000

CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-4}"]
