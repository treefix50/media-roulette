import logging
import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.library import Library

logger = logging.getLogger(__name__)
router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

library = Library(
    db_path=os.getenv("DATABASE_PATH", "/data/media-roulette.db"),
    movies_dir=os.getenv("MOVIES_DIR", "/data/movies"),
    series_dir=os.getenv("SERIES_DIR", "/data/series"),
)


def public_item(item: dict | None):
    if not item:
        return None
    return {k: v for k, v in item.items() if k not in {"path", "nfo_path"}}


@router.get("/api/test")
async def test():
    return {"status": "ok", "message": "Media Roulette läuft"}


@router.post("/api/scan")
async def scan():
    try:
        count = library.scan()
        return {"success": True, "count": count, "stats": library.stats()}
    except Exception:
        logger.exception("Manual library scan failed")
        return {"success": False, "message": "Bibliothek konnte nicht aktualisiert werden."}


@router.get("/api/stats")
async def stats():
    try:
        return library.stats()
    except Exception:
        logger.exception("Loading stats failed")
        return {"total": 0, "movies": 0, "series": 0, "providers": []}


@router.get("/api/random")
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
):
    try:
        # The client only sends title keys, never filesystem paths.
        exclude_titles = [p for p in (exclude or "").split("\n") if p]
        item = public_item(library.random_item(kind, provider, exclude_titles=exclude_titles))
        if not item and exclude_titles:
            item = public_item(library.random_item(kind, provider))
        if not item:
            return {"success": False, "message": "Keine passenden Medien gefunden."}
        return {"success": True, "item": item}
    except Exception:
        logger.exception("Random recommendation failed")
        return {"success": False, "message": "Keine Empfehlung verfügbar."}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )
