"""
Media Roulette - lightweight in-process rate limiter.

This module intentionally has no external dependency.

It is designed for the expected deployment:

```
Internet
    |
  Zoraxy
    |
Media Roulette
```

The limiter protects application endpoints from accidental or abusive
request bursts. It is not intended to replace a WAF or proxy-level rate
limiting.

Important:

* State is kept in process memory.
* Restarting Media Roulette clears the limiter.
* Multiple application workers have independent limiters.
* Zoraxy should therefore also provide sensible connection/request limits.
  """

from **future** import annotations

import asyncio
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import HTTPException, Request

# ============================================================================

# CONFIGURATION

# ============================================================================

# Login:

# 10 attempts per 15 minutes per client key.

LOGIN_MAX_REQUESTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60

# General authenticated API:

# 120 requests per minute per client key.

API_MAX_REQUESTS = 120
API_WINDOW_SECONDS = 60

# Library scan is expensive and therefore much more restricted.

SCAN_MAX_REQUESTS = 2
SCAN_WINDOW_SECONDS = 10 * 60

# ============================================================================

# DATA STRUCTURES

# ============================================================================

@dataclass(frozen=True)
class RateLimitConfig:
"""
Rate-limit configuration for one endpoint category.
"""

```
max_requests: int
window_seconds: int
```

class InMemoryRateLimiter:
"""
Small asynchronous in-memory sliding-window rate limiter.

```
The limiter stores timestamps for each key and removes timestamps outside
the active window before evaluating a new request.
"""

def __init__(
    self,
    config: RateLimitConfig,
) -> None:

    if config.max_requests <= 0:
        raise ValueError(
            "max_requests must be greater than zero."
        )

    if config.window_seconds <= 0:
        raise ValueError(
            "window_seconds must be greater than zero."
        )

    self.config = config

    self._requests: dict[
        str,
        Deque[float],
    ] = defaultdict(
        deque
    )

    self._lock = asyncio.Lock()

async def allow(
    self,
    key: str,
) -> bool:
    """
    Register one request and return whether it is allowed.
    """

    now = time.monotonic()

    cutoff = (
        now
        - self.config.window_seconds
    )

    async with self._lock:

        bucket = self._requests[
            key
        ]

        while (
            bucket
            and bucket[0] <= cutoff
        ):
            bucket.popleft()

        if len(bucket) >= (
            self.config.max_requests
        ):
            return False

        bucket.append(
            now
        )

        return True

async def retry_after(
    self,
    key: str,
) -> int:
    """
    Return an approximate number of seconds until the oldest request
    leaves the active window.
    """

    now = time.monotonic()

    cutoff = (
        now
        - self.config.window_seconds
    )

    async with self._lock:

        bucket = self._requests.get(
            key
        )

        if not bucket:
            return 0

        while (
            bucket
            and bucket[0] <= cutoff
        ):
            bucket.popleft()

        if not bucket:
            return 0

        remaining = (
            bucket[0]
            + self.config.window_seconds
            - now
        )

        return max(
            1,
            int(
                remaining
            ),
        )

async def check(
    self,
    key: str,
) -> None:
    """
    Enforce the limiter.

    Raises HTTP 429 when the request is rejected.
    """

    if await self.allow(
        key
    ):
        return

    retry_after = await self.retry_after(
        key
    )

    raise HTTPException(
        status_code=429,
        detail=(
            "Too many requests. "
            "Please try again later."
        ),
        headers={
            "Retry-After": str(
                retry_after
            ),
        },
    )

def clear(self) -> None:
    """
    Clear all stored limiter state.

    Mainly useful for tests.
    """

    self._requests.clear()
```

# ============================================================================

# LIMITERS

# ============================================================================

login_limiter = InMemoryRateLimiter(
RateLimitConfig(
max_requests=LOGIN_MAX_REQUESTS,
window_seconds=LOGIN_WINDOW_SECONDS,
)
)

api_limiter = InMemoryRateLimiter(
RateLimitConfig(
max_requests=API_MAX_REQUESTS,
window_seconds=API_WINDOW_SECONDS,
)
)

scan_limiter = InMemoryRateLimiter(
RateLimitConfig(
max_requests=SCAN_MAX_REQUESTS,
window_seconds=SCAN_WINDOW_SECONDS,
)
)

# ============================================================================

# CLIENT KEY

# ============================================================================

def _normalize_ip(
value: str | None,
) -> str:
"""
Normalize an address for use as a rate-limit key.
"""

```
if not value:
    return "unknown"

value = value.strip()

if not value:
    return "unknown"

# Keep the value opaque. We do not attempt to parse or canonicalize IP
# addresses here because proxy headers may contain IPv4, IPv6, or
# implementation-specific values.
return value[:128]
```

def client_key(
request: Request,
namespace: str,
) -> str:
"""
Build a rate-limit key.

```
Because Media Roulette runs behind Zoraxy, the direct TCP peer may be the
proxy itself. We therefore prefer the application-visible client address
only when it has been explicitly supplied by trusted proxy middleware.

If no such value exists, the Starlette connection peer is used.

This module does NOT blindly trust arbitrary X-Forwarded-For values.
"""

state_client_ip = getattr(
    request.state,
    "client_ip",
    None,
)

if state_client_ip:
    address = _normalize_ip(
        str(state_client_ip)
    )

else:
    client = request.client

    address = _normalize_ip(
        client.host
        if client
        else None
    )

return (
    f"{namespace}:"
    f"{address}"
)
```

# ============================================================================

# DEPENDENCIES

# ============================================================================

async def enforce_login_rate_limit(
request: Request,
) -> None:
"""
FastAPI dependency for login endpoints.
"""

```
await login_limiter.check(
    client_key(
        request,
        "login",
    )
)
```

async def enforce_api_rate_limit(
request: Request,
) -> None:
"""
FastAPI dependency for normal API endpoints.
"""

```
await api_limiter.check(
    client_key(
        request,
        "api",
    )
)
```

async def enforce_scan_rate_limit(
request: Request,
) -> None:
"""
FastAPI dependency for the expensive library scan endpoint.
"""

```
await scan_limiter.check(
    client_key(
        request,
        "scan",
    )
)
```

# ============================================================================

# ASGI MIDDLEWARE

# ============================================================================

class ClientIPMiddleware:
"""
Determine the client IP from a trusted reverse proxy.

```
IMPORTANT:

Do not enable arbitrary forwarded-header trust merely because the
application is behind a proxy. An attacker must not be able to send:

    X-Forwarded-For: victim-ip

directly to Media Roulette and thereby evade rate limiting.

The deployment should set TRUSTED_PROXY_IPS to the IP/CIDR of the Zoraxy
container/network.

If no trusted proxy is configured, the direct socket peer is used.
"""

def __init__(
    self,
    app,
    trusted_proxy_ips: set[str] | None = None,
) -> None:

    self.app = app

    self.trusted_proxy_ips = (
        trusted_proxy_ips
        or set()
    )

async def __call__(
    self,
    scope,
    receive,
    send,
):
    if scope["type"] != "http":
        await self.app(
            scope,
            receive,
            send,
        )
        return

    headers = {
        key.decode(
            "latin-1"
        ).lower(): value.decode(
            "latin-1"
        )
        for key, value in scope.get(
            "headers",
            [],
        )
    }

    client = scope.get(
        "client"
    )

    direct_host = (
        client[0]
        if client
        else None
    )

    client_ip = direct_host

    if (
        direct_host
        and direct_host
        in self.trusted_proxy_ips
    ):
        # Zoraxy should provide the original client in X-Real-IP or
        # X-Forwarded-For. Only trusted proxy peers are allowed to supply
        # these headers.
        forwarded = headers.get(
            "x-real-ip"
        )

        if not forwarded:
            forwarded_for = headers.get(
                "x-forwarded-for"
            )

            if forwarded_for:
                # Standard X-Forwarded-For is:
                #
                # client, proxy1, proxy2
                #
                # The first value represents the originating client.
                forwarded = (
                    forwarded_for
                    .split(",")[0]
                    .strip()
                )

        if forwarded:
            client_ip = forwarded

    scope = dict(
        scope
    )

    state = dict(
        scope.get(
            "state",
            {},
        )
    )

    state["client_ip"] = (
        _normalize_ip(
            client_ip
        )
    )

    scope["state"] = state

    await self.app(
        scope,
        receive,
        send,
    )
```

# ============================================================================

# CLEANUP

# ============================================================================

def clear_rate_limits() -> None:
"""
Clear all limiter state.

```
Useful for automated tests and administrative shutdown/restart handling.
"""

login_limiter.clear()
api_limiter.clear()
scan_limiter.clear()
```

**all** = [
"API_MAX_REQUESTS",
"API_WINDOW_SECONDS",
"ClientIPMiddleware",
"InMemoryRateLimiter",
"LOGIN_MAX_REQUESTS",
"LOGIN_WINDOW_SECONDS",
"RateLimitConfig",
"SCAN_MAX_REQUESTS",
"SCAN_WINDOW_SECONDS",
"api_limiter",
"client_key",
"clear_rate_limits",
"enforce_api_rate_limit",
"enforce_login_rate_limit",
"enforce_scan_rate_limit",
"login_limiter",
"scan_limiter",
]
