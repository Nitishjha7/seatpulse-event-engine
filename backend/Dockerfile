# Do targets: `dev` (default) aur `prod`.
#
# Ek hi Dockerfile isliye rakhi hai taki base layers (python version,
# system deps, requirements) dono me BILKUL same rahein. Do alag files
# hoti to wo dheere-dheere alag ho jaati — aur "mere laptop pe to chalta
# tha" wali dikkat wahin se paida hoti hai.

# ---------------------------------------------------------------------------
FROM python:3.11-slim AS base

WORKDIR /app

# Python ko container ke hisaab se set karo:
#   PYTHONDONTWRITEBYTECODE -> .pyc files image me bekaar jagah lete hain
#   PYTHONUNBUFFERED        -> print/logs turant dikhein, buffer me na atkein
#                              (iske bina container crash pe aakhri logs gayab)
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# ---------------------------------------------------------------------------
FROM base AS dev

# Dev image me test tools bhi chahiye — pytest, locust waqerah.
COPY requirements-dev.txt ./
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .
EXPOSE 8000

# --reload sirf dev me. Ye file watcher chalata hai jo CPU khata hai aur
# production me kabhi nahi hona chahiye.
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------------------------------------------------------------------
FROM base AS prod

# ⚠️ Root se nahi chalate. Container escape ho bhi jaye to attacker ko
# root nahi milna chahiye. Ye ek line bahut saste me bada faayda deti hai.
RUN useradd --create-home --uid 1000 appuser

COPY --chown=appuser:appuser . .

USER appuser
EXPOSE 8000

# Workers ki ginti compose se aati hai (WORKERS env). Yahan hardcode nahi
# ki, kyunki wo machine ke cores par nirbhar karti hai.
#
# --reload nahi hai, dev tools install nahi hain.
CMD ["sh", "-c", "uvicorn main:app --host 0.0.0.0 --port 8000 --workers ${WORKERS:-4}"]
