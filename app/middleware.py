"""
Media Roulette - HTTP security middleware.

Media Roulette is intended to run behind a reverse proxy such as Zoraxy.

The reverse proxy is responsible for:

* Public DNS
* TLS certificates
* HTTPS termination
* Public HTTP -> HTTPS redirection

This application is responsible for:

* HTTP security headers
* browser-side security policy
* preventing MIME sniffing
* clickjacking protection
* referrer policy
* cache control for authenticated application pages

No Flask-specific configuration is used here.
"""

from **future** import annotations

import os
from collections.abc import Awaitable, Callable

from fastapi import FastAPI, Request, Response
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

# ============================================================================

# CONFIGURATION

# ============================================================================

ENVIRONMENT = os.getenv(
"ENVIRONMENT",
"production",
).strip().lower()

# When Media Roulette is deployed behind Zoraxy, the browser-facing scheme is

# normally HTTPS even though the connection from Zoraxy to the container may

# be plain HTTP.

PUBLIC_SCHEME = os.getenv(
"PUBLIC_SCHEME",
"https" if ENVIRONMENT == "production" else "http",
).strip().lower()

# Optional public hostname, used only for documentation/diagnostics and future

# policy extensions. It is deliberately not inserted into response headers.

PUBLIC_HOST = os.getenv(
"PUBLIC_HOST",
"",
).strip()

# ============================================================================

# SECURITY HEADERS

# ============================================================================

def _content_security_policy() -> str:
"""
Build the Content-Security-Policy.

```
The current application keeps its JavaScript and CSS local to Media
Roulette. Inline scripts/styles are intentionally not allowed by the
policy.

Remote poster images are not allowed by default. Posters should therefore
be served through the application's local poster endpoint.

If a future implementation intentionally needs remote images, the CSP
should be changed explicitly rather than silently permitting all origins.
"""

return "; ".join(
    [
        "default-src 'self'",
        "base-uri 'self'",
        "form-action 'self'",
        "frame-ancestors 'none'",
        "object-src 'none'",
        "script-src 'self'",
        "style-src 'self'",
        "img-src 'self' data:",
        "font-src 'self'",
        "connect-src 'self'",
        "media-src 'self'",
        "worker-src 'self'",
    ]
)
```

def security_headers(
response: Response,
) -> Response:
"""
Add security-related response headers.

```
Existing values are replaced intentionally so that every response gets a
consistent policy.
"""

response.headers["X-Content-Type-Options"] = "nosniff"

response.headers["X-Frame-Options"] = "DENY"

response.headers["Referrer-Policy"] = (
    "strict-origin-when-cross-origin"
)

response.headers["Permissions-Policy"] = (
    "accelerometer=(), "
    "autoplay=(), "
    "camera=(), "
    "display-capture=(), "
    "fullscreen=(), "
    "geolocation=(), "
    "gyroscope=(), "
    "magnetometer=(), "
    "microphone=(), "
    "midi=(), "
    "payment=(), "
    "usb=()"
)

response.headers["Content-Security-Policy"] = (
    _content_security_policy()
)

# Prevent authenticated application data from being cached by shared
# intermediaries.
response.headers["Cache-Control"] = (
    "no-store"
)

# HSTS belongs on the application only when the public endpoint is HTTPS.
# Since Zoraxy terminates TLS, this header is still appropriate for browser
# clients once the production deployment is HTTPS-only.
if (
    ENVIRONMENT == "production"
    and PUBLIC_SCHEME == "https"
):
    response.headers["Strict-Transport-Security"] = (
        "max-age=31536000; includeSubDomains"
    )

# X-XSS-Protection is intentionally not emitted. It is obsolete in modern
# browsers and can create undesirable behavior in older clients.

return response
```

# ============================================================================

# FASTAPI MIDDLEWARE

# ============================================================================

class SecurityHeadersMiddleware(BaseHTTPMiddleware):
"""
Add security headers to every HTTP response.
"""

```
async def dispatch(
    self,
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    response = await call_next(request)

    return security_headers(
        response,
    )
```

def add_security_headers_fastapi(
app: FastAPI,
) -> FastAPI:
"""
Register the security middleware on a FastAPI application.

```
This helper keeps main.py concise and provides a single place for
application-wide security-header configuration.
"""

app.add_middleware(
    SecurityHeadersMiddleware,
)

return app
```

# ============================================================================

# COMPATIBILITY ALIAS

# ============================================================================

# Kept as a small compatibility layer for code that may still import the

# previous middleware helper name.

SecurityMiddleware = SecurityHeadersMiddleware

**all** = [
"PUBLIC_HOST",
"PUBLIC_SCHEME",
"SecurityHeadersMiddleware",
"SecurityMiddleware",
"add_security_headers_fastapi",
"security_headers",
]
