from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
    Depends,
    BackgroundTasks,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.security import OAuth2PasswordRequestForm
from starlette.concurrency import run_in_threadpool
from pydantic import BaseModel

from app.library import Library
from app.security import (
    get_current_user,
    require_auth,
    get_db,
    authenticate_user,
    create_access_token,
    Token,
    get_password_hash,
    User,
    Session,
)

logger = logging.getLogger(__name__)

router = APIRouter()

# ── Paths ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).resolve().parents[2]

# ── Library ────────────────────────────────────────────────────
library = Library(
    db_path=os.getenv("DATABASE_PATH", "/state/media-roulette.db"),
    movies_dir=os.getenv("MOVIES_DIR", "/data/movies"),
    series_dir=os.getenv("SERIES_DIR", "/data/series"),
)


# ── Helper: Public Item Sanitization ───────────────────────────
def public_item(item: dict[str, Any] | None) -> dict[str, Any] | None:
    """Create safe public representation of media item"""
    if not item:
        return None

    data = dict(item)
    data.pop("path", None)
    data.pop("nfo_path", None)
    data.pop("poster_path", None)

    media_id = data.get("id")
    local_poster = item.get("poster_path")

    if media_id is not None and local_poster:
        data["poster_url"] = f"/api/poster/{int(media_id)}"
    else:
        poster = item.get("poster")
        if poster:
            poster = str(poster).strip()
            if poster.lower().startswith(("https://", "http://")):
                data["poster_url"] = poster

    data.pop("poster", None)
    return data


# ── Authentication Endpoints ───────────────────────────────────
class LoginForm(BaseModel):
    username: str
    password: str
    remember_me: bool = False

@router.post("/api/token", response_model=Token, tags=["Auth"])
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db),
):
    """Login and receive JWT token"""
    user = authenticate_user(db, form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=401,
            detail="Incorrect email or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    access_token = create_access_token(
        data={"sub": user.email},
        expires_delta=None,  # Uses ACCESS_TOKEN_EXPIRE_MINUTES
    )

    return Token(access_token=access_token, token_type="bearer")

@router.get("/api/me", response_model=dict, tags=["Auth"])
async def get_current_user_info(
    current_user: User = Depends(require_auth),
):
    """Get current user information"""
    return {
        "email": current_user.email,
        "is_admin": current_user.is_admin,
        "is_active": current_user.is_active,
    }


# ── API Endpoints ──────────────────────────────────────────────
@router.get("/api/test")
async def test():
    """Health check for testing"""
    return {"status": "ok", "message": "Media Roulette running"}

@router.post("/api/scan", tags=["Library"])
async def scan(
    background_tasks: BackgroundTasks,
    current_user: User = Depends(require_auth),  # Requires auth
):
    """Trigger library scan - requires authentication"""
    try:
        count = await run_in_threadpool(library.scan)
        stats = await run_in_threadpool(library.stats)
        logger.info(f"Manual scan triggered by {current_user.email}")
        return {"success": True, "count": count, "stats": stats}
    except Exception as exc:
        logger.exception("Manual library scan failed")
        raise HTTPException(
            status_code=500,
            detail="Library could not be updated",
        ) from exc

@router.get("/api/stats", tags=["Library"])
async def stats(
    # Optional: Uncomment to require auth
    # current_user: User = Depends(require_auth),
):
    """Get library statistics"""
    try:
        return await run_in_threadpool(library.stats)
    except Exception as exc:
        logger.exception("Loading stats failed")
        raise HTTPException(
            status_code=500,
            detail="Statistics could not be loaded",
        ) from exc

@router.get("/api/random", tags=["Library"])
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
    current_user: User = Depends(require_auth),  # Requires auth
):
    """Get random media recommendation - requires authentication"""
    try:
        exclude_titles = [
            title.strip()
            for title in (exclude or "").splitlines()
            if title.strip()
        ]

        item = await run_in_threadpool(
            library.random_item,
            kind,
            provider,
            exclude_titles,
        )

        if not item and exclude_titles:
            item = await run_in_threadpool(
                library.random_item,
                kind,
                provider,
                None,
            )

        if not item:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": "No matching media found",
                },
            )

        public_data = public_item(item)

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
            public_data.get("kind"),
            public_data.get("title"),
            public_data.get("year", "?"),
            public_data.get("provider", "?"),
            current_user.email,
        )

        return {
            "success": True,
            "item": public_data,
        }

    except Exception as exc:
        logger.exception("Random recommendation failed")
        raise HTTPException(
            status_code=500,
            detail="No recommendation available",
        ) from exc

@router.get("/api/poster/{media_id}", tags=["Library"])
async def poster(media_id: int):
    """Serve poster image for given media ID"""
    try:
        poster_path = await run_in_threadpool(library.poster_for_id, media_id)

        if poster_path is None:
            raise HTTPException(status_code=404, detail="Poster not found")

        path = poster_path.resolve()

        if not path.is_file():
            raise HTTPException(status_code=404, detail="Poster not found")

        if path.suffix.lower() not in {".jpg", ".jpeg", ".png", ".webp"}:
            raise HTTPException(status_code=404, detail="Invalid poster format")

        media_types = {
            ".jpg": "image/jpeg",
            ".jpeg": "image/jpeg",
            ".png": "image/png",
            ".webp": "image/webp",
        }

        media_type = media_types.get(path.suffix.lower())

        if media_type is None:
            raise HTTPException(status_code=404, detail="Invalid poster format")

        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=path.name,
            headers={
                "Cache-Control": "public, max-age=86400",
                "X-Content-Type-Options": "nosniff",
            },
        )

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Poster loading failed for media %s", media_id)
        raise HTTPException(status_code=404, detail="Poster not available") from exc
