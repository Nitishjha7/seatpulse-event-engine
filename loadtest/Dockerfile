FROM python:3.11-slim

WORKDIR /loadtest

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Locust web UI
EXPOSE 8089

# Use ENTRYPOINT instead of CMD: `docker compose run` arguments
# override the CMD. Since the entrypoint remains fixed,
# `run --rm locust -f locustfile.py --headless ...` works correctly.
ENTRYPOINT ["locust"]
CMD ["-f", "locustfile.py"]
