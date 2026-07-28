import httpx

from app.services.base import MediaService


class EmbyService(MediaService):

    def __init__(self, server_url: str):
        super().__init__(server_url)



    async def login(
        self,
        username: str,
        password: str
    ):

        url = (
            f"{self.server_url}"
            "/Users/AuthenticateByName"
        )


        data = {
            "Username": username,
            "Pw": password
        }


        async with httpx.AsyncClient() as client:

            response = await client.post(
                url,
                json=data
            )


        if response.status_code != 200:
            return None


        result = response.json()


        return {
            "user_id": result["User"]["Id"],
            "username": result["User"]["Name"],
            "token": result["AccessToken"]
        }



    async def get_movies(
        self,
        user_id,
        token
    ):

        return await self._get_items(
            user_id,
            token,
            "Movie"
        )



    async def get_series(
        self,
        user_id,
        token
    ):

        return await self._get_items(
            user_id,
            token,
            "Series"
        )



    async def _get_items(
        self,
        user_id,
        token,
        item_type
    ):

        url = (
            f"{self.server_url}"
            f"/Users/{user_id}/Items"
        )


        headers = {
            "X-Emby-Token": token
        }


        params = {
            "IncludeItemTypes": item_type,
            "Recursive": "true"
        }


        async with httpx.AsyncClient() as client:

            response = await client.get(
                url,
                headers=headers,
                params=params
            )


        if response.status_code != 200:
            return []


        return response.json().get(
            "Items",
            []
        )
