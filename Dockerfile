FROM python:3.12-slim-bookworm


WORKDIR /app


ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1


# ==============================================================
# PYTHON DEPENDENCIES
# ==============================================================

COPY requirements.txt .


RUN pip install \
        --no-cache-dir \
        -r requirements.txt \
    && rm -rf /root/.cache/pip


# ==============================================================
# APPLICATION
# ==============================================================

COPY app/ ./app/


# ==============================================================
# STATE
# ==============================================================

RUN mkdir -p /state


# ==============================================================
# SECURITY
# ==============================================================

# Python erzeugt keine .pyc-Dateien.
# /state wird nur für die SQLite-Datenbank verwendet.


EXPOSE 8000


# ==============================================================
# SERVER
# ==============================================================

CMD [
    "uvicorn",
    "app.main:app",
    "--host",
    "0.0.0.0",
    "--port",
    "8000",
    "--proxy-headers"
]
