from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse

from fastapi.templating import Jinja2Templates

from app.auth.auth import authenticate


router = APIRouter()


templates = Jinja2Templates(
    directory="app/templates"
)


@router.get("/api/test")
async def test():

    return {
        "status": "ok",
        "message": "Media Roulette läuft"
    }



@router.get("/login", response_class=HTMLResponse)
async def login_page(
    request: Request
):

    return templates.TemplateResponse(
        request=request,
        name="login.html",
        context={}
    )



@router.post("/login")
async def login(
    server_type: str = Form(...),
    server_url: str = Form(...),
    username: str = Form(...),
    password: str = Form(...)
):

    user = await authenticate(
        server_type,
        server_url,
        username,
        password
    )


    if not user:

        return {
            "success": False,
            "message": "Login fehlgeschlagen"
        }


    return {
        "success": True,
        "user": user
    }
