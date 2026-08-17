import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router

# Basis-Verzeichnis für Pfade
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Logging konfigurieren
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup/Shutdown Lifecycle Handler"""
    # Startup - Initiale Bibliotheksscannen
    async def run_scan():
        try:
            from app.api.routes import library
            library.scan()
            logger.info("Initial library scan completed successfully")
        except Exception:
            logger.exception("Initial library scan failed")

    asyncio.create_task(run_scan())
    yield
    # Shutdown - optional, wenn später Ressourcen freigegeben werden müssen

app = FastAPI(title="Media Roulette", lifespan=lifespan)

# Statische Dateien mounten (absoluter Pfad)
app.mount("/static", StaticFiles(directory=os.path.join(BASE_DIR, "app", "static")), name="static")

# API-Routes einbinden
app.include_router(router)

@app.get("/health")
async def health_check():
    """Health Check Endpoint für Docker"""
    return {"status": "healthy"}
