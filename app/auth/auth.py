from app.services.jellyfin import JellyfinService
from app.services.emby import EmbyService


def get_media_service(
    server_type: str,
    server_url: str
):

    if server_type.lower() == "jellyfin":

        return JellyfinService(
            server_url
        )


    if server_type.lower() == "emby":

        return EmbyService(
            server_url
        )


    raise ValueError(
        "Unbekannter Medienserver"
    )



async def authenticate(
    server_type,
    server_url,
    username,
    password
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
        **user
    }
