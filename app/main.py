from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from app.api.routes import router, library

app = FastAPI(title="Media Roulette")
app.mount("/static", StaticFiles(directory="app/static"), name="static")
app.include_router(router)


@app.on_event("startup")
async def startup_scan():
    library.scan()
