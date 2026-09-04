"""
Media Roulette - lightweight in-process rate limiter.

Expected deployment:

```
Internet
    |
  Zoraxy
    |
Media Roulette
    |
   br0
```

The limiter is intentionally dependency-free.

Important:

* Rate-limit state is kept in process memory.
* Restarting Media Roulette clears the limiter.
* Multiple application workers have independent limiters.
* Zoraxy should additionally provide sensible proxy-level protection.
* Forwarded client-IP headers are trusted ONLY when the direct network peer
  belongs to TRUSTED_PROXY_IPS.
  """

from **future** import annotations

import asyncio
import ipaddress
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Deque

from fastapi import HTTPException, Request

# ============================================================================

# CONFIGURATION

# ============================================================================

# Login:

# 10 attempts per 15 minutes per client IP.

LOGIN_MAX_REQUESTS = 10
LOGIN_WINDOW_SECONDS = 15 * 60

# General authenticated API:

# 120 requests per minute per client IP.

API_MAX_REQUESTS = 120
API_WINDOW_SECONDS = 60

# Library scan:

# Maximum 2 scans per 10 minutes per client IP.

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
Small asynchronous sliding-window rate limiter.

```
Timestamps are stored per rate-limit key. Entries older than the active
window are removed before a request is evaluated.
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
    Return the approximate number of seconds until the oldest request
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

def clear(
    self,
) -> None:
    """
    Clear all limiter state.

    Primarily useful for tests.
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

# IP HELPERS

# ============================================================================

def _normalize_ip(
value: str | None,
) -> str:
"""
Normalize an address for use as a rate-limit key.

```
IPv4 and IPv6 addresses are canonicalized where possible.

Unknown/unusable values are mapped to "unknown".
"""

if not value:
    return "unknown"

value = value.strip()

if not value:
    return "unknown"

# Remove an accidental surrounding IPv6 bracket notation.
if (
    value.startswith("[")
    and value.endswith("]")
):
    value = value[1:-1]

try:
    return str(
        ipaddress.ip_address(
            value
        )
    )

except ValueError:
    # Keep implementation-specific values opaque but bounded.
    return value[:128]
```

def _parse_networks(
values: set[str] | None,
) -> tuple[
tuple[
ipaddress.IPv4Network
| ipaddress.IPv6Network,
...
],
tuple[str, ...],
]:
"""
Parse trusted proxy IPs and CIDR networks.

```
Invalid entries are ignored.

The second returned tuple contains invalid entries so callers/tests can
inspect the configuration if required.
"""

networks: list[
    ipaddress.IPv4Network
    | ipaddress.IPv6Network
] = []

invalid: list[str] = []

for raw_value in (
    values or set()
):

    value = raw_value.strip()

    if not value:
        continue

    try:
        networks.append(
            ipaddress.ip_network(
                value,
                strict=False,
            )
        )

    except ValueError:

        invalid.append(
            value
        )

return (
    tuple(
        networks
    ),
    tuple(
        invalid
    ),
)
```

def _address_is_trusted_proxy(
address: str | None,
trusted_networks: tuple[
ipaddress.IPv4Network
| ipaddress.IPv6Network,
...
],
) -> bool:
"""
Return True when the direct peer belongs to a trusted proxy network.
"""

```
if not address:
    return False

try:
    ip = ipaddress.ip_address(
        address
    )

except ValueError:
    return False

for network in trusted_networks:

    if ip.version != network.version:
        continue

    if ip in network:
        return True

return False
```

# ============================================================================

# CLIENT KEY

# ============================================================================

def client_key(
request: Request,
namespace: str,
) -> str:
"""
Build a rate-limit key.

```
ClientIPMiddleware stores the trusted client address in
request.state.client_ip.

If that value does not exist, the direct socket peer is used.
"""

state_client_ip = getattr(
    request.state,
    "client_ip",
    None,
)

if state_client_ip:

    address = _normalize_ip(
        str(
            state_client_ip
        )
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

# FASTAPI DEPENDENCIES

# ============================================================================

async def enforce_login_rate_limit(
request: Request,
) -> None:
"""
Rate-limit authentication attempts.
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
Rate-limit normal API requests.
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
Rate-limit expensive library scans.
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

# ASGI CLIENT-IP MIDDLEWARE

# ============================================================================

class ClientIPMiddleware:
"""
Determine the original client IP from a trusted reverse proxy.

```
Security model:

1. The direct TCP peer is identified first.
2. Forwarded headers are considered ONLY if that peer is trusted.
3. X-Real-IP has priority.
4. Otherwise the first X-Forwarded-For address is used.
5. If the proxy is not trusted, forwarded headers are ignored entirely.

This prevents an attacker from directly submitting:

    X-Forwarded-For: trusted-or-victim-ip

to bypass rate limiting.

TRUSTED_PROXY_IPS may contain individual addresses or CIDR networks,
for example:

    192.168.1.100
    192.168.1.0/24
    10.0.0.0/8
"""

def __init__(
    self,
    app,
    trusted_proxy_ips: set[str] | None = None,
) -> None:

    self.app = app

    self.trusted_proxy_ips = {
        value.strip()
        for value in (
            trusted_proxy_ips
            or set()
        )
        if value
        and value.strip()
    }

    (
        self._trusted_networks,
        self.invalid_trusted_proxy_ips,
    ) = _parse_networks(
        self.trusted_proxy_ips
    )

async def __call__(
    self,
    scope,
    receive,
    send,
):
    """
    Process an ASGI request scope.
    """

    if scope["type"] != "http":

        await self.app(
            scope,
            receive,
            send,
        )

        return

    client = scope.get(
        "client"
    )

    direct_host = (
        client[0]
        if client
        else None
    )

    client_ip = direct_host

    # --------------------------------------------------------------------
    # Extract request headers.
    # --------------------------------------------------------------------

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

    # --------------------------------------------------------------------
    # Only trust forwarded headers from a configured proxy.
    # --------------------------------------------------------------------

    if _address_is_trusted_proxy(
        direct_host,
        self._trusted_networks,
    ):

        forwarded = headers.get(
            "x-real-ip"
        )

        if not forwarded:

            forwarded_for = headers.get(
                "x-forwarded-for"
            )

            if forwarded_for:

                # Standard X-Forwarded-For format:
                #
                # client, proxy1, proxy2
                #
                # The first address represents the originating client.
                forwarded = (
                    forwarded_for
                    .split(",")[0]
                    .strip()
                )

        if forwarded:

            normalized_forwarded = (
                _normalize_ip(
                    forwarded
                )
            )

            if normalized_forwarded != "unknown":
                client_ip = normalized_forwarded

    # --------------------------------------------------------------------
    # Copy the ASGI scope because it is immutable by convention.
    # --------------------------------------------------------------------

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

    state["direct_peer_ip"] = (
        _normalize_ip(
            direct_host
        )
    )

    state["trusted_proxy"] = (
        _address_is_trusted_proxy(
            direct_host,
            self._trusted_networks,
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
Primarily useful for automated tests.
"""

login_limiter.clear()
api_limiter.clear()
scan_limiter.clear()
```

# ============================================================================

# EXPORTS

# ============================================================================

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
