"""
Media Roulette - HTTP security middleware.

The application is designed to run behind a reverse proxy such as Zoraxy.

Responsibilities:

* security response headers
* Content Security Policy
* HSTS in HTTPS production mode
* removal of application-level server identification

TLS termination itself is intentionally left to Zoraxy.
"""

from **future** import annotations

import os

from fastapi import FastAPI
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

# ============================================================================

# CONFIGURATION

# ============================================================================

ENVIRONMENT = os.getenv(
"ENVIRONMENT",
"production",
).strip().lower()

PUBLIC_SCHEME = os.getenv(
"PUBLIC_SCHEME",
"https" if ENVIRONMENT == "production" else "http",
).strip().lower()

# HSTS is intentionally disabled unless the application knows it is served

# through HTTPS.

HSTS_MAX_AGE = 31536000

# ============================================================================

# CONTENT SECURITY POLICY

# ============================================================================

# The existing Media Roulette frontend uses application-local resources and

# may currently contain inline JavaScript/CSS.

#

# 'unsafe-inline' is therefore retained for compatibility at this stage.

# Once the frontend files have been audited, inline code can be migrated to

# external files and these directives can be tightened.

CONTENT_SECURITY_POLICY = (
"default-src 'self'; "
"base-uri 'self'; "
"form-action 'self'; "
"frame-ancestors 'self'; "
"object-src 'none'; "
"script-src 'self' 'unsafe-inline'; "
"style-src 'self' 'unsafe-inline'; "
"img-src 'self' data: https:; "
"font-src 'self' data:; "
"connect-src 'self'; "
"media-src 'self'; "
)

# ============================================================================

# MIDDLEWARE

# ============================================================================

class SecurityHeadersMiddleware(
BaseHTTPMiddleware
):
"""
Add security headers to every HTTP response.

```
This middleware does not terminate TLS and does not attempt to manipulate
forwarded protocol headers. Zoraxy remains responsible for public HTTPS.
"""

async def dispatch(
    self,
    request: Request,
    call_next,
) -> Response:

    response = await call_next(
        request
    )

    # --------------------------------------------------------------------
    # Clickjacking
    # --------------------------------------------------------------------

    response.headers[
        "X-Frame-Options"
    ] = "SAMEORIGIN"

    # --------------------------------------------------------------------
    # MIME sniffing
    # --------------------------------------------------------------------

    response.headers[
        "X-Content-Type-Options"
    ] = "nosniff"

    # --------------------------------------------------------------------
    # Referrer handling
    # --------------------------------------------------------------------

    response.headers[
        "Referrer-Policy"
    ] = "strict-origin-when-cross-origin"

    # --------------------------------------------------------------------
    # Browser permissions
    # --------------------------------------------------------------------

    response.headers[
        "Permissions-Policy"
    ] = (
        "camera=(), "
        "microphone=(), "
        "geolocation=(), "
        "payment=(), "
        "usb=()"
    )

    # --------------------------------------------------------------------
    # Content Security Policy
    # --------------------------------------------------------------------

    response.headers[
        "Content-Security-Policy"
    ] = CONTENT_SECURITY_POLICY

    # --------------------------------------------------------------------
    # Cross-origin isolation policy
    # --------------------------------------------------------------------

    response.headers[
        "Cross-Origin-Opener-Policy"
    ] = "same-origin"

    response.headers[
        "Cross-Origin-Resource-Policy"
    ] = "same-origin"

    # --------------------------------------------------------------------
    # Browser cache behavior for authenticated HTML/API responses
    # --------------------------------------------------------------------

    path = request.url.path

    if (
        path == "/"
        or path == "/login"
        or path.startswith("/api/")
    ):
        # Do not allow a shared intermediary cache to retain authenticated
        # application responses.
        response.headers[
            "Cache-Control"
        ] = (
            "no-store, "
            "max-age=0"
        )

        response.headers[
            "Pragma"
        ] = "no-cache"

    # --------------------------------------------------------------------
    # HSTS
    # --------------------------------------------------------------------

    if (
        ENVIRONMENT == "production"
        and PUBLIC_SCHEME == "https"
    ):
        response.headers[
            "Strict-Transport-Security"
        ] = (
            f"max-age={HSTS_MAX_AGE}; "
            "includeSubDomains"
        )

    # --------------------------------------------------------------------
    # Remove headers that may reveal implementation details.
    #
    # Note:
    # Uvicorn may add its own Server header at the ASGI server layer.
    # Removing it here is therefore best-effort only.
    # --------------------------------------------------------------------

    response.headers.pop(
        "X-Powered-By",
        None,
    )

    response.headers.pop(
        "Server",
        None,
    )

    return response
```

# ============================================================================

# FASTAPI HELPER

# ============================================================================

def add_security_headers_fastapi(
app: FastAPI,
) -> FastAPI:
"""
Install SecurityHeadersMiddleware and return the same application.

```
Returning the app keeps compatibility with existing initialization code.
"""

app.add_middleware(
    SecurityHeadersMiddleware
)

return app
```

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"CONTENT_SECURITY_POLICY",
"HSTS_MAX_AGE",
"SecurityHeadersMiddleware",
"add_security_headers_fastapi",
]
