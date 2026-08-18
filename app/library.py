from __future__ import annotations

import logging
import re
import sqlite3
import threading
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


# Feste Providerliste.
#
# Diese Namen müssen exakt den direkten Provider-Ordnern unter
# /movies bzw. /tv entsprechen.
#
# Wichtig:
# Es gibt bewusst KEINE automatische Erkennung beliebiger
# Ordner als Provider.
#
# Beispiel:
#
#   /tv/max/...
#   /tv/netflix/...
#
# werden verarbeitet.
#
#   /tv/hbo/...
#   /tv/primevideo/...
#
# werden ignoriert, solange sie hier nicht eingetragen sind.
PROVIDERS = {
    "max",
    "peacock",
    "paramount",
    "appletv",
    "appletvplus",
    "disney",
    "netflix",
    "sky",
}


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


def parse_nfo(
    path: Path | None,
) -> dict[str, Any]:
    """
    Liest Metadaten aus einer NFO.

    Eine fehlerhafte oder unlesbare NFO darf niemals den
    kompletten Bibliotheksscan abbrechen.
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
        value = _clean(node.text)

        if value:
            genres.append(value)

    if genres:
        data["genres"] = ", ".join(
            dict.fromkeys(genres)
        )

    # ----------------------------------------------------------
    # Poster
    # ----------------------------------------------------------

    poster = None

    for thumb in root.findall("thumb"):
        aspect = (
            thumb.attrib.get("aspect") or ""
        ).lower()

        value = _clean(thumb.text)

        if value and (
            not aspect
            or aspect == "poster"
        ):
            poster = value
            break

    if not poster:
        art = root.find("art")

        if art is not None:
            poster_node = art.find("poster")

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
        # Manche NFOs verwenden 0-5 statt 0-10.
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

        # Werte unter 100 werden als Stunden interpretiert.
        if 0 < runtime < 100:
            runtime *= 60

        runtime = max(
            1,
            runtime,
        )

    data["runtime"] = runtime

    # ----------------------------------------------------------
    # Year
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


def parse_name(
    name: str,
) -> tuple[str, int | None]:
    """
    Extrahiert Titel und Jahr aus Ordner-/Dateinamen.

    Beispiele:

        The Matrix (1999)
        The.Matrix.1999
        The Matrix - 1999
    """

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


class Library:
    """
    Verwaltung der lokalen Medienbibliothek.

    Filme:

        /movies/PROVIDER/FILM/

    Serien:

        /tv/PROVIDER/SERIE/

    Erlaubte Provider:

        max
        paramount
        appletv
        appletvplus
        disney
        netflix
        sky

    Bei Serien ist ausschließlich der Serienordner ein
    Media-Roulette-Eintrag.

    Beispiel:

        /tv/max/Power Book IV Force/
            Season 01/
            Season 02/
            Season 03/

    ergibt genau EINEN Eintrag:

        provider = max
        title    = Power Book IV Force
        kind     = series

    Season- und Episode-Ordner werden niemals als eigene
    Media-Einträge angelegt.
    """

    def __init__(
        self,
        db_path: str,
        movies_dir: str,
        series_dir: str,
    ):
        self.db_path = Path(db_path)

        self.movies_dir = Path(
            movies_dir
        )

        self.series_dir = Path(
            series_dir
        )

        self._scan_lock = threading.Lock()

        self._init_db()

    # ==============================================================
    # PROVIDERS
    # ==============================================================

    @staticmethod
    def _normalize_provider(
        provider: str | None,
    ) -> str | None:
        """
        Normalisiert einen Providernamen.

        Provider werden intern immer lowercase gespeichert.

        Dadurch werden beispielsweise:

            MAX
            Max
            max

        gleich behandelt.

        Die feste Providerliste bleibt trotzdem strikt bestehen.
        """

        if provider is None:
            return None

        normalized = provider.strip().casefold()

        if not normalized:
            return None

        if normalized not in PROVIDERS:
            return None

        return normalized

    @staticmethod
    def _provider_dirs(
        root_dir: Path,
    ) -> list[tuple[str, Path]] | None:
        """
        Liefert ausschließlich die erlaubten Providerordner.

        Alle anderen direkten Unterordner werden ignoriert.

        Beispiel:

            /tv/max
            /tv/netflix
            /tv/hbo

        liefert nur:

            max
            netflix
        """

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

                provider = Library._normalize_provider(
                    child.name
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

        return conn

    def _init_db(self) -> None:

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

            for column, statement in migrations.items():

                if column not in columns:
                    db.execute(statement)

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
            directory / preferred_name
        )

        try:

            if preferred.is_file():
                return preferred

            nfos = sorted(
                (
                    item
                    for item in directory.iterdir()
                    if item.is_file()
                    and item.suffix.lower() == ".nfo"
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
        """
        Sucht deterministisch nach einer Videodatei.

        Bei Serien wird diese Datei ausschließlich verwendet,
        um festzustellen, ob die Serie tatsächlich Videoinhalt
        besitzt.

        Die Episode selbst wird NICHT als Media-Roulette-Eintrag
        verwendet.
        """

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

    @staticmethod
    def _find_local_poster(
        directory: Path,
        nfo_poster: str | None,
    ) -> Path | None:

        if nfo_poster:

            candidate = Path(
                nfo_poster
            )

            if not candidate.is_absolute():
                candidate = (
                    directory / candidate
                )

            try:

                if candidate.is_file():
                    return candidate.resolve()

            except OSError:
                pass

        for filename in POSTER_FILENAMES:

            candidate = (
                directory / filename
            )

            try:

                if candidate.is_file():
                    return candidate.resolve()

            except OSError:
                continue

        return None

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
        """
        Erstellt einen Media-Datensatz.

        Film:
            path = tatsächliche Videodatei

        Serie:
            path = Serienordner

        Dadurch bleiben Serien unabhängig von ihren Seasons
        und Episoden genau ein Roulette-Eintrag.
        """

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

        data = parse_nfo(nfo)

        title = data.get("title")
        year = data.get("year")

        if not title:

            title, name_year = parse_name(
                media_dir.name
            )

            if year is None:
                year = name_year

        poster_path = (
            self._find_local_poster(
                media_dir,
                data.get("poster"),
            )
        )

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
            "provider": normalized_provider,
            "title": (
                title
                or media_dir.name
            ),
            "year": year,
            "plot": data.get("plot"),
            "genres": data.get("genres"),
            "rating": data.get("rating"),
            "runtime": data.get("runtime"),
            "path": str(
                resolved_media_path
            ),
            "nfo_path": (
                str(resolved_nfo_path)
                if resolved_nfo_path
                else None
            ),
            "tmdbid": data.get("tmdbid"),
            "imdbid": data.get("imdbid"),
            "poster": data.get("poster"),
            "poster_path": (
                str(resolved_poster_path)
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
    ) -> list[dict[str, Any]] | None:
        """
        Filme:

            /movies/provider/movie/

        Es werden ausschließlich die fest definierten Provider
        verarbeitet.
        """

        provider_dirs = self._provider_dirs(
            root_dir
        )

        if provider_dirs is None:
            return None

        items: list[dict[str, Any]] = []

        for provider, provider_dir in provider_dirs:

            movie_dirs = self._iter_dirs(
                provider_dir
            )

            if movie_dirs is None:
                logger.warning(
                    "Unable to read movie provider: %s",
                    provider_dir,
                )
                continue

            logger.debug(
                "Scanning movie provider: %s",
                provider,
            )

            for movie_dir in movie_dirs:

                item = self._build_item(
                    kind="movie",
                    provider=provider,
                    media_dir=movie_dir,
                    preferred_nfo="movie.nfo",
                )

                if item:
                    items.append(item)

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
    ) -> list[dict[str, Any]] | None:
        """
        Serien:

            /tv/provider/series/

        Beispiel:

            /tv/max/Power Book IV Force/
                Season 01/
                Season 02/
                Season 03/

        ergibt exakt EINEN Datensatz:

            kind     = series
            provider = max
            title    = Power Book IV Force

        Seasons und Episoden werden ausschließlich rekursiv
        durchsucht, um festzustellen, ob die Serie Videoinhalt
        besitzt.
        """

        provider_dirs = self._provider_dirs(
            root_dir
        )

        if provider_dirs is None:
            return None

        items: list[dict[str, Any]] = []

        for provider, provider_dir in provider_dirs:

            series_dirs = self._iter_dirs(
                provider_dir
            )

            if series_dirs is None:
                logger.warning(
                    "Unable to read series provider: %s",
                    provider_dir,
                )
                continue

            logger.debug(
                "Scanning series provider: %s",
                provider,
            )

            for series_dir in series_dirs:

                logger.debug(
                    "Scanning series: %s/%s",
                    provider,
                    series_dir.name,
                )

                item = self._build_item(
                    kind="series",
                    provider=provider,
                    media_dir=series_dir,
                    preferred_nfo="tvshow.nfo",
                )

                if item:
                    items.append(item)

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
        items: list[dict[str, Any]],
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
        items: list[dict[str, Any]],
    ) -> None:
        """
        Entfernt veraltete Einträge ausschließlich aus dem
        jeweiligen Medienbaum.

        Wenn der Root nicht erreichbar ist, wird diese Funktion
        nicht aufgerufen.
        """

        try:
            root = root_dir.resolve()
        except OSError:
            return

        current_paths = {
            str(
                Path(item["path"]).resolve()
            )
            for item in items
        }

        db.execute(
            """
            CREATE TEMP TABLE IF NOT EXISTS scan_paths (
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
                INSERT OR IGNORE INTO scan_paths(path)
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

    def scan(self) -> int:

        if not self._scan_lock.acquire(
            blocking=False
        ):
            return self.stats()["total"]

        try:

            movie_items = self._scan_movies(
                self.movies_dir
            )

            series_items = self._scan_series(
                self.series_dir
            )

            with self._connect() as db:

                db.execute("BEGIN")

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

                return int(total)

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
        exclude_titles: list[str] | None = None,
    ) -> dict[str, Any] | None:

        query = """
            SELECT *
            FROM media
            WHERE 1=1
        """

        params: list[Any] = []

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

            # "alle" bedeutet alle erlaubten Provider.
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
        # Exclude titles
        # ----------------------------------------------------------

        excluded: list[str] = []

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

        # ----------------------------------------------------------
        # Roulette
        # ----------------------------------------------------------

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
                SELECT poster_path
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

        if not poster_path:
            return None

        try:

            path = Path(
                poster_path
            )

            if path.is_file():
                return path

        except OSError:
            pass

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
                "total": int(total),
                "movies": int(movies),
                "series": int(series),
                "providers": providers,
                "avg_rating": (
                    round(
                        float(avg_rating),
                        2,
                    )
                    if avg_rating is not None
                    else None
                ),
            }
