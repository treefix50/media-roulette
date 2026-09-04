"""
Media Roulette - FastAPI application entry point.

Deployment:

```
Internet
    |
  Zoraxy
    |
Media Roulette
    |
  SQLite / Media Library
```

Zoraxy terminates HTTPS and forwards requests to this application.

The application itself is responsible for:

* signed browser sessions
* authentication
* CSRF protection
* security headers
* trusted-host validation
* static files
* HTML templates
* API routes
* library startup scan
  """

from **future** import annotations

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.trustedhost import TrustedHostMiddleware

from app.library import Library
from app.middleware import add_security_headers_fastapi
from app.rate_limit import ClientIPMiddleware
from app.security import (
destroy_session,
get_or_create_csrf_token,
get_current_user,
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

# CONFIGURATION

# ============================================================================

ENVIRONMENT = os.getenv(
"ENVIRONMENT",
"production",
).strip().lower()

HOST = os.getenv(
"HOST",
"0.0.0.0",
).strip()

PORT = int(
os.getenv(
"PORT",
"8000",
)
)

SECRET_KEY = os.getenv(
"SECRET_KEY",
"",
).strip()

SESSION_SECRET = os.getenv(
"SESSION_SECRET",
SECRET_KEY,
).strip()

PUBLIC_HOST = os.getenv(
"PUBLIC_HOST",
"",
).strip()

PUBLIC_SCHEME = os.getenv(
"PUBLIC_SCHEME",
"https" if ENVIRONMENT == "production" else "http",
).strip().lower()

TRUSTED_HOSTS_RAW = os.getenv(
"TRUSTED_HOSTS",
"",
).strip()

TRUSTED_PROXY_IPS_RAW = os.getenv(
"TRUSTED_PROXY_IPS",
"",
).strip()

TRUSTED_PROXY_IPS = {
value.strip()
for value in TRUSTED_PROXY_IPS_RAW.split(",")
if value.strip()
}

# ============================================================================

# MEDIA CONFIGURATION

# ============================================================================

DATABASE_PATH = os.getenv(
"DATABASE_PATH",
"/state/media_roulette.db",
).strip()

MOVIES_DIR = os.getenv(
"MOVIES_DIR",
"/data/movies",
).strip()

SERIES_DIR = os.getenv(
"SERIES_DIR",
"/data/tv",
).strip()

# ============================================================================

# LOGGING

# ============================================================================

LOG_LEVEL = os.getenv(
"LOG_LEVEL",
"INFO",
).strip().upper()

LOG_FORMAT = (
"%(asctime)s "
"%(levelname)s "
"%(name)s "
"%(message)s"
)

logging.basicConfig(
level=getattr(
logging,
LOG_LEVEL,
logging.INFO,
),
format=LOG_FORMAT,
)

logger = logging.getLogger(
"media_roulette"
)

# ============================================================================

# VALIDATION

# ============================================================================

def _validate_configuration() -> None:
"""
Validate mandatory production configuration.

```
The application refuses to silently generate a new session secret in
production because doing so would invalidate all sessions after every
restart and can hide a serious deployment mistake.
"""

if ENVIRONMENT == "production":

    if not SESSION_SECRET:
        raise RuntimeError(
            "SESSION_SECRET or SECRET_KEY must be configured in production."
        )

    if len(SESSION_SECRET) < 32:
        raise RuntimeError(
            "SESSION_SECRET must contain at least 32 characters."
        )

    if PUBLIC_SCHEME != "https":
        logger.warning(
            "PUBLIC_SCHEME is not https in production."
        )

if not TEMPLATES_DIR.is_dir():
    raise RuntimeError(
        "Template directory does not exist: "
        f"{TEMPLATES_DIR}"
    )

if not STATIC_DIR.is_dir():
    raise RuntimeError(
        "Static directory does not exist: "
        f"{STATIC_DIR}"
    )

if PORT < 1 or PORT > 65535:
    raise RuntimeError(
        f"Invalid PORT: {PORT}"
    )
```

_validate_configuration()

# ============================================================================

# TEMPLATES

# ============================================================================

templates = Jinja2Templates(
directory=str(
TEMPLATES_DIR
)
)

# ============================================================================

# LIBRARY

# ============================================================================

library = Library(
db_path=DATABASE_PATH,
movies_dir=MOVIES_DIR,
series_dir=SERIES_DIR,
)

# ============================================================================

# STARTUP / SHUTDOWN

# ============================================================================

@asynccontextmanager
async def lifespan(
application: FastAPI,
):
"""
Application lifespan.

```
Library initialization happens when the Library object is constructed.
The actual media scan is performed once during startup.
"""

logger.info(
    "Starting Media Roulette"
)

logger.info(
    "Environment: %s",
    ENVIRONMENT,
)

logger.info(
    "Database: %s",
    DATABASE_PATH,
)

logger.info(
    "Movies directory: %s",
    MOVIES_DIR,
)

logger.info(
    "Series directory: %s",
    SERIES_DIR,
)

try:

    count = library.scan()

    logger.info(
        "Initial library scan complete: %s items",
        count,
    )

except Exception:
    logger.exception(
        "Initial library scan failed"
    )

    # Do not prevent the HTTP application from starting.
    #
    # This is useful when the media mount is temporarily unavailable.
    # The authenticated manual scan endpoint can retry later.

yield

logger.info(
    "Shutting down Media Roulette"
)
```

# ============================================================================

# APPLICATION

# ============================================================================

app = FastAPI(
title="Media Roulette",
description=(
"Local random media recommendation service."
),
version=os.getenv(
"APP_VERSION",
"1.0.0",
),
lifespan=lifespan,
docs_url=None,
redoc_url=None,
openapi_url=None,
)

# ============================================================================

# PROXY / CLIENT IP

# ============================================================================

app.add_middleware(
ClientIPMiddleware,
trusted_proxy_ips=TRUSTED_PROXY_IPS,
)

# ============================================================================

# SESSION

# ============================================================================

app.add_middleware(
SessionMiddleware,
secret_key=SESSION_SECRET,
session_cookie="media_roulette_session",
max_age=60 * 60 * 24 * 14,
same_site="lax",
https_only=(
PUBLIC_SCHEME == "https"
),
)

# ============================================================================

# TRUSTED HOST

# ============================================================================

if TRUSTED_HOSTS_RAW:

```
trusted_hosts = [
    value.strip()
    for value in TRUSTED_HOSTS_RAW.split(",")
    if value.strip()
]

if trusted_hosts:
    app.add_middleware(
        TrustedHostMiddleware,
        allowed_hosts=trusted_hosts,
    )
```

# ============================================================================

# SECURITY HEADERS

# ============================================================================

app = add_security_headers_fastapi(
app
)

# ============================================================================

# ROUTER

# ============================================================================

# Import only after the application-level objects exist.

#

# routes.py may import `templates` and `library` from this module.

from app.api.routes import router as api_router  # noqa: E402

app.include_router(
api_router
)

# ============================================================================

# STATIC FILES

# ============================================================================

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

# ROOT PAGE

# ============================================================================

@app.get(
"/",
response_class=HTMLResponse,
include_in_schema=False,
)
async def root(
request: Request,
):
"""
Render the main application page.

```
The frontend can use the authentication state to decide whether to show
the login UI or the application UI.
"""

current_user = get_current_user(
    request
)

csrf_token = get_or_create_csrf_token(
    request
)

return templates.TemplateResponse(
    request=request,
    name="index.html",
    context={
        "authenticated": (
            current_user is not None
        ),
        "user": current_user,
        "csrf_token": csrf_token,
        "public_host": PUBLIC_HOST,
        "public_scheme": PUBLIC_SCHEME,
    },
)
```

# ============================================================================

# LOGIN PAGE

# ============================================================================

@app.get(
"/login",
response_class=HTMLResponse,
include_in_schema=False,
)
async def login_page(
request: Request,
):
"""
Render the login page.
"""

```
current_user = get_current_user(
    request
)

if current_user is not None:
    return RedirectResponse(
        url="/",
        status_code=303,
    )

csrf_token = get_or_create_csrf_token(
    request
)

return templates.TemplateResponse(
    request=request,
    name="login.html",
    context={
        "authenticated": False,
        "csrf_token": csrf_token,
        "public_host": PUBLIC_HOST,
        "public_scheme": PUBLIC_SCHEME,
    },
)
```

# ============================================================================

# LOGOUT

# ============================================================================

@app.post(
"/logout",
include_in_schema=False,
)
async def logout(
request: Request,
):
"""
Destroy the entire authenticated session.
"""

```
destroy_session(
    request
)

return RedirectResponse(
    url="/login",
    status_code=303,
)
```

# ============================================================================

# HEALTH

# ============================================================================

@app.get(
"/health",
include_in_schema=False,
)
async def health():
"""
Public health endpoint.

```
No authentication is required so Docker/Zoraxy monitoring can use it.
"""

return {
    "status": "healthy",
    "timestamp": datetime.now(
        timezone.utc
    ).isoformat(),
    "version": os.getenv(
        "APP_VERSION",
        "1.0.0",
    ),
    "service": "media-roulette",
}
```

# ============================================================================

# ERROR HANDLERS

# ============================================================================

@app.exception_handler(
404
)
async def not_found_handler(
request: Request,
exc,
):
"""
Return a generic 404 without exposing filesystem or framework details.
"""

```
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
async def internal_error_handler(
request: Request,
exc,
):
"""
Do not expose internal exception details to clients.
"""

```
logger.exception(
    "Unhandled application exception"
)

return JSONResponse(
    status_code=500,
    content={
        "detail": "Internal server error.",
    },
)
```

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"app",
"library",
"templates",
]

# ============================================================================

# LOCAL DEVELOPMENT

# ============================================================================

if **name** == "**main**":

```
import uvicorn

uvicorn.run(
    "app.main:app",
    host=HOST,
    port=PORT,
    reload=(
        ENVIRONMENT != "production"
        and os.getenv(
            "DEBUG",
            "false",
        ).strip().lower()
        == "true"
    ),
)
```
