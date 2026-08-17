# Media Roulette Dockerfile
FROM python:3.12-slim

WORKDIR /app

# Abhängigkeiten installieren
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Anwendung kopieren
COPY app/ ./app/

# Exponiere Port
EXPOSE 8000

# Anwendung starten
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
