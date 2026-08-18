from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import (
    FileResponse,
    HTMLResponse,
    JSONResponse,
)
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from app.library import Library


logger = logging.getLogger(__name__)

router = APIRouter()


BASE_DIR = Path(__file__).resolve().parents[2]

templates = Jinja2Templates(
    directory=str(
        BASE_DIR / "app" / "templates"
    )
)


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


def public_item(
    item: dict[str, Any] | None,
) -> dict[str, Any] | None:
    """
    Entfernt interne Dateipfade aus API-Antworten.

    Lokale Poster werden stattdessen über /api/poster/{id}
    bereitgestellt.
    """

    if not item:
        return None

    data = dict(item)

    data.pop("path", None)
    data.pop("nfo_path", None)
    data.pop("poster_path", None)

    media_id = data.get("id")

    if media_id and item.get("poster_path"):
        data["poster_url"] = (
            f"/api/poster/{int(media_id)}"
        )

    elif item.get("poster"):
        poster = str(item["poster"]).strip()

        if (
            poster.startswith("https://")
            or poster.startswith("http://")
        ):
            data["poster_url"] = poster

    data.pop("poster", None)

    return data


@router.get(
    "/api/test"
)
async def test():
    """
    Einfacher API-Test.
    """

    return {
        "status": "ok",
        "message": "Media Roulette läuft",
    }


@router.post(
    "/api/scan"
)
async def scan():
    """
    Manuelle Bibliotheksaktualisierung.
    """

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


@router.get(
    "/api/stats"
)
async def stats():
    """
    Statistiken der Mediabibliothek.
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
            detail=(
                "Statistiken konnten nicht "
                "geladen werden."
            ),
        ) from exc


@router.get(
    "/api/random"
)
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
):
    """
    Zufällige Medienempfehlung.

    exclude enthält Titel, jeweils einen pro Zeile.
    """

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

        # Wenn alle möglichen Ergebnisse ausgeschlossen wurden,
        # darf die Anwendung auf die vollständige Auswahl zurückfallen.
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
                    "message": (
                        "Keine passenden Medien "
                        "gefunden."
                    ),
                },
            )

        public_data = public_item(item)

        if not public_data:
            return JSONResponse(
                status_code=404,
                content={
                    "success": False,
                    "message": (
                        "Das gefundene Medium "
                        "konnte nicht gelesen werden."
                    ),
                },
            )

        logger.info(
            "Recommendation: %s - %s (%s) @ %s",
            public_data.get("kind"),
            public_data.get("title"),
            public_data.get("year", "?"),
            public_data.get("provider", "?"),
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


@router.get(
    "/api/poster/{media_id}"
)
async def poster(media_id: int):
    """
    Liefert ein lokal gespeichertes Poster.

    Der Client darf keinen Dateipfad angeben.
    Dadurch bleibt der Zugriff auf Dateien auf bereits
    vom Scanner ermittelte Poster beschränkt.
    """

    try:
        poster_path = await run_in_threadpool(
            library.poster_for_id,
            media_id,
        )

        if not poster_path:
            raise HTTPException(
                status_code=404,
                detail="Poster nicht gefunden.",
            )

        return FileResponse(
            path=str(poster_path),
            headers={
                "Cache-Control": (
                    "public, max-age=86400"
                )
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
            detail="Poster nicht verfügbar.",
        ) from exc


@router.get(
    "/",
    response_class=HTMLResponse,
)
async def home(
    request: Request,
):
    """
    Hauptseite.
    """

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request,
        },
    )
