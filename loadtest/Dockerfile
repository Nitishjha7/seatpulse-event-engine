FROM python:3.11-slim

WORKDIR /loadtest

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Locust ka web UI
EXPOSE 8089

# ENTRYPOINT isliye (sirf CMD nahi): `docker compose run` ke arguments
# CMD ko poora replace kar dete hain. Entrypoint fix rehta hai, to
# `run --rm locust -f locustfile.py --headless ...` sahi chalta hai.
ENTRYPOINT ["locust"]
CMD ["-f", "locustfile.py"]
