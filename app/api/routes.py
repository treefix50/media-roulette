from fastapi import APIRouter


router = APIRouter()


@router.get("/api/test")
async def test():

    return {
        "status": "ok",
        "message": "Media Roulette läuft"
    }
