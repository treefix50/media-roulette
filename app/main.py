from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.templating import Jinja2Templates
from fastapi.staticfiles import StaticFiles

from app.api.routes import router


app = FastAPI(
    title="Media Roulette"
)


BASE_DIR = Path(__file__).resolve().parent


templates = Jinja2Templates(
    directory=str(BASE_DIR / "templates")
)


app.mount(
    "/static",
    StaticFiles(directory=str(BASE_DIR / "static")),
    name="static"
)


app.include_router(router)


@app.get("/")
async def home(request: Request):

    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={
            "request": request
        }
    )
