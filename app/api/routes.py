import logging
import os
from typing import Any

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from app.library import Library

logger = logging.getLogger(__name__)

router = APIRouter()

# Basis-Verzeichnis für Template-Pfad
BASE_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "app", "templates"))

# Library-Instanz mit Umgebungsvariablen
library = Library(
    db_path=os.getenv("DATABASE_PATH", "/state/media-roulette.db"),
    movies_dir=os.getenv("MOVIES_DIR", "/data/movies"),
    series_dir=os.getenv("SERIES_DIR", "/data/series"),
)

def public_item(item: dict | None) -> dict[str, Any] | None:
    """Filtert interne Felder (path, nfo_path) aus API-Antworten."""
    if not item:
        return None
    return {k: v for k, v in item.items() if k not in {"path", "nfo_path"}}

@router.get("/api/test")
async def test():
    """Health Test Endpoint"""
    return {"status": "ok", "message": "Media Roulette läuft"}

@router.post("/api/scan")
async def scan():
    """Manuelle Bibliotheks-Aktualisierung"""
    try:
        count = library.scan()
        stats = library.stats()
        return {"success": True, "count": count, "stats": stats}
    except Exception:
        logger.exception("Manual library scan failed")
        raise HTTPException(status_code=500, detail="Bibliothek konnte nicht aktualisiert werden.")

@router.get("/api/stats")
async def stats():
    """Statistiken der Mediabibliothek"""
    try:
        return library.stats()
    except Exception:
        logger.exception("Loading stats failed")
        raise HTTPException(status_code=500, detail="Statistiken konnten nicht geladen werden.")

@router.get("/api/random")
async def random_media(
    kind: str | None = None,
    provider: str | None = None,
    exclude: str | None = None,
):
    """Zufällige Medien-Empfehlung"""
    try:
        exclude_titles = [p for p in (exclude or "").split("\n") if p]
        item = public_item(library.random_item(kind, provider, exclude_titles=exclude_titles))
        
        if not item and exclude_titles:
            # Retry ohne Exclude-Liste bei leerem Ergebnis
            item = public_item(library.random_item(kind, provider))

        if not item:
            return JSONResponse(
                status_code=404,
                content={"success": False, "message": "Keine passenden Medien gefunden."}
            )

        return {"success": True, "item": item}

    except Exception:
        logger.exception("Random recommendation failed")
        raise HTTPException(status_code=500, detail="Keine Empfehlung verfügbar.")

@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    """Hauptseite"""
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )
