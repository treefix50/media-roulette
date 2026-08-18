from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    HTTPException,
    Request,
)
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.templating import Jinja2Templates
from starlette.concurrency import (
    run_in_threadpool,
)

from app.library import (
    Library,
    POSTER_EXTENSIONS,
)


logger = logging.getLogger(__name__)

router = APIRouter()


# ==============================================================
# PATHS
# ==============================================================

BASE_DIR = Path(
    __file__
).resolve().parents[2]


templates = Jinja2Templates(
    directory=str(
        BASE_DIR
        / "app"
        / "templates"
    )
)


# ==============================================================
# LIBRARY
# ==============================================================

library = Library(
    db_path=os.getenv(
        "DATABASE_PATH",
        "/state/media-roulette.db",
    ),
    movies_dir=os.getenv(
        "MOVIES_DIR",
        "/data/movies",
    ),
    series_dir=os.getenv(
        "SERIES_DIR",
        "/data/series",
    ),
)


# ==============================================================
# PUBLIC API ITEM
# ==============================================================

def public_item(
    item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Erstellt eine sichere öffentliche Darstellung eines
    Media-Eintrags.

    NIEMALS nach außen:

        path
        nfo_path
        poster_path

    Ein lokales Poster wird ausschließlich über:

        /api/poster/{id}

    ausgeliefert.
    """

    if not item:
        return None

    data = dict(
        item
    )

    # ----------------------------------------------------------
    # INTERNE DATEIPFADE ENTFERNEN
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
    # POSTER URL
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
            f"/api/poster/"
            f"{int(media_id)}"
        )

    else:

        poster = item.get(
            "poster"
        )

        if poster:

            poster = str(
                poster
            ).strip()

            # Nur HTTP(S)-URLs als externe Poster erlauben.
            if poster.lower().startswith(
                (
                    "https://",
                    "http://",
                )
            ):

                data["poster_url"] = (
                    poster
                )

    # NFO-Rohwert nicht öffentlich zurückgeben.
    data.pop(
        "poster",
        None,
    )

    return data


# ==============================================================
# TEST
# ==============================================================

@router.get(
    "/api/test"
)
async def test():
    return {
        "status": "ok",
        "message": (
            "Media Roulette läuft"
        ),
    }


# ==============================================================
# SCAN
# ==============================================================

@router.post(
    "/api/scan"
)
async def scan():

    try:

        count = await run_in_threadpool(
            library.scan
        )

        stats = await run_in_threadpool(
            library.stats
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
            detail=(
                "Bibliothek konnte nicht "
                "aktualisiert werden."
            ),
        ) from exc


# ==============================================================
# STATS
# ==============================================================

@router.get(
    "/api/stats"
)
async def stats():

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
            detail=(
                "Statistiken konnten nicht "
                "geladen werden."
            ),
        ) from exc


# ==============================================================
# RANDOM
# ==============================================================

@router.get(
    "/api/random"
)
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
):

    try:

        exclude_titles = [
            title.strip()
            for title
            in (
                exclude or ""
            ).splitlines()
            if title.strip()
        ]

        item = (
            await run_in_threadpool(
                library.random_item,
                kind,
                provider,
                exclude_titles,
            )
        )

        # ------------------------------------------------------
        # Wenn alle Kandidaten ausgeschlossen wurden,
        # wieder auf komplette Auswahl zurückfallen.
        # ------------------------------------------------------

        if (
            not item
            and exclude_titles
        ):

            item = (
                await run_in_threadpool(
                    library.random_item,
                    kind,
                    provider,
                    None,
                )
            )

        if not item:

            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": (
                        "Keine passenden Medien "
                        "gefunden."
                    ),
                },
            )

        public_data = (
            public_item(
                item
            )
        )

        if not public_data:

            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": (
                        "Das gefundene Medium "
                        "konnte nicht gelesen "
                        "werden."
                    ),
                },
            )

        logger.info(
            "Recommendation: %s - %s (%s) @ %s",
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
            detail=(
                "Keine Empfehlung verfügbar."
            ),
        ) from exc


# ==============================================================
# POSTER
# ==============================================================

@router.get(
    "/api/poster/{media_id}"
)
async def poster(
    media_id: int,
):
    """
    Liefert ausschließlich das Poster, das der Scanner
    für die angegebene Media-ID in der Datenbank hinterlegt hat.

    Der Client kann KEINEN Dateipfad angeben.

    Dadurch ist dies kein beliebiger File-Download-Endpoint.
    """

    try:

        poster_path = (
            await run_in_threadpool(
                library.poster_for_id,
                media_id,
            )
        )

        if poster_path is None:

            raise HTTPException(
                status_code=404,
                detail=(
                    "Poster nicht gefunden."
                ),
            )

        # ------------------------------------------------------
        # Zusätzliche Sicherheitsprüfung
        # ------------------------------------------------------

        path = poster_path.resolve()

        if not path.is_file():

            raise HTTPException(
                status_code=404,
                detail=(
                    "Poster nicht gefunden."
                ),
            )

        if (
            path.suffix.lower()
            not in POSTER_EXTENSIONS
        ):

            raise HTTPException(
                status_code=404,
                detail=(
                    "Ungültiges Posterformat."
                ),
            )

        # ------------------------------------------------------
        # MIME TYPE
        # ------------------------------------------------------

        media_types = {
            ".jpg":
                "image/jpeg",

            ".jpeg":
                "image/jpeg",

            ".png":
                "image/png",

            ".webp":
                "image/webp",
        }

        media_type = media_types.get(
            path.suffix.lower()
        )

        if media_type is None:

            raise HTTPException(
                status_code=404,
                detail=(
                    "Ungültiges Posterformat."
                ),
            )

        # ------------------------------------------------------
        # FILE RESPONSE
        # ------------------------------------------------------

        return FileResponse(
            path=str(path),
            media_type=media_type,
            filename=path.name,
            headers={
                "Cache-Control": (
                    "public, "
                    "max-age=86400"
                ),
                "X-Content-Type-Options":
                    "nosniff",
            },
        )

    except HTTPException:
        raise

    except Exception as exc:

        logger.exception(
            "Poster loading failed "
            "for media %s",
            media_id,
        )

        raise HTTPException(
            status_code=404,
            detail=(
                "Poster nicht verfügbar."
            ),
        ) from exc


# ==============================================================
# HOME
# ==============================================================

@router.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request,
):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
        },
    )
