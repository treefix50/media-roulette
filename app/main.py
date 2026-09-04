"""
Media Roulette - FastAPI application entry point.

Deployment model:

```
Internet
    |
  Zoraxy
    |
Media Roulette / Uvicorn
```

Zoraxy terminates TLS and forwards HTTP traffic to this application.

Application responsibilities:

* session authentication
* CSRF protection
* security headers
* static files
* HTML templates
* API routing
* health endpoint

The application does NOT expose or trust arbitrary forwarded headers.
"""

from **future** import annotations

import logging
import os
import secrets
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from app.api.routes import router
from app.library import init_db
from app.middleware import add_security_headers_fastapi
from app.rate_limit import (
ClientIPMiddleware,
enforce_api_rate_limit,
)

# ============================================================================

# LOGGING

# ============================================================================

LOG_LEVEL = os.getenv(
"LOG_LEVEL",
"INFO",
).strip().upper()

logging.basicConfig(
level=getattr(
logging,
LOG_LEVEL,
logging.INFO,
),
format=(
"%(asctime)s "
"%(levelname)s "
"%(name)s "
"%(message)s"
),
)

logger = logging.getLogger(
"media_roulette"
)

# ============================================================================

# PATHS

# ============================================================================

BASE_DIR = Path(
**file**
).resolve().parent

PROJECT_ROOT = BASE_DIR.parent

TEMPLATES_DIR = Path(
os.getenv(
"TEMPLATES_DIR",
str(
BASE_DIR / "templates"
),
)
).expanduser()

STATIC_DIR = Path(
os.getenv(
"STATIC_DIR",
str(
BASE_DIR / "static"
),
)
).expanduser()

# ============================================================================

# ENVIRONMENT

# ============================================================================

ENVIRONMENT = os.getenv(
"ENVIRONMENT",
"production",
).strip().lower()

PUBLIC_HOST = os.getenv(
"PUBLIC_HOST",
"",
).strip()

PUBLIC_SCHEME = os.getenv(
"PUBLIC_SCHEME",
"https" if ENVIRONMENT == "production" else "http",
).strip().lower()

SESSION_SECRET = os.getenv(
"SESSION_SECRET",
"",
)

# Optional trusted proxy addresses.

# Example:

#

# TRUSTED_PROXY_IPS=172.18.0.2,172.18.0.3

#

# Never use "*" here.

TRUSTED_PROXY_IPS = {
value.strip()
for value in os.getenv(
"TRUSTED_PROXY_IPS",
"",
).split(",")
if value.strip()
}

# ============================================================================

# STARTUP VALIDATION

# ============================================================================

def _validate_configuration() -> None:
"""
Validate configuration that is unsafe to guess.

```
Production deployments must provide a persistent session secret.
"""

if ENVIRONMENT == "production":

    if not SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET must be set in production."
        )

    if len(SESSION_SECRET) < 32:
        raise RuntimeError(
            "SESSION_SECRET must contain at least 32 characters."
        )

    if PUBLIC_SCHEME != "https":
        logger.warning(
            "PUBLIC_SCHEME is not https in production. "
            "HSTS will not be enabled."
        )

if not TEMPLATES_DIR.is_dir():
    raise RuntimeError(
        f"Template directory does not exist: {TEMPLATES_DIR}"
    )

if not STATIC_DIR.is_dir():
    raise RuntimeError(
        f"Static directory does not exist: {STATIC_DIR}"
    )
```

_validate_configuration()

# ============================================================================

# DATABASE

# ============================================================================

init_db()

# ============================================================================

# FASTAPI APP

# ============================================================================

app = FastAPI(
title="Media Roulette",
description=(
"Self-hosted media randomizer."
),
version=os.getenv(
"APP_VERSION",
"1.0.0",
),
docs_url=None,
redoc_url=None,
openapi_url=None,
)

# ============================================================================

# TEMPLATES / STATIC

# ============================================================================

templates = Jinja2Templates(
directory=str(
TEMPLATES_DIR
)
)

app.mount(
"/static",
StaticFiles(
directory=str(
STATIC_DIR
)
),
name="static",
)

# ============================================================================

# MIDDLEWARE

# ============================================================================

# ClientIPMiddleware must be installed before the rate-limiter dependencies

# execute so request.state.client_ip is available.

#

# The middleware itself only trusts forwarded IP headers when the direct peer

# belongs to TRUSTED_PROXY_IPS.

app.add_middleware(
ClientIPMiddleware,
trusted_proxy_ips=TRUSTED_PROXY_IPS,
)

# Signed session cookie.

#

# SessionMiddleware uses itsdangerous internally and signs the cookie so the

# browser cannot alter its contents without invalidating the signature.

#

# same_site="lax" is suitable for the normal browser navigation flow.

#

# https_only=True means the cookie will only be transmitted over HTTPS.

# This is appropriate for the production Zoraxy deployment.

app.add_middleware(
SessionMiddleware,
secret_key=(
SESSION_SECRET
if SESSION_SECRET
else secrets.token_urlsafe(32)
),
session_cookie="media_roulette_session",
max_age=60 * 60 * 24 * 14,
same_site="lax",
https_only=(
PUBLIC_SCHEME == "https"
),
)

# Application security headers.

add_security_headers_fastapi(
app
)

# ============================================================================

# ROUTES

# ============================================================================

app.include_router(
router
)

# ============================================================================

# HEALTH

# ============================================================================

@app.get(
"/health",
include_in_schema=False,
)
async def health() -> JSONResponse:
"""
Lightweight health endpoint.

```
This endpoint deliberately does not require authentication so that
Zoraxy/container monitoring can check application availability.
"""

return JSONResponse(
    {
        "status": "ok",
        "service": "media-roulette",
    }
)
```

# ============================================================================

# APPLICATION PAGES

# ============================================================================

@app.get(
"/",
response_class=HTMLResponse,
include_in_schema=False,
)
async def index(
request: Request,
):
"""
Render the main application.

```
Authentication is handled by the template itself so an unauthenticated
browser receives the login page without a redirect loop.
"""

authenticated = bool(
    request.session.get(
        "authenticated",
        False,
    )
)

csrf_token = request.session.get(
    "csrf_token"
)

if not csrf_token:
    csrf_token = secrets.token_urlsafe(
        32
    )

    request.session[
        "csrf_token"
    ] = csrf_token

context: dict[str, Any] = {
    "request": request,
    "authenticated": authenticated,
    "csrf_token": csrf_token,
    "public_host": PUBLIC_HOST,
    "public_scheme": PUBLIC_SCHEME,
}

return templates.TemplateResponse(
    request=request,
    name="index.html",
    context=context,
)
```

# ============================================================================

# ERROR HANDLERS

# ============================================================================

@app.exception_handler(
404
)
async def not_found(
request: Request,
exc,
):
"""
Return a JSON response for API requests and the normal application page
for browser requests.
"""

```
path = request.url.path

if path.startswith(
    "/api/"
):
    return JSONResponse(
        status_code=404,
        content={
            "detail": "Not found.",
        },
    )

return JSONResponse(
    status_code=404,
    content={
        "detail": "Not found.",
    },
)
```

@app.exception_handler(
500
)
async def internal_error(
request: Request,
exc,
):
"""
Prevent internal exception details from reaching clients.
"""

```
logger.exception(
    "Unhandled application error"
)

return JSONResponse(
    status_code=500,
    content={
        "detail": "Internal server error.",
    },
)
```

# ============================================================================

# STARTUP / SHUTDOWN

# ============================================================================

@app.on_event(
"startup"
)
async def startup_event() -> None:
"""
Application startup hook.
"""

```
logger.info(
    "Media Roulette starting"
)

logger.info(
    "Environment: %s",
    ENVIRONMENT,
)

logger.info(
    "Public scheme: %s",
    PUBLIC_SCHEME,
)

if PUBLIC_HOST:
    logger.info(
        "Public host: %s",
        PUBLIC_HOST,
    )

if TRUSTED_PROXY_IPS:
    logger.info(
        "Trusted proxy addresses configured: %d",
        len(TRUSTED_PROXY_IPS),
    )
else:
    logger.info(
        "No trusted proxy addresses configured; "
        "direct connection addresses will be used."
    )
```

@app.on_event(
"shutdown"
)
async def shutdown_event() -> None:
"""
Application shutdown hook.
"""

```
logger.info(
    "Media Roulette shutting down"
)
```

# ============================================================================

# EXPORT

# ============================================================================

**all** = [
"app",
"templates",
]
