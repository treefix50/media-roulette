from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

from app.api.routes import library, router


BASE_DIR = Path(__file__).resolve().parent.parent
STATIC_DIR = BASE_DIR / "app" / "static"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)

logger = logging.getLogger("media-roulette")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Application lifecycle.

    Der Bibliotheksscan wird vor der Annahme von Requests ausgeführt.
    Dadurch gibt es keinen Race-Condition-Zustand, bei dem die Oberfläche
    bereits verfügbar ist, während die Datenbank noch aufgebaut wird.
    """

    logger.info("Starting Media Roulette")

    try:
        total = await run_in_threadpool(library.scan)
        logger.info("Initial library scan completed: %s media items", total)
    except Exception:
        # Ein Scanfehler soll die Weboberfläche nicht komplett unbrauchbar
        # machen. Bereits vorhandene Daten in SQLite bleiben erhalten.
        logger.exception("Initial library scan failed")

    yield

    logger.info("Stopping Media Roulette")


app = FastAPI(
    title="Media Roulette",
    version="1.0.0",
    lifespan=lifespan,
)

app.mount(
    "/static",
    StaticFiles(directory=str(STATIC_DIR)),
    name="static",
)

app.include_router(router)


@app.get("/health")
async def health_check():
    """
    Docker-/Unraid-Healthcheck.
    """

    try:
        stats = await run_in_threadpool(library.stats)

        return {
            "status": "healthy",
            "database": "ok",
            "media": stats["total"],
        }

    except Exception:
        logger.exception("Health check failed")

        return {
            "status": "degraded",
            "database": "error",
        }
