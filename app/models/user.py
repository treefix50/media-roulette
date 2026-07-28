from pydantic import BaseModel, Field
from typing import Optional


class UserSession(BaseModel):
    """
    Gespeicherte Benutzer-Session für Jellyfin / Emby
    """

    server_type: str = Field(
        ...,
        description="Medienserver: jellyfin oder emby"
    )

    server_url: str = Field(
        ...,
        description="URL zum Jellyfin/Emby Server"
    )

    username: str = Field(
        ...,
        description="Benutzername"
    )

    user_id: Optional[str] = Field(
        default=None,
        description="ID des Benutzers im Medienserver"
    )

    token: Optional[str] = Field(
        default=None,
        description="Authentifizierungs-Token"
    )


class LoginRequest(BaseModel):
    """
    Daten vom Login-Formular
    """

    server_type: str = Field(
        ...,
        description="jellyfin oder emby"
    )

    server_url: str = Field(
        ...,
        description="z.B. http://192.168.1.50:8096"
    )

    username: str

    password: str


class LoginResponse(BaseModel):
    """
    Antwort nach erfolgreichem Login
    """

    success: bool

    username: Optional[str] = None

    message: Optional[str] = None
