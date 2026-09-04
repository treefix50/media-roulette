# ============================================================================

# Media Roulette - Production Dockerfile

#

# Deployment:

#

# Internet

# |

# Zoraxy

# |

# Media Roulette :8000

#

# The container itself does NOT terminate TLS.

# Zoraxy is responsible for public HTTPS.

# ============================================================================

# ----------------------------------------------------------------------------

# Build stage

# ----------------------------------------------------------------------------

FROM python:3.11-slim AS builder

WORKDIR /build

ENV PYTHONDONTWRITEBYTECODE=1 
PYTHONUNBUFFERED=1 
PIP_DISABLE_PIP_VERSION_CHECK=1

# Copy dependency definition first so Docker can cache the dependency layer.

COPY requirements.txt .

# Build wheels for all Python dependencies.

RUN python -m pip wheel 
--no-cache-dir 
--wheel-dir /wheels 
-r requirements.txt

# ----------------------------------------------------------------------------

# Runtime stage

# ----------------------------------------------------------------------------

FROM python:3.11-slim AS runtime

# Prevent Python from writing .pyc files and make stdout/stderr immediately

# visible to Docker logs.

ENV PYTHONDONTWRITEBYTECODE=1 
PYTHONUNBUFFERED=1 
PIP_DISABLE_PIP_VERSION_CHECK=1 
PIP_NO_CACHE_DIR=1

# Application defaults.

ENV HOST=0.0.0.0 
PORT=8000 
ENVIRONMENT=production 
DATABASE_PATH=/state/media_roulette.db 
MOVIES_DIR=/data/movies 
SERIES_DIR=/data/tv

# ----------------------------------------------------------------------------

# System packages

# ----------------------------------------------------------------------------

RUN apt-get update 
&& apt-get install -y --no-install-recommends 
ca-certificates 
curl 
&& rm -rf /var/lib/apt/lists/* 
&& groupadd --system --gid 10001 appgroup 
&& useradd 
--system 
--uid 10001 
--gid 10001 
--home-dir /app 
--create-home 
--shell /usr/sbin/nologin 
appuser

# ----------------------------------------------------------------------------

# Application directories

# ----------------------------------------------------------------------------

WORKDIR /app

RUN mkdir -p 
/app 
/state 
/data/movies 
/data/tv 
&& chown -R 
appuser:appgroup 
/app 
/state 
/data

# ----------------------------------------------------------------------------

# Python dependencies

# ----------------------------------------------------------------------------

# requirements.txt is required by the pip command in this stage.

COPY requirements.txt /tmp/requirements.txt

# Copy the pre-built wheels from the builder stage.

COPY --from=builder /wheels /wheels

RUN python -m pip install 
--no-cache-dir 
--no-index 
--find-links=/wheels 
-r /tmp/requirements.txt 
&& rm -rf 
/wheels 
/tmp/requirements.txt

# ----------------------------------------------------------------------------

# Application

# ----------------------------------------------------------------------------

COPY --chown=appuser:appgroup app/ /app/app/

# Ensure all application files are readable by the unprivileged user.

RUN chown -R 
appuser:appgroup 
/app/app

# ----------------------------------------------------------------------------

# Runtime user

# ----------------------------------------------------------------------------

USER appuser

# ----------------------------------------------------------------------------

# Network

# ----------------------------------------------------------------------------

EXPOSE 8000

# ----------------------------------------------------------------------------

# Container health check

#

# This talks directly to FastAPI inside the container.

# Zoraxy does not need to be involved.

# ----------------------------------------------------------------------------

HEALTHCHECK 
--interval=30s 
--timeout=10s 
--start-period=20s 
--retries=3 
CMD curl 
--fail 
--silent 
--show-error 
http://127.0.0.1:8000/health 
|| exit 1

# ----------------------------------------------------------------------------

# Application startup

#

# One worker is intentional:

#

# - the current rate limiter stores state in process memory

# - Library uses SQLite

# - startup scanning should happen once

#

# If horizontal scaling is needed later, rate limiting should move to a

# shared backend and the library architecture should be reviewed accordingly.

# ----------------------------------------------------------------------------

CMD [
"uvicorn",
"app.main:app",
"--host",
"0.0.0.0",
"--port",
"8000",
"--workers",
"1",
"--proxy-headers",
"--forwarded-allow-ips",
"127.0.0.1"
]
