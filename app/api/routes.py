"""
Media Roulette - API routes.

Authentication:
Signed browser session + CSRF token.

Deployment:
Internet -> Zoraxy -> FastAPI

No JWT/OAuth2 bearer tokens are used.
"""

from **future** import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Form, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.library import Library
from app.rate_limit import (
enforce_api_rate_limit,
enforce_login_rate_limit,
enforce_scan_rate_limit,
)
from app.security import (
authenticate_user,
create_session,
get_or_create_csrf_token,
public_user,
require_auth,
validate_csrf_token,
)

logger = logging.getLogger("media_roulette.api")

router = APIRouter()

# ============================================================================

# LIBRARY

# ============================================================================

library = Library(
db_path=os.getenv(
"DATABASE_PATH",
"/state/media_roulette.db",
),
movies_dir=os.getenv(
"MOVIES_DIR",
"/data/movies",
),
series_dir=os.getenv(
"SERIES_DIR",
"/data/tv",
),
)

# ============================================================================

# HELPERS

# ============================================================================

def public_item(
item: dict[str, Any] | None,
) -> dict[str, Any] | None:
"""
Convert an internal media record into a safe API representation.

```
Internal filesystem paths are never returned to API clients.
"""

if not item:
    return None

data = dict(item)

# ------------------------------------------------------------------------
# Never expose filesystem paths.
# ------------------------------------------------------------------------

data.pop("path", None)
data.pop("nfo_path", None)
data.pop("poster_path", None)

# ------------------------------------------------------------------------
# Local poster.
# ------------------------------------------------------------------------

media_id = data.get("id")
local_poster = item.get("poster_path")

if media_id is not None and local_poster:
    try:
        data["poster_url"] = f"/api/poster/{int(media_id)}"
    except (TypeError, ValueError):
        pass

# ------------------------------------------------------------------------
# Remote poster URL.
# ------------------------------------------------------------------------

if "poster_url" not in data:
    poster = item.get("poster")

    if poster:
        poster = str(poster).strip()

        if poster.lower().startswith(
            (
                "https://",
                "http://",
            )
        ):
            data["poster_url"] = poster

# The raw poster field may contain a local filename or URL.
# It must not be exposed directly.
data.pop("poster", None)

return data
```

def _validate_kind(
kind: str | None,
) -> str | None:
"""
Validate the requested media type.
"""

```
if kind is None:
    return None

normalized = kind.strip().casefold()

if not normalized:
    return None

if normalized not in {
    "movie",
    "series",
}:
    raise HTTPException(
        status_code=400,
        detail="Invalid media type.",
    )

return normalized
```

def _validate_provider(
provider: str | None,
) -> str | None:
"""
Normalize provider input.

```
Provider names are compared case-insensitively by the API.
"""

if provider is None:
    return None

normalized = provider.strip()

if not normalized:
    return None

if len(normalized) > 64:
    raise HTTPException(
        status_code=400,
        detail="Provider value is too long.",
    )

if normalized.casefold() in {
    "alle",
    "all",
    "*",
}:
    return "alle"

return normalized
```

def _parse_exclude(
exclude: str | None,
) -> list[str]:
"""
Convert newline-separated excluded titles into a bounded list.
"""

```
if not exclude:
    return []

titles: list[str] = []

for raw in exclude.splitlines():
    title = raw.strip()

    if not title:
        continue

    if len(title) > 500:
        continue

    if title not in titles:
        titles.append(title)

    # Prevent a huge request parameter from creating a large SQL query.
    if len(titles) >= 100:
        break

return titles
```

# ============================================================================

# AUTHENTICATION

# ============================================================================

@router.post(
"/api/token",
tags=["Auth"],
)
async def login(
request: Request,
username: str = Form(...),
password: str = Form(...),
csrf_token: str | None = Form(default=None),
_rate_limit: None = Depends(
enforce_login_rate_limit
),
):
"""
Authenticate the user and establish a signed browser session.

```
The historic /api/token URL is retained for frontend compatibility.

No OAuth2 bearer token is returned.
"""

# ------------------------------------------------------------------------
# Login CSRF
#
# A first-time unauthenticated browser may not yet have a session-bound
# CSRF token. If the frontend supplies one, validate it. Otherwise the
# login endpoint remains usable for initial authentication.
# ------------------------------------------------------------------------

if csrf_token is not None:
    validate_csrf_token(
        request,
        csrf_token,
    )

username = username.strip()

if not username:
    raise HTTPException(
        status_code=401,
        detail="Incorrect email or password.",
    )

try:
    user = await run_in_threadpool(
        authenticate_user,
        username,
        password,
    )

except ValueError:
    raise HTTPException(
        status_code=401,
        detail="Incorrect email or password.",
    )

if user is None:
    logger.warning(
        "Failed login attempt for %s",
        username[:128],
    )

    raise HTTPException(
        status_code=401,
        detail="Incorrect email or password.",
    )

create_session(
    request,
    user,
)

logger.info(
    "Successful login for %s",
    user.get(
        "email",
        "unknown",
    ),
)

return {
    "success": True,
    "user": public_user(user),
    "csrf_token": get_or_create_csrf_token(request),
}
```

@router.get(
"/api/me",
tags=["Auth"],
)
async def get_current_user_info(
request: Request,
current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Return the currently authenticated user's public information and
session-bound CSRF token.
"""

```
return {
    "authenticated": True,
    "user": public_user(current_user),
    "csrf_token": get_or_create_csrf_token(request),
}
```

@router.post(
"/api/logout",
tags=["Auth"],
)
async def logout(
request: Request,
_current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Destroy the current authenticated session.

```
This endpoint requires a valid authenticated session.
"""

# Import locally to keep the security dependency surface explicit.
from app.security import destroy_session

destroy_session(request)

return {
    "success": True,
}
```

# ============================================================================

# API HEALTH

# ============================================================================

@router.get(
"/api/test",
tags=["System"],
)
async def test():
"""
Lightweight public application health endpoint.

```
This endpoint intentionally does not require authentication so Zoraxy
or Docker can use it as a connectivity/health check.
"""

return {
    "status": "ok",
    "message": "Media Roulette running",
}
```

# ============================================================================

# LIBRARY SCAN

# ============================================================================

@router.post(
"/api/scan",
tags=["Library"],
)
async def scan(
current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_scan_rate_limit
),
):
"""
Trigger a complete library scan.

```
The synchronous filesystem/database scanner runs in a worker thread so
the FastAPI event loop remains responsive.
"""

try:
    count = await run_in_threadpool(
        library.scan
    )

    stats = await run_in_threadpool(
        library.stats
    )

    logger.info(
        "Manual scan triggered by %s",
        current_user.get(
            "email",
            "unknown",
        ),
    )

    return {
        "success": True,
        "count": count,
        "stats": stats,
    }

except RuntimeError as exc:
    # Library.scan() uses a process-local lock and reports a concurrent
    # scan through RuntimeError.
    if "already running" in str(exc).casefold():
        raise HTTPException(
            status_code=409,
            detail="A library scan is already running.",
        ) from exc

    logger.exception(
        "Manual library scan failed"
    )

    raise HTTPException(
        status_code=500,
        detail="Library could not be updated.",
    ) from exc

except Exception as exc:
    logger.exception(
        "Manual library scan failed"
    )

    raise HTTPException(
        status_code=500,
        detail="Library could not be updated.",
    ) from exc
```

# ============================================================================

# LIBRARY STATISTICS

# ============================================================================

@router.get(
"/api/stats",
tags=["Library"],
)
async def stats(
_current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Return current library statistics.

```
Statistics are protected because they reveal information about the
private media library.
"""

try:
    return await run_in_threadpool(
        library.stats
    )

except Exception as exc:
    logger.exception(
        "Loading library statistics failed"
    )

    raise HTTPException(
        status_code=500,
        detail="Statistics could not be loaded.",
    ) from exc
```

# ============================================================================

# PROVIDERS

# ============================================================================

@router.get(
"/api/providers",
tags=["Library"],
)
async def providers(
_current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Return all discovered media providers.
"""

```
try:
    values = await run_in_threadpool(
        library.providers
    )

    return {
        "success": True,
        "providers": values,
    }

except Exception as exc:
    logger.exception(
        "Loading media providers failed"
    )

    raise HTTPException(
        status_code=500,
        detail="Providers could not be loaded.",
    ) from exc
```

# ============================================================================

# RANDOM RECOMMENDATION

# ============================================================================

@router.get(
"/api/random",
tags=["Library"],
)
async def random_media(
kind: str | None = None,
provider: str | None = None,
exclude: str | None = None,
current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Return one random media recommendation.

```
Supported filters:

    kind:
        movie
        series

    provider:
        provider name
        alle / all / *

    exclude:
        newline-separated titles
"""

normalized_kind = _validate_kind(
    kind
)

normalized_provider = _validate_provider(
    provider
)

exclude_titles = _parse_exclude(
    exclude
)

try:
    item = await run_in_threadpool(
        library.random_item,
        normalized_kind,
        normalized_provider,
        exclude_titles,
    )

    # If every matching title was excluded, fall back to the full
    # matching set rather than returning a false "no media" result.
    if item is None and exclude_titles:
        item = await run_in_threadpool(
            library.random_item,
            normalized_kind,
            normalized_provider,
            None,
        )

    if item is None:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "No matching media found.",
            },
        )

    public_data = public_item(
        item
    )

    if public_data is None:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "Media could not be read.",
            },
        )

    logger.info(
        "Recommendation: %s - %s (%s) @ %s by %s",
        public_data.get("kind"),
        public_data.get("title"),
        public_data.get("year", "?"),
        public_data.get("provider", "?"),
        current_user.get(
            "email",
            "unknown",
        ),
    )

    return {
        "success": True,
        "item": public_data,
    }

except HTTPException:
    raise

except Exception as exc:
    logger.exception(
        "Random recommendation failed"
    )

    raise HTTPException(
        status_code=500,
        detail="No recommendation available.",
    ) from exc
```

# ============================================================================

# POSTER

# ============================================================================

@router.get(
"/api/poster/{media_id}",
tags=["Library"],
)
async def poster(
media_id: int,
_current_user: dict[str, Any] = Depends(
require_auth
),
_rate_limit: None = Depends(
enforce_api_rate_limit
),
):
"""
Serve a scanner-approved local poster.

```
Library.poster_for_id() performs the authoritative filesystem path
validation before this endpoint serves the file.
"""

if media_id <= 0:
    raise HTTPException(
        status_code=404,
        detail="Poster not found.",
    )

try:
    poster_path = await run_in_threadpool(
        library.poster_for_id,
        media_id,
    )

    if poster_path is None:
        raise HTTPException(
            status_code=404,
            detail="Poster not found.",
        )

    path = Path(
        poster_path
    ).resolve()

    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail="Poster not found.",
        )

    media_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".webp": "image/webp",
    }

    media_type = media_types.get(
        path.suffix.casefold()
    )

    if media_type is None:
        raise HTTPException(
            status_code=404,
            detail="Poster format is not supported.",
        )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=path.name,
        headers={
            "Cache-Control": "private, max-age=86400",
            "X-Content-Type-Options": "nosniff",
            "Content-Disposition": (
                f'inline; filename="{path.name}"'
            ),
        },
    )

except HTTPException:
    raise

except Exception as exc:
    logger.exception(
        "Poster loading failed for media %s",
        media_id,
    )

    raise HTTPException(
        status_code=404,
        detail="Poster not available.",
    ) from exc
```

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"library",
"public_item",
"router",
]
