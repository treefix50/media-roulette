from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    HTTPException,
)
from fastapi.responses import (
    FileResponse,
    JSONResponse,
)
from fastapi.security import OAuth2PasswordRequestForm
from starlette.concurrency import run_in_threadpool

from app.library import Library
from app.main import templates
from app.security import (
    Token,
    authenticate_user,
    create_access_token,
    require_auth,
)


logger = logging.getLogger(__name__)

router = APIRouter()


# ==============================================================
# PATHS
# ==============================================================

BASE_DIR = Path(__file__).resolve().parents[2]


# ==============================================================
# LIBRARY
# ==============================================================

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


# ==============================================================
# PUBLIC MEDIA REPRESENTATION
# ==============================================================

def public_item(
    item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Creates the safe public representation of a media item.

    Internal filesystem paths are never exposed through the API.

    Local posters are served through /api/poster/{id}.
    Remote poster URLs from the NFO are only returned when no
    local poster is available.
    """

    if not item:
        return None

    data = dict(item)

    # ----------------------------------------------------------
    # Never expose internal filesystem paths.
    # ----------------------------------------------------------

    data.pop(
        "path",
        None,
    )

    data.pop(
        "nfo_path",
        None,
    )

    data.pop(
        "poster_path",
        None,
    )

    # ----------------------------------------------------------
    # Poster
    # ----------------------------------------------------------

    media_id = data.get(
        "id"
    )

    local_poster = item.get(
        "poster_path"
    )

    if (
        media_id is not None
        and local_poster
    ):
        data["poster_url"] = (
            f"/api/poster/{int(media_id)}"
        )

    else:
        poster = item.get(
            "poster"
        )

        if poster:
            poster = str(
                poster
            ).strip()

            if poster.lower().startswith(
                (
                    "https://",
                    "http://",
                )
            ):
                data["poster_url"] = poster

    # The original poster field may contain a local filename
    # or an external URL. It should not be exposed separately.
    data.pop(
        "poster",
        None,
    )

    return data


# ==============================================================
# AUTHENTICATION
# ==============================================================

@router.post(
    "/api/token",
    response_model=Token,
    tags=["Auth"],
)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
):
    """
    Authenticate a user and return a JWT access token.
    """

    user = authenticate_user(
        form_data.username,
        form_data.password,
    )

    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={
                "WWW-Authenticate": "Bearer",
            },
        )

    access_token = create_access_token(
        data={
            "sub": user["email"],
        },
        expires_delta=None,
    )

    return Token(
        access_token=access_token,
        token_type="bearer",
    )


@router.get(
    "/api/me",
    response_model=dict,
    tags=["Auth"],
)
async def get_current_user_info(
    current_user: dict = Depends(
        require_auth
    ),
):
    """
    Return the currently authenticated user's
    public account information.
    """

    return {
        "email": current_user["email"],
        "is_admin": bool(
            current_user.get(
                "is_admin"
            )
        ),
        "is_active": bool(
            current_user.get(
                "is_active"
            )
        ),
    }


# ==============================================================
# HEALTH CHECK
# ==============================================================

@router.get(
    "/api/test",
)
async def test():
    """
    Basic application health check.
    """

    return {
        "status": "ok",
        "message": "Media Roulette running",
    }


# ==============================================================
# LIBRARY SCAN
# ==============================================================

@router.post(
    "/api/scan",
    tags=["Library"],
)
async def scan(
    current_user: dict = Depends(
        require_auth
    ),
):
    """
    Trigger a complete library scan.

    Scanning is performed in a threadpool because the library
    scanner performs synchronous filesystem and SQLite operations.
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

    except Exception as exc:
        logger.exception(
            "Manual library scan failed"
        )

        raise HTTPException(
            status_code=500,
            detail="Library could not be updated",
        ) from exc


# ==============================================================
# LIBRARY STATISTICS
# ==============================================================

@router.get(
    "/api/stats",
    tags=["Library"],
)
async def stats():
    """
    Return current library statistics.
    """

    try:
        return await run_in_threadpool(
            library.stats
        )

    except Exception as exc:
        logger.exception(
            "Loading stats failed"
        )

        raise HTTPException(
            status_code=500,
            detail="Statistics could not be loaded",
        ) from exc


# ==============================================================
# RANDOM RECOMMENDATION
# ==============================================================

@router.get(
    "/api/random",
    tags=["Library"],
)
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
    current_user: dict = Depends(
        require_auth
    ),
):
    """
    Return one random media recommendation.

    Optional parameters:

        kind
            movie / series

        provider
            provider name

        exclude
            newline-separated titles that should preferably
            be excluded from the recommendation.
    """

    try:
        # ------------------------------------------------------
        # Excluded titles
        # ------------------------------------------------------

        exclude_titles = [
            title.strip()
            for title in (
                exclude or ""
            ).splitlines()
            if title.strip()
        ]

        # ------------------------------------------------------
        # Recommendation
        # ------------------------------------------------------

        item = await run_in_threadpool(
            library.random_item,
            kind,
            provider,
            exclude_titles,
        )

        # ------------------------------------------------------
        # If every matching item was excluded, preserve the
        # existing behaviour and allow a fallback recommendation.
        # ------------------------------------------------------

        if (
            not item
            and exclude_titles
        ):
            item = await run_in_threadpool(
                library.random_item,
                kind,
                provider,
                None,
            )

        # ------------------------------------------------------
        # Nothing found
        # ------------------------------------------------------

        if not item:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "No matching media found",
                },
            )

        # ------------------------------------------------------
        # Remove internal data before returning the item.
        # ------------------------------------------------------

        public_data = public_item(
            item
        )

        if not public_data:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "Found media could not be read",
                },
            )

        logger.info(
            "Recommendation: %s - %s (%s) @ %s (by %s)",
            public_data.get(
                "kind"
            ),
            public_data.get(
                "title"
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

    except Exception as exc:
        logger.exception(
            "Random recommendation failed"
        )

        raise HTTPException(
            status_code=500,
            detail="No recommendation available",
        ) from exc


# ==============================================================
# POSTER
# ==============================================================

@router.get(
    "/api/poster/{media_id}",
    tags=["Library"],
)
async def poster(
    media_id: int,
):
    """
    Serve a locally stored poster for a media item.

    The actual path is resolved by Library.poster_for_id(),
    which performs the filesystem security checks.
    """

    try:
        poster_path = await run_in_threadpool(
            library.poster_for_id,
            media_id,
        )

        if poster_path is None:
            raise HTTPException(
                status_code=404,
                detail="Poster not found",
            )

        # ------------------------------------------------------
        # Resolve once more before serving.
        # ------------------------------------------------------

        path = poster_path.resolve()

        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="Poster not found",
            )

        # ------------------------------------------------------
        # Only supported image formats.
        # ------------------------------------------------------

        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }

        suffix = path.suffix.lower()

        media_type = media_types.get(
            suffix
        )

        if media_type is None:
            raise HTTPException(
                status_code=404,
                detail="Invalid poster format",
            )

        # ------------------------------------------------------
        # Serve poster.
        # ------------------------------------------------------

        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=path.name,
            headers={
                "Cache-Control": (
                    "public, max-age=86400"
                ),
                "X-Content-Type-Options": "nosniff",
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
            detail="Poster not available",
        ) from exc
