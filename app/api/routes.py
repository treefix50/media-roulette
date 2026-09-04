"""
Media Roulette - API routes.

Authentication is session based.

The application is intended to run behind Zoraxy:
Browser -> HTTPS/Zoraxy -> Media Roulette HTTP

There is deliberately no JWT/OAuth2 implementation here anymore.
Authentication is handled through the signed session cookie provided by
Starlette's SessionMiddleware and the helpers in app.security.
"""

from **future** import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import (
APIRouter,
Depends,
Form,
HTTPException,
Request,
)
from fastapi.responses import FileResponse, JSONResponse
from starlette.concurrency import run_in_threadpool

from app.library import Library
from app.main import templates
from app.security import (
authenticate_user,
create_session,
destroy_session,
get_current_user,
get_or_create_csrf_token,
public_user,
require_auth,
validate_csrf_token,
)

logger = logging.getLogger(**name**)

router = APIRouter()

# ============================================================================

# CONFIGURATION

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

# LIBRARY

# ============================================================================

library = Library(
db_path=DATABASE_PATH,
movies_dir=MOVIES_DIR,
series_dir=SERIES_DIR,
)

# ============================================================================

# PUBLIC MEDIA REPRESENTATION

# ============================================================================

def public_item(
item: dict[str, Any] | None,
) -> dict[str, Any] | None:
"""
Convert an internal media database record into a safe API object.

```
Internal filesystem paths are never exposed.

Local posters are served through:
    /api/poster/{id}

Remote poster URLs are intentionally not exposed anymore. This keeps the
Content-Security-Policy strict and prevents the browser from loading
arbitrary remote image resources.
"""

if not item:
    return None

data = dict(item)

# ------------------------------------------------------------------------
# Never expose internal filesystem paths.
# ------------------------------------------------------------------------

data.pop("path", None)
data.pop("nfo_path", None)
data.pop("poster_path", None)

# ------------------------------------------------------------------------
# Poster
# ------------------------------------------------------------------------

media_id = data.get("id")
local_poster = item.get("poster_path")

if media_id is not None and local_poster:
    try:
        data["poster_url"] = f"/api/poster/{int(media_id)}"
    except (TypeError, ValueError):
        pass

# The original NFO poster value may be a local filename or URL.
# It must never be exposed separately.
data.pop("poster", None)

return data
```

# ============================================================================

# AUTHENTICATION

# ============================================================================

@router.post(
"/api/login",
tags=["Auth"],
)
async def login(
request: Request,
username: str = Form(...),
password: str = Form(...),
csrf_token: str | None = Form(None),
):
"""
Authenticate a user and create a signed browser session.

```
The login form is the only place where credentials are accepted.

The password is never stored in the session.
"""

# Login is intentionally protected by CSRF when the browser already has
# a session. For a completely new anonymous browser session, there is no
# previous CSRF token to validate.
existing_csrf = request.session.get(
    "csrf_token"
)

if existing_csrf is not None:
    validate_csrf_token(
        request,
        csrf_token,
    )

user = authenticate_user(
    username,
    password,
)

if user is None:
    raise HTTPException(
        status_code=401,
        detail="Incorrect email or password.",
    )

create_session(
    request,
    user,
)

# Generate a fresh CSRF token after authentication.
get_or_create_csrf_token(
    request,
)

logger.info(
    "Successful login for %s",
    user.get("email", "unknown"),
)

return {
    "success": True,
    "user": public_user(user),
}
```

@router.post(
"/api/logout",
tags=["Auth"],
)
async def logout(
request: Request,
csrf_token: str | None = Form(None),
):
"""
Destroy the current authenticated session.
"""

```
if request.session.get("authenticated"):
    validate_csrf_token(
        request,
        csrf_token,
    )

email = request.session.get(
    "email",
    "unknown",
)

destroy_session(
    request,
)

logger.info(
    "Logout for %s",
    email,
)

return {
    "success": True,
}
```

@router.get(
"/api/csrf",
tags=["Auth"],
)
async def csrf_token(
request: Request,
):
"""
Return the CSRF token for the current browser session.

```
This endpoint does not require authentication because it is also used
before login.
"""

return {
    "csrf_token": get_or_create_csrf_token(
        request,
    ),
}
```

@router.get(
"/api/me",
tags=["Auth"],
)
async def get_current_user_info(
current_user: dict[str, Any] = Depends(
require_auth
),
):
"""
Return the authenticated user's public account information.
"""

```
return public_user(
    current_user,
)
```

# ============================================================================

# HEALTH

# ============================================================================

@router.get(
"/api/test",
tags=["Health"],
include_in_schema=False,
)
async def test():
"""
Legacy compatibility health endpoint.

```
/health in main.py is the preferred health endpoint.
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
):
"""
Trigger a complete library scan.

```
The synchronous filesystem/database scanner runs in a threadpool so the
FastAPI event loop remains responsive.
"""

try:
    count = await run_in_threadpool(
        library.scan,
    )

    stats = await run_in_threadpool(
        library.stats,
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

except Exception as exc:
    logger.exception(
        "Manual library scan failed",
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
current_user: dict[str, Any] = Depends(
require_auth
),
):
"""
Return current library statistics.

```
Statistics are protected because they reveal information about the
private media library.
"""

try:
    result = await run_in_threadpool(
        library.stats,
    )

    return result

except Exception as exc:
    logger.exception(
        "Loading stats failed",
    )

    raise HTTPException(
        status_code=500,
        detail="Statistics could not be loaded.",
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
):
"""
Return one random media recommendation.

```
Supported parameters:

    kind:
        movie
        series

    provider:
        any provider discovered by the library scanner
        "alle" means all providers

    exclude:
        newline-separated titles that should be excluded when possible.
"""

try:
    # --------------------------------------------------------------------
    # Validate kind.
    # --------------------------------------------------------------------

    normalized_kind = (
        kind.strip().casefold()
        if kind
        else None
    )

    if normalized_kind not in {
        None,
        "",
        "movie",
        "series",
    }:
        raise HTTPException(
            status_code=400,
            detail="Invalid media type.",
        )

    if normalized_kind == "":
        normalized_kind = None

    # --------------------------------------------------------------------
    # Normalize provider.
    # --------------------------------------------------------------------

    normalized_provider = (
        provider.strip()
        if provider
        else None
    )

    if normalized_provider == "":
        normalized_provider = None

    # --------------------------------------------------------------------
    # Excluded titles.
    #
    # Limit the amount of user-controlled data passed to the SQL layer.
    # The library itself still uses parameterized queries.
    # --------------------------------------------------------------------

    exclude_titles: list[str] = []

    for title in (
        exclude or ""
    ).splitlines():

        cleaned = title.strip()

        if not cleaned:
            continue

        if len(cleaned) > 500:
            continue

        if cleaned not in exclude_titles:
            exclude_titles.append(
                cleaned
            )

        # Prevent excessively large NOT IN lists.
        if len(exclude_titles) >= 100:
            break

    # --------------------------------------------------------------------
    # First attempt: honor exclusions.
    # --------------------------------------------------------------------

    item = await run_in_threadpool(
        library.random_item,
        normalized_kind,
        normalized_provider,
        exclude_titles,
    )

    # --------------------------------------------------------------------
    # If all matching items were excluded, allow a fallback.
    # --------------------------------------------------------------------

    if (
        not item
        and exclude_titles
    ):
        item = await run_in_threadpool(
            library.random_item,
            normalized_kind,
            normalized_provider,
            None,
        )

    # --------------------------------------------------------------------
    # No result.
    # --------------------------------------------------------------------

    if not item:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "No matching media found.",
            },
        )

    public_data = public_item(
        item,
    )

    if not public_data:
        return JSONResponse(
            status_code=404,
            content={
                "success": False,
                "message": "Found media could not be read.",
            },
        )

    logger.info(
        "Recommendation: %s - %s (%s) @ %s (by %s)",
        public_data.get(
            "kind",
        ),
        public_data.get(
            "title",
        ),
        public_data.get(
            "year",
            "?",
        ),
        public_data.get(
            "provider",
            "?",
        ),
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
        "Random recommendation failed",
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
current_user: dict[str, Any] = Depends(
require_auth
),
):
"""
Serve a locally stored poster.

```
The client cannot supply a filesystem path. The database ID is resolved
through Library.poster_for_id(), which performs the path security checks.
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
        poster_path,
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

    suffix = path.suffix.casefold()

    media_type = media_types.get(
        suffix,
    )

    if media_type is None:
        raise HTTPException(
            status_code=404,
            detail="Invalid poster format.",
        )

    return FileResponse(
        path=str(path),
        media_type=media_type,
        filename=path.name,
        headers={
            # Posters are local, immutable-ish media assets. They can be
            # cached safely for a day. Authentication remains required
            # before the resource can be requested.
            "Cache-Control": (
                "private, max-age=86400"
            ),
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

# PUBLIC EXPORTS

# ============================================================================

**all** = [
"library",
"login",
"logout",
"poster",
"public_item",
"random_media",
"router",
"scan",
"stats",
]
