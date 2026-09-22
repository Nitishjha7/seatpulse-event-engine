FROM python:3.11-slim

WORKDIR /loadtest

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8089

# ENTRYPOINT (not CMD) so `docker compose run --rm locust -f locustfile.py
# FlashSaleUser --headless ...` overrides just the args, not the binary
ENTRYPOINT ["locust"]
CMD ["-f", "locustfile.py"]
