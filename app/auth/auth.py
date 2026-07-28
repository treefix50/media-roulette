from app.services.jellyfin import JellyfinService
from app.services.emby import EmbyService


def get_media_service(server_type: str, server_url: str):

    server_type = server_type.lower()

    if server_type == "jellyfin":
        return JellyfinService(server_url)

    if server_type == "emby":
        return EmbyService(server_url)

    raise ValueError("Nicht unterstützter Mediaserver")


async def authenticate(
    server_type: str,
    server_url: str,
    username: str,
    password: str
):

    service = get_media_service(
        server_type,
        server_url
    )

    user = await service.login(
        username,
        password
    )

    if not user:
        return None

    return {
        "server_type": server_type,
        "server_url": server_url,
        "user_id": user["user_id"],
        "username": user["username"],
        "token": user["token"]
    }
