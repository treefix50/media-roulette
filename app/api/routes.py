import os

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.library import Library

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
    count = library.scan()
    return {"success": True, "count": count, "stats": library.stats()}


@router.get("/api/stats")
async def stats():
    return library.stats()


@router.get("/api/random")
async def random_media(kind: str | None = None, provider: str | None = None):
    item = public_item(library.random_item(kind, provider))
    if not item:
        return {"success": False, "message": "Keine passenden Medien gefunden."}
    return {"success": True, "item": item}


@router.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={"request": request},
    )
