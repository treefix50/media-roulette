import asyncio
import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router, library

logger = logging.getLogger(__name__)

app = FastAPI(title="Media Roulette")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.on_event("startup")
async def startup_scan():
    async def run_scan():
        try:
            library.scan()
        except Exception:
            logger.exception("Initial library scan failed")

    asyncio.create_task(run_scan())
