from abc import ABC, abstractmethod


class MediaService(ABC):
    """
    Grundklasse für alle Medienserver
    """

    def __init__(self, server_url: str):
        self.server_url = server_url.rstrip("/")


    @abstractmethod
    async def login(
        self,
        username: str,
        password: str
    ):
        """
        Benutzer authentifizieren
        """
        pass


    @abstractmethod
    async def get_movies(
        self,
        user_id: str,
        token: str
    ):
        """
        Filme abrufen
        """
        pass


    @abstractmethod
    async def get_series(
        self,
        user_id: str,
        token: str
    ):
        """
        Serien abrufen
        """
        pass
