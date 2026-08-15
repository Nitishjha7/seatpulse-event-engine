FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt requirements-dev.txt ./
# Dev image hai (--reload ke saath chalti hai), isliye test tools bhi
# saath me install kar rahe hain. Production build me sirf
# requirements.txt install karna — dev wali line hata dena.
RUN pip install --no-cache-dir -r requirements-dev.txt

COPY . .

EXPOSE 8000

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
