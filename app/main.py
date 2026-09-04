"""
Media Roulette - FastAPI application entry point.

The application is designed to run behind a reverse proxy such as Zoraxy.
TLS/HTTPS termination is expected to happen at the reverse proxy.

Important deployment assumptions:

* The application itself listens on HTTP.
* The media directories are mounted read-only.
* Persistent application state is stored below /state.
* Only one application process should perform the startup library scan.
* Authentication is handled by app.security.
  """

from **future** import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from slowapi.errors import RateLimitExceeded

from app.middleware import add_security_headers_fastapi
from app.rate_limit import limiter, rate_limiter_exception_handler

# ============================================================================

# PATHS

# ============================================================================

APP_DIR = Path(**file**).resolve().parent
TEMPLATES_DIR = APP_DIR / "templates"
STATIC_DIR = APP_DIR / "static"

# ============================================================================

# CONFIGURATION

# ============================================================================

HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

ENVIRONMENT = os.getenv("ENVIRONMENT", "production").strip().lower()

# The application must not silently start with a known/default session secret.

SECRET_KEY = os.getenv("SECRET_KEY", "").strip()

if not SECRET_KEY:
raise RuntimeError(
"SECRET_KEY is not configured. "
"Generate a long random secret and set SECRET_KEY in the environment."
)

if len(SECRET_KEY) < 32:
raise RuntimeError(
"SECRET_KEY is too short. "
"Use a random secret with at least 32 characters."
)

# TrustedHostMiddleware is important when the application is reachable

# through a reverse proxy. The value may contain a comma-separated list:

#

# media.example.com,localhost,127.0.0.1

#

# "*" disables host validation and is therefore not recommended for

# production deployments.

TRUSTED_HOSTS_RAW = os.getenv(
"TRUSTED_HOSTS",
"localhost,127.0.0.1",
).strip()

TRUSTED_HOSTS = [
host.strip()
for host in TRUSTED_HOSTS_RAW.split(",")
if host.strip()
]

if not TRUSTED_HOSTS:
raise RuntimeError(
"TRUSTED_HOSTS must contain at least one hostname."
)

# Session settings.

#

# HTTPS is normally terminated by Zoraxy. The application receives the

# forwarded request after the proxy. The authentication implementation

# itself is responsible for its cookie configuration.

SESSION_MAX_AGE = int(
os.getenv("SESSION_MAX_AGE", str(60 * 60 * 24))
)

SESSION_SAME_SITE = os.getenv(
"SESSION_SAME_SITE",
"lax",
).strip().lower()

if SESSION_SAME_SITE not in {"lax", "strict", "none"}:
raise RuntimeError(
"SESSION_SAME_SITE must be one of: lax, strict, none."
)

# ============================================================================

# LOGGING

# ============================================================================

# Docker/Unraid deployments should log to stdout/stderr instead of maintaining

# an application-specific log file inside the container.

logging.basicConfig(
level=os.getenv("LOG_LEVEL", "INFO").upper(),
format="%(asctime)s %(levelname)s %(name)s: %(message)s",
handlers=[
logging.StreamHandler(),
],
force=True,
)

logger = logging.getLogger("media_roulette")

# ============================================================================

# TEMPLATES

# ============================================================================

if not TEMPLATES_DIR.is_dir():
raise RuntimeError(
f"Template directory does not exist: {TEMPLATES_DIR}"
)

if not STATIC_DIR.is_dir():
raise RuntimeError(
f"Static directory does not exist: {STATIC_DIR}"
)

templates = Jinja2Templates(
directory=str(TEMPLATES_DIR),
)

# ============================================================================

# APPLICATION LIFESPAN

# ============================================================================

@asynccontextmanager
async def lifespan(application: FastAPI) -> AsyncIterator[None]:
"""
Application startup/shutdown lifecycle.

```
The library scan is deliberately executed once during application startup.
The container deployment must use a single application worker. Running
multiple independent workers would otherwise perform the scan once per
process.
"""

logger.info(
    "Starting Media Roulette "
    "(environment=%s, host=%s, port=%s)",
    ENVIRONMENT,
    HOST,
    PORT,
)

# Import here to avoid unnecessary import-time initialization and to keep
# the application object independent from the library scanner.
from app.api.routes import library
from app.security import create_default_admin

try:
    create_default_admin()
except Exception:
    logger.exception("Security initialization failed")
    raise

try:
    count = library.scan()
    logger.info(
        "Initial library scan complete: %s media items",
        count,
    )
except Exception:
    # A library scan failure should not necessarily make the web
    # application unavailable. The existing database remains usable and
    # the scan can be triggered again through the authenticated API.
    logger.exception("Initial library scan failed")

yield

logger.info("Shutting down Media Roulette...")
```

# ============================================================================

# FASTAPI APPLICATION

# ============================================================================

app = FastAPI(
title="Media Roulette",
description="Local random media recommendation service",
version="2.0.0",
lifespan=lifespan,
docs_url=None,
redoc_url=None,
openapi_url=None,
)

# ============================================================================

# RATE LIMITING

# ============================================================================

app.state.limiter = limiter

app.add_exception_handler(
RateLimitExceeded,
rate_limiter_exception_handler,
)

# ============================================================================

# TRUSTED HOSTS

# ============================================================================

app.add_middleware(
TrustedHostMiddleware,
allowed_hosts=TRUSTED_HOSTS,
)

# ============================================================================

# SESSION MIDDLEWARE

# ============================================================================

app.add_middleware(
SessionMiddleware,
secret_key=SECRET_KEY,
max_age=SESSION_MAX_AGE,
same_site=SESSION_SAME_SITE,
https_only=ENVIRONMENT == "production",
)

# ============================================================================

# SECURITY HEADERS

# ============================================================================

app = add_security_headers_fastapi(app)

# ============================================================================

# STATIC FILES

# ============================================================================

app.mount(
"/static",
StaticFiles(directory=str(STATIC_DIR)),
name="static",
)

# ============================================================================

# ROUTES

# ============================================================================

@app.get(
"/",
response_class=HTMLResponse,
include_in_schema=False,
)
async def root(request: Request) -> HTMLResponse:
"""
Render the main application page.

```
Authentication is intentionally handled by the frontend/API layer so
unauthenticated visitors can be redirected to the login page without
duplicating authentication logic here.
"""

return templates.TemplateResponse(
    "index.html",
    {
        "request": request,
    },
)
```

@app.get(
"/login",
response_class=HTMLResponse,
include_in_schema=False,
)
async def login_page(request: Request) -> HTMLResponse:
"""
Render the login page.
"""

```
return templates.TemplateResponse(
    "login.html",
    {
        "request": request,
    },
)
```

@app.get(
"/health",
tags=["Health"],
)
async def health_check() -> dict[str, str]:
"""
Public health endpoint for Docker, Unraid and reverse-proxy monitoring.

```
This endpoint intentionally does not require authentication.
"""

return {
    "status": "healthy",
    "service": "media-roulette",
    "version": "2.0.0",
    "timestamp": datetime.now(timezone.utc).isoformat(),
}
```

@app.post(
"/logout",
include_in_schema=False,
)
async def logout(request: Request) -> RedirectResponse:
"""
Clear the server-side authentication/session state.

```
The authentication implementation may store additional session values;
clearing the complete session prevents stale authentication information
from surviving logout.
"""

request.session.clear()

response = RedirectResponse(
    url="/login",
    status_code=303,
)

# Explicitly expire the Starlette session cookie as an additional
# defensive measure.
response.delete_cookie(
    key="session",
    path="/",
)

return response
```

# ============================================================================

# API ROUTER

# ============================================================================

# Import only after the application-wide objects above have been initialized.

# routes.py does not import templates from main.py, avoiding the circular

# import present in the previous architecture.

from app.api.routes import router as api_router

app.include_router(api_router)

# ============================================================================

# PUBLIC EXPORTS

# ============================================================================

**all** = [
"app",
"templates",
]

# ============================================================================

# DIRECT EXECUTION

# ============================================================================

if **name** == "**main**":
import uvicorn

```
debug = (
    os.getenv("DEBUG", "false").strip().lower()
    in {"1", "true", "yes", "on"}
)

# Deliberately one worker.
#
# The library scanner performs filesystem/database work during startup.
# Multiple Uvicorn workers would create separate processes and therefore
# separate in-process locks, causing the startup scan to run multiple
# times.
uvicorn.run(
    "app.main:app",
    host=HOST,
    port=PORT,
    reload=debug,
    workers=1,
    proxy_headers=True,
    forwarded_allow_ips=os.getenv(
        "FORWARDED_ALLOW_IPS",
        "*",
    ),
)
```
