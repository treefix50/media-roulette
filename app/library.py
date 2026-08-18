from __future__ import annotations

import hashlib
import ipaddress
import logging
import os
import re
import sqlite3
import threading
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


# ==============================================================
# KONFIGURATION
# ==============================================================

VIDEO_EXTENSIONS = {
    ".mkv",
    ".mp4",
    ".m4v",
    ".avi",
    ".mov",
    ".wmv",
    ".webm",
    ".ts",
    ".m2ts",
}


# --------------------------------------------------------------
# FESTE PROVIDER
# --------------------------------------------------------------

PROVIDERS = {
    "max",
    "prime",
    "peacock",
    "paramount",
    "appletv",
    "appletvplus",
    "disney",
    "netflix",
    "sky",
}


# --------------------------------------------------------------
# POSTER
# --------------------------------------------------------------

POSTER_FILENAMES = (
    "poster.jpg",
    "poster.jpeg",
    "poster.png",
    "poster.webp",
    "folder.jpg",
    "folder.jpeg",
    "folder.png",
    "folder.webp",
    "cover.jpg",
    "cover.jpeg",
    "cover.png",
    "cover.webp",
    "movie.jpg",
    "movie.jpeg",
    "movie.png",
    "movie.webp",
    "tvshow.jpg",
    "tvshow.jpeg",
    "tvshow.png",
    "tvshow.webp",
)

POSTER_MAX_BYTES = 10 * 1024 * 1024

POSTER_TIMEOUT = 15

POSTER_CACHE_DIRNAME = "poster_cache"


# ==============================================================
# HILFSFUNKTIONEN
# ==============================================================

def _clean(value: str | None) -> str | None:
    if value is None:
        return None

    value = re.sub(
        r"\s+",
        " ",
        value,
    ).strip()

    return value or None


def _nfo_value(
    root: ET.Element,
    name: str,
) -> str | None:
    node = root.find(name)

    if node is None:
        return None

    return _clean(node.text)


def _parse_number(value: Any) -> float | None:
    if value is None:
        return None

    try:
        return float(
            str(value).replace(",", ".")
        )
    except (TypeError, ValueError):
        return None


# ==============================================================
# NFO
# ==============================================================

def parse_nfo(
    path: Path | None,
) -> dict[str, Any]:
    """
    Liest Metadaten aus einer NFO.

    Fehlerhafte NFOs dürfen niemals den kompletten
    Bibliotheksscan abbrechen.
    """

    if path is None:
        return {}

    try:
        if not path.is_file():
            return {}
    except OSError:
        return {}

    try:
        root = ET.parse(path).getroot()

    except (
        ET.ParseError,
        OSError,
        IOError,
    ):
        logger.warning(
            "Unable to parse NFO: %s",
            path,
        )
        return {}

    data: dict[str, Any] = {}

    # ----------------------------------------------------------
    # Standardfelder
    # ----------------------------------------------------------

    for field in (
        "title",
        "originaltitle",
        "year",
        "plot",
        "runtime",
        "rating",
        "premiered",
        "tmdbid",
        "imdbid",
    ):

        value = _nfo_value(
            root,
            field,
        )

        if value:
            data[field] = value

    # ----------------------------------------------------------
    # Genres
    # ----------------------------------------------------------

    genres: list[str] = []

    for node in root.findall("genre"):

        value = _clean(
            node.text
        )

        if value:
            genres.append(value)

    if genres:

        data["genres"] = ", ".join(
            dict.fromkeys(genres)
        )

    # ----------------------------------------------------------
    # POSTER AUS NFO
    #
    # Unterstützt:
    #
    # <thumb>...</thumb>
    # <thumb aspect="poster">...</thumb>
    #
    # sowie:
    #
    # <art>
    #     <poster>...</poster>
    # </art>
    # ----------------------------------------------------------

    poster: str | None = None

    # Zuerst explizit aspect="poster"
    for thumb in root.findall("thumb"):

        aspect = (
            thumb.attrib.get("aspect") or ""
        ).strip().casefold()

        value = _clean(
            thumb.text
        )

        if (
            value
            and aspect == "poster"
        ):
            poster = value
            break

    # Danach <thumb> ohne aspect.
    #
    # Einige NFOs verwenden einfach:
    #
    # <thumb>poster.jpg</thumb>
    #
    if not poster:

        for thumb in root.findall("thumb"):

            aspect = (
                thumb.attrib.get("aspect") or ""
            ).strip().casefold()

            value = _clean(
                thumb.text
            )

            if (
                value
                and not aspect
            ):
                poster = value
                break

    # Danach <art><poster>
    if not poster:

        art = root.find("art")

        if art is not None:

            poster_node = art.find(
                "poster"
            )

            if poster_node is not None:

                poster = _clean(
                    poster_node.text
                )

    if poster:
        data["poster"] = poster

    # ----------------------------------------------------------
    # Rating
    # ----------------------------------------------------------

    rating = _parse_number(
        data.get("rating")
    )

    if rating is not None:

        if 0 < rating <= 5:
            rating *= 2

        rating = max(
            0.0,
            min(10.0, rating),
        )

    data["rating"] = rating

    # ----------------------------------------------------------
    # Runtime
    # ----------------------------------------------------------

    runtime = _parse_number(
        data.get("runtime")
    )

    if runtime is not None:

        runtime = int(
            round(runtime)
        )

        if 0 < runtime < 100:
            runtime *= 60

        runtime = max(
            1,
            runtime,
        )

    data["runtime"] = runtime

    # ----------------------------------------------------------
    # Jahr
    # ----------------------------------------------------------

    year = data.get("year")

    if year:

        match = re.search(
            r"(19\d{2}|20\d{2})",
            str(year),
        )

        if match:

            data["year"] = int(
                match.group(1)
            )

        else:

            data["year"] = None

    return data


# ==============================================================
# NAMEN
# ==============================================================

def parse_name(
    name: str,
) -> tuple[str, int | None]:

    text = Path(name).stem

    text = re.sub(
        r"[._]+",
        " ",
        text,
    )

    match = re.search(
        r"\b(19\d{2}|20\d{2})\b(?:\s*\(\1\))?",
        text,
    )

    year = (
        int(match.group(1))
        if match
        else None
    )

    if match:

        text = text[
            :match.start()
        ].strip(
            " -()."
        )

    text = re.sub(
        r"\s+",
        " ",
        text,
    ).strip()

    text = re.sub(
        r"[()\[\]]+$",
        "",
        text,
    ).strip()

    return (
        text or Path(name).stem,
        year,
    )


# ==============================================================
# LIBRARY
# ==============================================================

class Library:
    """
    Verwaltung der lokalen Medienbibliothek.

    Filme:

        /movies/PROVIDER/FILM/

    Serien:

        /tv/PROVIDER/SERIE/

    Es werden ausschließlich Provider aus PROVIDERS verarbeitet.

    Serien werden als Serienordner gespeichert:

        /tv/max/Power Book IV Force/

    und NICHT als einzelne Episode.
    """

    def __init__(
        self,
        db_path: str,
        movies_dir: str,
        series_dir: str,
    ):

        self.db_path = Path(
            db_path
        ).expanduser()

        self.movies_dir = Path(
            movies_dir
        ).expanduser()

        self.series_dir = Path(
            series_dir
        ).expanduser()

        self._scan_lock = (
            threading.Lock()
        )

        # ----------------------------------------------------------
        # Poster-Cache
        #
        # Der Cache liegt absichtlich NICHT im Medienordner.
        # ----------------------------------------------------------

        self.poster_cache_dir = (
            self.db_path.parent
            / POSTER_CACHE_DIRNAME
        )

        self._prepare_secure_storage()

        self._init_db()

    # ==============================================================
    # SECURE STORAGE
    # ==============================================================

    def _prepare_secure_storage(
        self,
    ) -> None:
        """
        Erstellt DB- und Poster-Verzeichnis mit möglichst
        restriktiven Dateirechten.

        Unter Unix/Linux:

            Verzeichnis = 0700
            Dateien      = 0600

        Unter Windows greifen diese POSIX-Modi nur eingeschränkt;
        dort muss zusätzlich das Dateisystem-/NTFS-Berechtigungsmodell
        verwendet werden.
        """

        try:

            self.db_path.parent.mkdir(
                parents=True,
                exist_ok=True,
            )

            self.poster_cache_dir.mkdir(
                parents=True,
                exist_ok=True,
            )

            self._chmod_directory(
                self.db_path.parent
            )

            self._chmod_directory(
                self.poster_cache_dir
            )

            if self.db_path.exists():

                self._chmod_file(
                    self.db_path
                )

        except OSError:

            logger.warning(
                "Unable to fully harden database storage permissions.",
                exc_info=True,
            )

    @staticmethod
    def _chmod_directory(
        path: Path,
    ) -> None:

        try:

            if os.name != "nt":

                path.chmod(
                    0o700
                )

        except OSError:
            pass

    @staticmethod
    def _chmod_file(
        path: Path,
    ) -> None:

        try:

            if os.name != "nt":

                path.chmod(
                    0o600
                )

        except OSError:
            pass

    # ==============================================================
    # PROVIDERS
    # ==============================================================

    @staticmethod
    def _normalize_provider(
        provider: str | None,
    ) -> str | None:

        if provider is None:
            return None

        normalized = (
            provider.strip().casefold()
        )

        if not normalized:
            return None

        if normalized not in PROVIDERS:
            return None

        return normalized

    @staticmethod
    def _provider_dirs(
        root_dir: Path,
    ) -> list[
        tuple[str, Path]
    ] | None:

        try:

            if not root_dir.exists():
                return None

            if not root_dir.is_dir():
                return None

            result: list[
                tuple[str, Path]
            ] = []

            for child in root_dir.iterdir():

                if not child.is_dir():
                    continue

                provider = (
                    Library._normalize_provider(
                        child.name
                    )
                )

                if provider is None:

                    logger.debug(
                        "Ignoring unsupported provider directory: %s",
                        child,
                    )

                    continue

                result.append(
                    (
                        provider,
                        child,
                    )
                )

            result.sort(
                key=lambda item:
                item[0].casefold()
            )

            return result

        except OSError:

            logger.exception(
                "Unable to read provider directory: %s",
                root_dir,
            )

            return None

    # ==============================================================
    # DATABASE
    # ==============================================================

    def _connect(
        self,
    ) -> sqlite3.Connection:

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
        )

        conn.row_factory = sqlite3.Row

        conn.execute(
            "PRAGMA busy_timeout=30000"
        )

        conn.execute(
            "PRAGMA journal_mode=WAL"
        )

        conn.execute(
            "PRAGMA synchronous=NORMAL"
        )

        conn.execute(
            "PRAGMA foreign_keys=ON"
        )

        # ----------------------------------------------------------
        # DB-Datei wieder absichern.
        # ----------------------------------------------------------

        self._chmod_file(
            self.db_path
        )

        return conn

    def _init_db(
        self,
    ) -> None:

        with self._connect() as db:

            db.execute(
                """
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    kind TEXT NOT NULL,
                    provider TEXT NOT NULL,
                    title TEXT NOT NULL,
                    year INTEGER,
                    plot TEXT,
                    genres TEXT,
                    rating REAL,
                    runtime INTEGER,
                    path TEXT NOT NULL UNIQUE,
                    nfo_path TEXT,
                    tmdbid TEXT,
                    imdbid TEXT,
                    poster TEXT,
                    poster_path TEXT,
                    updated_at TEXT
                        DEFAULT CURRENT_TIMESTAMP
                )
                """
            )

            columns = {
                row["name"]
                for row in db.execute(
                    "PRAGMA table_info(media)"
                ).fetchall()
            }

            migrations = {

                "tmdbid":
                    "ALTER TABLE media "
                    "ADD COLUMN tmdbid TEXT",

                "imdbid":
                    "ALTER TABLE media "
                    "ADD COLUMN imdbid TEXT",

                "poster":
                    "ALTER TABLE media "
                    "ADD COLUMN poster TEXT",

                "poster_path":
                    "ALTER TABLE media "
                    "ADD COLUMN poster_path TEXT",
            }

            for column, statement in (
                migrations.items()
            ):

                if column not in columns:

                    db.execute(
                        statement
                    )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_media_kind
                ON media(kind)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_media_provider
                ON media(provider)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_media_title
                ON media(title)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_media_rating
                ON media(rating)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS
                idx_media_path
                ON media(path)
                """
            )

        self._chmod_file(
            self.db_path
        )

    # ==============================================================
    # FILESYSTEM
    # ==============================================================

    @staticmethod
    def _iter_dirs(
        path: Path,
    ) -> list[Path] | None:

        try:

            if not path.exists():
                return None

            if not path.is_dir():
                return None

            return sorted(
                (
                    child
                    for child in path.iterdir()
                    if child.is_dir()
                ),
                key=lambda item:
                    item.name.casefold(),
            )

        except OSError:

            logger.exception(
                "Unable to read directory: %s",
                path,
            )

            return None

    @staticmethod
    def _find_nfo(
        directory: Path,
        preferred_name: str,
    ) -> Path | None:

        preferred = (
            directory
            / preferred_name
        )

        try:

            if preferred.is_file():
                return preferred

            nfos = sorted(
                (
                    item
                    for item in directory.iterdir()
                    if item.is_file()
                    and item.suffix.lower()
                    == ".nfo"
                ),
                key=lambda item:
                    item.name.casefold(),
            )

            return (
                nfos[0]
                if nfos
                else None
            )

        except OSError:
            return None

    @staticmethod
    def _find_first_video(
        directory: Path,
    ) -> Path | None:

        try:

            videos = sorted(
                (
                    path
                    for path in directory.rglob("*")
                    if path.is_file()
                    and path.suffix.lower()
                    in VIDEO_EXTENSIONS
                ),
                key=lambda path:
                    str(path).casefold(),
            )

            return (
                videos[0]
                if videos
                else None
            )

        except OSError:

            logger.exception(
                "Unable to scan videos: %s",
                directory,
            )

            return None

    # ==============================================================
    # POSTER - LOCAL
    # ==============================================================

    @staticmethod
    def _find_local_poster(
        directory: Path,
        nfo_poster: str | None,
    ) -> Path | None:
        """
        Sucht zuerst das explizit in der NFO angegebene Poster.

        Danach bekannte lokale Posterdateien.

        Unterstützt auch file://-URLs.
        """

        if nfo_poster:

            value = nfo_poster.strip()

            # ------------------------------------------------------
            # file://
            # ------------------------------------------------------

            if value.casefold().startswith(
                "file://"
            ):

                try:

                    parsed = urllib.parse.urlparse(
                        value
                    )

                    candidate = Path(
                        urllib.request.url2pathname(
                            parsed.path
                        )
                    )

                except (
                    ValueError,
                    OSError,
                ):

                    candidate = None

            else:

                candidate = Path(
                    value
                )

            if candidate is not None:

                if not candidate.is_absolute():

                    candidate = (
                        directory
                        / candidate
                    )

                try:

                    if candidate.is_file():

                        return candidate.resolve()

                except OSError:
                    pass

        # ----------------------------------------------------------
        # Standardnamen
        # ----------------------------------------------------------

        for filename in POSTER_FILENAMES:

            candidate = (
                directory
                / filename
            )

            try:

                if candidate.is_file():

                    return candidate.resolve()

            except OSError:
                continue

        return None

    # ==============================================================
    # POSTER - URL SECURITY
    # ==============================================================

    @staticmethod
    def _is_public_hostname(
        hostname: str | None,
    ) -> bool:
        """
        Verhindert Poster-Downloads zu lokalen/private IPs.

        Damit kann eine manipulierte NFO nicht einfach versuchen,
        interne Dienste wie:

            127.0.0.1
            localhost
            192.168.x.x
            10.x.x.x
            172.16.x.x

        anzusprechen.

        DNS-Auflösung wird bewusst ebenfalls geprüft.
        """

        if not hostname:
            return False

        hostname = hostname.strip()

        if not hostname:
            return False

        if hostname.casefold() in {
            "localhost",
            "localhost.localdomain",
        }:
            return False

        try:

            ip = ipaddress.ip_address(
                hostname
            )

            return (
                not ip.is_private
                and not ip.is_loopback
                and not ip.is_link_local
                and not ip.is_reserved
                and not ip.is_multicast
            )

        except ValueError:
            pass

        try:

            import socket

            addresses = socket.getaddrinfo(
                hostname,
                None,
            )

            for address in addresses:

                ip_text = address[4][0]

                ip = ipaddress.ip_address(
                    ip_text
                )

                if (
                    ip.is_private
                    or ip.is_loopback
                    or ip.is_link_local
                    or ip.is_reserved
                    or ip.is_multicast
                ):
                    return False

            return bool(addresses)

        except OSError:

            return False

    @classmethod
    def _poster_url_allowed(
        cls,
        value: str,
    ) -> bool:

        try:

            parsed = urllib.parse.urlparse(
                value
            )

        except ValueError:

            return False

        if parsed.scheme.casefold() not in {
            "http",
            "https",
        }:
            return False

        if not parsed.hostname:
            return False

        return cls._is_public_hostname(
            parsed.hostname
        )

    # ==============================================================
    # POSTER - REMOTE DOWNLOAD
    # ==============================================================

    def _poster_cache_path(
        self,
        url: str,
    ) -> Path:

        digest = hashlib.sha256(
            url.encode(
                "utf-8"
            )
        ).hexdigest()

        return (
            self.poster_cache_dir
            / f"{digest}.img"
        )

    @staticmethod
    def _detect_image_extension(
        data: bytes,
    ) -> str | None:

        # JPEG
        if data.startswith(
            b"\xff\xd8\xff"
        ):
            return ".jpg"

        # PNG
        if data.startswith(
            b"\x89PNG\r\n\x1a\n"
        ):
            return ".png"

        # WEBP
        if (
            data.startswith(b"RIFF")
            and data[8:12] == b"WEBP"
        ):
            return ".webp"

        return None

    def _download_poster(
        self,
        url: str,
    ) -> Path | None:
        """
        Lädt ein NFO-Poster einmalig in den privaten Poster-Cache.

        Nur JPEG, PNG und WEBP werden akzeptiert.

        Maximale Größe:
            POSTER_MAX_BYTES
        """

        if not self._poster_url_allowed(
            url
        ):

            logger.warning(
                "Rejected unsafe poster URL: %s",
                url,
            )

            return None

        base_cache_path = (
            self._poster_cache_path(
                url
            )
        )

        # ----------------------------------------------------------
        # Bereits gecacht
        # ----------------------------------------------------------

        for extension in (
            ".jpg",
            ".png",
            ".webp",
        ):

            cached = (
                base_cache_path.with_suffix(
                    extension
                )
            )

            try:

                if cached.is_file():

                    if cached.stat().st_size > 0:

                        return cached

            except OSError:
                continue

        # ----------------------------------------------------------
        # Download
        # ----------------------------------------------------------

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent":
                    "Media-Roulette/1.0",
                "Accept":
                    "image/jpeg,image/png,image/webp,*/*;q=0.5",
            },
        )

        temp_path = (
            base_cache_path.with_suffix(
                ".tmp"
            )
        )

        try:

            with urllib.request.urlopen(
                request,
                timeout=POSTER_TIMEOUT,
            ) as response:

                content_type = (
                    response.headers.get(
                        "Content-Type",
                        "",
                    )
                    .split(";")[0]
                    .strip()
                    .casefold()
                )

                if content_type not in {
                    "image/jpeg",
                    "image/jpg",
                    "image/png",
                    "image/webp",
                    "",
                }:

                    logger.warning(
                        "Rejected poster with content type %s: %s",
                        content_type,
                        url,
                    )

                    return None

                total = 0

                with open(
                    temp_path,
                    "wb",
                ) as output:

                    while True:

                        chunk = response.read(
                            64 * 1024
                        )

                        if not chunk:
                            break

                        total += len(
                            chunk
                        )

                        if total > POSTER_MAX_BYTES:

                            logger.warning(
                                "Poster too large: %s",
                                url,
                            )

                            return None

                        output.write(
                            chunk
                        )

            temp_path.chmod(
                0o600
                if os.name != "nt"
                else 0o666
            )

            temp_path_data = (
                temp_path.read_bytes()
            )

            extension = (
                self._detect_image_extension(
                    temp_path_data
                )
            )

            if extension is None:

                logger.warning(
                    "Downloaded poster is not a supported image: %s",
                    url,
                )

                return None

            final_path = (
                base_cache_path.with_suffix(
                    extension
                )
            )

            os.replace(
                temp_path,
                final_path,
            )

            self._chmod_file(
                final_path
            )

            return final_path

        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
        ):

            logger.warning(
                "Unable to download poster: %s",
                url,
                exc_info=True,
            )

            return None

        finally:

            try:

                if temp_path.exists():
                    temp_path.unlink()

            except OSError:
                pass

    # ==============================================================
    # POSTER - RESOLVER
    # ==============================================================

    def _resolve_poster(
        self,
        directory: Path,
        nfo_poster: str | None,
    ) -> tuple[
        str | None,
        Path | None,
    ]:
        """
        Liefert:

            poster
                Originalwert aus der NFO

            poster_path
                IMMER möglichst lokaler Pfad

        Das ist der entscheidende Teil für die Anzeige.
        """

        if nfo_poster:

            value = nfo_poster.strip()

            # ------------------------------------------------------
            # HTTP/HTTPS
            # ------------------------------------------------------

            if value.casefold().startswith(
                (
                    "http://",
                    "https://",
                )
            ):

                cached = (
                    self._download_poster(
                        value
                    )
                )

                if cached:

                    return (
                        value,
                        cached,
                    )

                # Kein lokaler Cache möglich.
                # Originalwert bleibt trotzdem erhalten.
                return (
                    value,
                    None,
                )

            # ------------------------------------------------------
            # Lokale Datei
            # ------------------------------------------------------

            local = (
                self._find_local_poster(
                    directory,
                    value,
                )
            )

            if local:

                return (
                    value,
                    local,
                )

        # ----------------------------------------------------------
        # Kein brauchbarer NFO-Eintrag:
        # lokale Standardposter suchen.
        # ----------------------------------------------------------

        local = (
            self._find_local_poster(
                directory,
                None,
            )
        )

        if local:

            return (
                None,
                local,
            )

        return (
            nfo_poster,
            None,
        )

    # ==============================================================
    # ITEM BUILDER
    # ==============================================================

    def _build_item(
        self,
        *,
        kind: str,
        provider: str,
        media_dir: Path,
        preferred_nfo: str,
    ) -> dict[str, Any] | None:

        normalized_provider = (
            self._normalize_provider(
                provider
            )
        )

        if normalized_provider is None:
            return None

        first_video = (
            self._find_first_video(
                media_dir
            )
        )

        if first_video is None:
            return None

        nfo = self._find_nfo(
            media_dir,
            preferred_nfo,
        )

        data = parse_nfo(
            nfo
        )

        title = data.get(
            "title"
        )

        year = data.get(
            "year"
        )

        if not title:

            title, name_year = (
                parse_name(
                    media_dir.name
                )
            )

            if year is None:
                year = name_year

        # ----------------------------------------------------------
        # POSTER
        # ----------------------------------------------------------

        poster, poster_path = (
            self._resolve_poster(
                media_dir,
                data.get("poster"),
            )
        )

        # ----------------------------------------------------------
        # Pfad
        # ----------------------------------------------------------

        if kind == "series":
            media_path = media_dir
        else:
            media_path = first_video

        try:

            resolved_media_path = (
                media_path.resolve()
            )

        except OSError:

            return None

        try:

            resolved_nfo_path = (
                nfo.resolve()
                if nfo
                else None
            )

        except OSError:

            resolved_nfo_path = None

        try:

            resolved_poster_path = (
                poster_path.resolve()
                if poster_path
                else None
            )

        except OSError:

            resolved_poster_path = None

        return {
            "kind": kind,

            "provider":
                normalized_provider,

            "title":
                title
                or media_dir.name,

            "year":
                year,

            "plot":
                data.get("plot"),

            "genres":
                data.get("genres"),

            "rating":
                data.get("rating"),

            "runtime":
                data.get("runtime"),

            "path":
                str(
                    resolved_media_path
                ),

            "nfo_path":
                (
                    str(
                        resolved_nfo_path
                    )
                    if resolved_nfo_path
                    else None
                ),

            "tmdbid":
                data.get("tmdbid"),

            "imdbid":
                data.get("imdbid"),

            # Originaler NFO-Wert
            "poster":
                poster,

            # Lokale, tatsächlich anzeigbare Datei
            "poster_path":
                (
                    str(
                        resolved_poster_path
                    )
                    if resolved_poster_path
                    else None
                ),
        }

    # ==============================================================
    # MOVIES
    # ==============================================================

    def _scan_movies(
        self,
        root_dir: Path,
    ) -> list[
        dict[str, Any]
    ] | None:

        provider_dirs = (
            self._provider_dirs(
                root_dir
            )
        )

        if provider_dirs is None:
            return None

        items: list[
            dict[str, Any]
        ] = []

        for (
            provider,
            provider_dir,
        ) in provider_dirs:

            movie_dirs = (
                self._iter_dirs(
                    provider_dir
                )
            )

            if movie_dirs is None:

                logger.warning(
                    "Unable to read movie provider: %s",
                    provider_dir,
                )

                continue

            for movie_dir in movie_dirs:

                item = (
                    self._build_item(
                        kind="movie",
                        provider=provider,
                        media_dir=movie_dir,
                        preferred_nfo="movie.nfo",
                    )
                )

                if item:
                    items.append(
                        item
                    )

        logger.info(
            "Movie scan found %d movies",
            len(items),
        )

        return items

    # ==============================================================
    # SERIES
    # ==============================================================

    def _scan_series(
        self,
        root_dir: Path,
    ) -> list[
        dict[str, Any]
    ] | None:

        provider_dirs = (
            self._provider_dirs(
                root_dir
            )
        )

        if provider_dirs is None:
            return None

        items: list[
            dict[str, Any]
        ] = []

        for (
            provider,
            provider_dir,
        ) in provider_dirs:

            series_dirs = (
                self._iter_dirs(
                    provider_dir
                )
            )

            if series_dirs is None:

                logger.warning(
                    "Unable to read series provider: %s",
                    provider_dir,
                )

                continue

            for series_dir in series_dirs:

                item = (
                    self._build_item(
                        kind="series",
                        provider=provider,
                        media_dir=series_dir,
                        preferred_nfo="tvshow.nfo",
                    )
                )

                if item:
                    items.append(
                        item
                    )

        logger.info(
            "Series scan found %d series",
            len(items),
        )

        return items

    # ==============================================================
    # DATABASE UPDATE
    # ==============================================================

    @staticmethod
    def _upsert_items(
        db: sqlite3.Connection,
        items: list[
            dict[str, Any]
        ],
    ) -> None:

        for item in items:

            db.execute(
                """
                INSERT INTO media (
                    kind,
                    provider,
                    title,
                    year,
                    plot,
                    genres,
                    rating,
                    runtime,
                    path,
                    nfo_path,
                    tmdbid,
                    imdbid,
                    poster,
                    poster_path,
                    updated_at
                )
                VALUES (
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    ?,
                    CURRENT_TIMESTAMP
                )
                ON CONFLICT(path)
                DO UPDATE SET
                    kind=excluded.kind,
                    provider=excluded.provider,
                    title=excluded.title,
                    year=excluded.year,
                    plot=excluded.plot,
                    genres=excluded.genres,
                    rating=excluded.rating,
                    runtime=excluded.runtime,
                    nfo_path=excluded.nfo_path,
                    tmdbid=excluded.tmdbid,
                    imdbid=excluded.imdbid,
                    poster=excluded.poster,
                    poster_path=excluded.poster_path,
                    updated_at=CURRENT_TIMESTAMP
                """,
                (
                    item["kind"],
                    item["provider"],
                    item["title"],
                    item["year"],
                    item["plot"],
                    item["genres"],
                    item["rating"],
                    item["runtime"],
                    item["path"],
                    item["nfo_path"],
                    item["tmdbid"],
                    item["imdbid"],
                    item["poster"],
                    item["poster_path"],
                ),
            )

    # ==============================================================
    # CLEANUP
    # ==============================================================

    @staticmethod
    def _delete_missing_from_root(
        db: sqlite3.Connection,
        root_dir: Path,
        items: list[
            dict[str, Any]
        ],
    ) -> None:

        try:

            root = (
                root_dir.resolve()
            )

        except OSError:

            return

        current_paths = {
            str(
                Path(
                    item["path"]
                ).resolve()
            )
            for item in items
        }

        db.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS
            scan_paths (
                path TEXT PRIMARY KEY
            )
            """
        )

        db.execute(
            "DELETE FROM scan_paths"
        )

        if current_paths:

            db.executemany(
                """
                INSERT OR IGNORE INTO
                scan_paths(path)
                VALUES (?)
                """,
                (
                    (path,)
                    for path in current_paths
                ),
            )

        db.execute(
            """
            DELETE FROM media
            WHERE path GLOB ?
              AND path NOT IN (
                  SELECT path
                  FROM scan_paths
              )
            """,
            (
                f"{root}/*",
            ),
        )

    # ==============================================================
    # FULL SCAN
    # ==============================================================

    def scan(
        self,
    ) -> int:

        if not self._scan_lock.acquire(
            blocking=False
        ):

            return self.stats()[
                "total"
            ]

        try:

            movie_items = (
                self._scan_movies(
                    self.movies_dir
                )
            )

            series_items = (
                self._scan_series(
                    self.series_dir
                )
            )

            with self._connect() as db:

                db.execute(
                    "BEGIN"
                )

                # --------------------------------------------------
                # Filme
                # --------------------------------------------------

                if movie_items is not None:

                    self._upsert_items(
                        db,
                        movie_items,
                    )

                    self._delete_missing_from_root(
                        db,
                        self.movies_dir,
                        movie_items,
                    )

                else:

                    logger.warning(
                        "Movie directory unavailable. "
                        "Existing movie records preserved."
                    )

                # --------------------------------------------------
                # Serien
                # --------------------------------------------------

                if series_items is not None:

                    self._upsert_items(
                        db,
                        series_items,
                    )

                    self._delete_missing_from_root(
                        db,
                        self.series_dir,
                        series_items,
                    )

                else:

                    logger.warning(
                        "Series directory unavailable. "
                        "Existing series records preserved."
                    )

                db.commit()

                total = db.execute(
                    """
                    SELECT COUNT(*)
                    FROM media
                    """
                ).fetchone()[0]

                movies = db.execute(
                    """
                    SELECT COUNT(*)
                    FROM media
                    WHERE kind = 'movie'
                    """
                ).fetchone()[0]

                series = db.execute(
                    """
                    SELECT COUNT(*)
                    FROM media
                    WHERE kind = 'series'
                    """
                ).fetchone()[0]

                logger.info(
                    "Library scan finished: "
                    "%d total, %d movies, %d series",
                    total,
                    movies,
                    series,
                )

                return int(
                    total
                )

        except Exception:

            logger.exception(
                "Library scan failed."
            )

            raise

        finally:

            self._scan_lock.release()

    # ==============================================================
    # RANDOM
    # ==============================================================

    def random_item(
        self,
        kind: str | None = None,
        provider: str | None = None,
        exclude_titles: list[
            str
        ] | None = None,
    ) -> dict[
        str, Any
    ] | None:

        query = """
            SELECT *
            FROM media
            WHERE 1=1
        """

        params: list[
            Any
        ] = []

        # ----------------------------------------------------------
        # Kind
        # ----------------------------------------------------------

        normalized_kind = (
            kind.strip().lower()
            if kind
            else ""
        )

        if normalized_kind in {
            "movie",
            "series",
        }:

            query += """
                AND kind = ?
            """

            params.append(
                normalized_kind
            )

        # ----------------------------------------------------------
        # Provider
        # ----------------------------------------------------------

        if provider:

            normalized_provider = (
                provider.strip().casefold()
            )

            if normalized_provider != "alle":

                if (
                    normalized_provider
                    not in PROVIDERS
                ):
                    return None

                query += """
                    AND provider = ?
                """

                params.append(
                    normalized_provider
                )

        # ----------------------------------------------------------
        # Ausschlüsse
        # ----------------------------------------------------------

        excluded: list[
            str
        ] = []

        for title in (
            exclude_titles or []
        ):

            if not title:
                continue

            cleaned = title.strip()

            if not cleaned:
                continue

            if cleaned not in excluded:

                excluded.append(
                    cleaned
                )

        if excluded:

            placeholders = ",".join(
                "?"
                for _ in excluded
            )

            query += (
                " AND title NOT IN "
                f"({placeholders})"
            )

            params.extend(
                excluded
            )

        query += """
            ORDER BY RANDOM()
            LIMIT 1
        """

        with self._connect() as db:

            row = db.execute(
                query,
                params,
            ).fetchone()

            return (
                dict(row)
                if row
                else None
            )

    # ==============================================================
    # POSTER
    # ==============================================================

    def poster_for_id(
        self,
        media_id: int,
    ) -> Path | None:

        with self._connect() as db:

            row = db.execute(
                """
                SELECT poster_path, poster
                FROM media
                WHERE id = ?
                """,
                (media_id,),
            ).fetchone()

        if not row:
            return None

        poster_path = row[
            "poster_path"
        ]

        # ----------------------------------------------------------
        # 1. Bereits lokal gespeichert
        # ----------------------------------------------------------

        if poster_path:

            try:

                path = Path(
                    poster_path
                )

                if path.is_file():

                    return path

            except OSError:
                pass

        # ----------------------------------------------------------
        # 2. Falls alter DB-Eintrag noch keine lokale Kopie hat:
        #    versuchen wir den NFO-Posterwert nachträglich zu laden.
        # ----------------------------------------------------------

        poster = row[
            "poster"
        ]

        if poster:

            if poster.casefold().startswith(
                (
                    "http://",
                    "https://",
                )
            ):

                cached = (
                    self._download_poster(
                        poster
                    )
                )

                if cached:

                    with self._connect() as db:

                        db.execute(
                            """
                            UPDATE media
                            SET poster_path = ?,
                                updated_at =
                                    CURRENT_TIMESTAMP
                            WHERE id = ?
                            """,
                            (
                                str(
                                    cached
                                ),
                                media_id,
                            ),
                        )

                    return cached

        return None

    # ==============================================================
    # STATS
    # ==============================================================

    def stats(
        self,
    ) -> dict[str, Any]:

        with self._connect() as db:

            total = db.execute(
                """
                SELECT COUNT(*)
                FROM media
                """
            ).fetchone()[0]

            movies = db.execute(
                """
                SELECT COUNT(*)
                FROM media
                WHERE kind = 'movie'
                """
            ).fetchone()[0]

            series = db.execute(
                """
                SELECT COUNT(*)
                FROM media
                WHERE kind = 'series'
                """
            ).fetchone()[0]

            avg_rating = db.execute(
                """
                SELECT AVG(rating)
                FROM media
                WHERE rating IS NOT NULL
                """
            ).fetchone()[0]

            providers = [
                row["provider"]
                for row in db.execute(
                    """
                    SELECT DISTINCT provider
                    FROM media
                    WHERE provider IS NOT NULL
                      AND provider != ''
                    ORDER BY provider COLLATE NOCASE
                    """
                ).fetchall()
            ]

            return {
                "total":
                    int(total),

                "movies":
                    int(movies),

                "series":
                    int(series),

                "providers":
                    providers,

                "avg_rating":
                    (
                        round(
                            float(
                                avg_rating
                            ),
                            2,
                        )
                        if avg_rating is not None
                        else None
                    ),
            }
