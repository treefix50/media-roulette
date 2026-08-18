from __future__ import annotations

import logging
import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


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
)


def _clean(value: str | None) -> str | None:
    """
    Bereinigt Text aus NFO-Dateien.
    """

    if value is None:
        return None

    value = re.sub(r"\s+", " ", value).strip()

    return value or None


def _nfo_value(root: ET.Element, name: str) -> str | None:
    """
    Liest einen einfachen Wert aus einem NFO-Element.
    """

    node = root.find(name)

    if node is None:
        return None

    return _clean(node.text)


def _parse_number(value: Any) -> float | None:
    """
    Robuste Zahlenkonvertierung.
    """

    if value is None:
        return None

    try:
        return float(str(value).replace(",", "."))
    except (TypeError, ValueError):
        return None


def parse_nfo(path: Path | None) -> dict[str, Any]:
    """
    Liest Metadaten aus einer NFO-Datei.

    Fehlerhafte oder unvollständige NFO-Dateien werden ignoriert,
    statt den gesamten Scan abzubrechen.
    """

    if not path:
        return {}

    try:
        if not path.is_file():
            return {}
    except OSError:
        return {}

    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, IOError):
        logger.warning("Unable to parse NFO: %s", path)
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
        value = _nfo_value(root, field)

        if value:
            data[field] = value

    genres = []

    for node in root.findall("genre"):
        value = _clean(node.text)

        if value:
            genres.append(value)

    if genres:
        data["genres"] = ", ".join(dict.fromkeys(genres))

    # Poster aus Kodi-/Jellyfin-/Emby-artigen NFOs lesen.
    poster = None

    for thumb in root.findall("thumb"):
        aspect = (thumb.attrib.get("aspect") or "").lower()
        value = _clean(thumb.text)

        if value and (not aspect or aspect == "poster"):
            poster = value
            break

    if not poster:
        art = root.find("art")

        if art is not None:
            poster_node = art.find("poster")

            if poster_node is not None:
                poster = _clean(poster_node.text)

    if poster:
        data["poster"] = poster

    rating = _parse_number(data.get("rating"))

    if rating is not None:
        # Manche NFOs speichern Bewertungen auf einer 0-5-Skala.
        if 0 < rating <= 5:
            rating *= 2

        rating = max(0.0, min(10.0, rating))

    data["rating"] = rating

    runtime = _parse_number(data.get("runtime"))

    if runtime is not None:
        runtime = int(round(runtime))

        # Einige Quellen speichern Laufzeit in Stunden.
        if 0 < runtime < 100:
            runtime *= 60

        runtime = max(1, runtime)

    data["runtime"] = runtime

    year = data.get("year")

    if year:
        match = re.search(r"(19\d{2}|20\d{2})", str(year))

        if match:
            data["year"] = int(match.group(1))
        else:
            data["year"] = None

    return data


def parse_name(name: str) -> tuple[str, int | None]:
    """
    Extrahiert Titel und Jahr aus Ordner-/Dateinamen.

    Beispiele:
        The Matrix (1999)
        The.Matrix.1999
        The Matrix - 1999
    """

    text = Path(name).stem

    text = re.sub(r"[._]+", " ", text)

    match = re.search(
        r"\b(19\d{2}|20\d{2})\b(?:\s*\(\1\))?",
        text,
    )

    year = int(match.group(1)) if match else None

    if match:
        text = text[: match.start()].strip(" -().")

    text = re.sub(r"\s+", " ", text).strip()

    text = re.sub(r"[()\[\]]+$", "", text).strip()

    return text or Path(name).stem, year


class Library:
    """
    Verwaltung der lokalen Medienbibliothek.

    SQLite enthält nur Metadaten.
    Der eigentliche Medienbaum bleibt read-only.
    """

    def __init__(
        self,
        db_path: str,
        movies_dir: str,
        series_dir: str,
    ):
        self.db_path = Path(db_path)

        self.movies_dir = Path(movies_dir)
        self.series_dir = Path(series_dir)

        self._scan_lock = threading.Lock()

        self._init_db()

    # ------------------------------------------------------------------
    # DATABASE
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        """
        Erstellt eine eigene SQLite-Verbindung.

        Jede Operation erhält ihre eigene Connection.
        """

        self.db_path.parent.mkdir(
            parents=True,
            exist_ok=True,
        )

        conn = sqlite3.connect(
            str(self.db_path),
            timeout=30,
        )

        conn.row_factory = sqlite3.Row

        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")

        return conn

    def _init_db(self) -> None:
        """
        Initialisiert die Datenbank und führt kleine Migrationen
        für bestehende Installationen durch.
        """

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
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
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
                "tmdbid": "ALTER TABLE media ADD COLUMN tmdbid TEXT",
                "imdbid": "ALTER TABLE media ADD COLUMN imdbid TEXT",
                "poster": "ALTER TABLE media ADD COLUMN poster TEXT",
                "poster_path": "ALTER TABLE media ADD COLUMN poster_path TEXT",
            }

            for column, statement in migrations.items():
                if column not in columns:
                    db.execute(statement)

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_kind
                ON media(kind)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_provider
                ON media(provider)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_title
                ON media(title)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_rating
                ON media(rating)
                """
            )

            db.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_media_path
                ON media(path)
                """
            )

    # ------------------------------------------------------------------
    # FILESYSTEM HELPERS
    # ------------------------------------------------------------------

    @staticmethod
    def _iter_dirs(path: Path) -> list[Path] | None:
        """
        Liefert sortierte Unterordner.

        None bedeutet:
        Der Ordner ist nicht erreichbar.

        Eine leere Liste bedeutet:
        Der Ordner ist erreichbar, aber leer.
        """

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
                key=lambda item: item.name.casefold(),
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
        """
        Sucht bevorzugt nach movie.nfo/tvshow.nfo und danach
        nach einer beliebigen NFO-Datei.
        """

        preferred = directory / preferred_name

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
                key=lambda item: item.name.casefold(),
            )

            return nfos[0] if nfos else None

        except OSError:
            return None

    @staticmethod
    def _find_first_video(directory: Path) -> Path | None:
        """
        Sucht deterministisch die erste Videodatei.

        Die vorherige Implementierung verwendete teilweise
        zufällige Set-Reihenfolge bzw. glob-Muster, wodurch die
        Ergebnisse unnötig unterschiedlich sein konnten.
        """

        try:
            videos = sorted(
                (
                    path
                    for path in directory.rglob("*")
                    if path.is_file()
                    and path.suffix.lower() in VIDEO_EXTENSIONS
                ),
                key=lambda path: str(path).casefold(),
            )

            return videos[0] if videos else None

        except OSError:
            return None

    @staticmethod
    def _find_local_poster(
        directory: Path,
        nfo_poster: str | None,
    ) -> Path | None:
        """
        Findet ein lokales Poster.

        Die Datei wird niemals direkt vom Client über einen Pfad
        angefordert. Nur die interne Media-ID wird später verwendet.
        """

        if nfo_poster:
            candidate = Path(nfo_poster)

            if not candidate.is_absolute():
                candidate = directory / candidate

            try:
                if candidate.is_file():
                    return candidate.resolve()
            except OSError:
                pass

        for filename in POSTER_FILENAMES:
            candidate = directory / filename

            try:
                if candidate.is_file():
                    return candidate.resolve()
            except OSError:
                continue

        return None

    @staticmethod
    def _is_likely_show(directory: Path) -> bool:
        """
        Erkennt die klassische direkte Serienstruktur:

            /series/Breaking Bad/Season 1/...

        und auch:

            /series/Breaking Bad/episodes/...

        anhand vorhandener Videodateien.
        """

        return (
            Library._find_first_video(directory)
            is not None
        )

    # ------------------------------------------------------------------
    # ITEM CREATION
    # ------------------------------------------------------------------

    def _build_item(
        self,
        *,
        kind: str,
        provider: str,
        media_dir: Path,
        preferred_nfo: str,
    ) -> dict[str, Any] | None:
        """
        Erstellt einen standardisierten Media-Datensatz.
        """

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

        video = self._find_first_video(media_dir)

        if not video:
            return None

        local_poster = self._find_local_poster(
            media_dir,
            data.get("poster"),
        )

        return {
            "kind": kind,
            "provider": provider,
            "title": title or video.stem,
            "year": year,
            "plot": data.get("plot"),
            "genres": data.get("genres"),
            "rating": data.get("rating"),
            "runtime": data.get("runtime"),
            "path": str(video),
            "nfo_path": (
                str(nfo)
                if nfo
                else None
            ),
            "tmdbid": data.get("tmdbid"),
            "imdbid": data.get("imdbid"),
            "poster": data.get("poster"),
            "poster_path": (
                str(local_poster)
                if local_poster
                else None
            ),
        }

    # ------------------------------------------------------------------
    # MOVIE SCANNER
    # ------------------------------------------------------------------

    def _scan_movies(
        self,
        root_dir: Path,
    ) -> list[dict[str, Any]] | None:
        """
        Erwartete Struktur:

            /movies/
                provider/
                    Movie/
                        movie.mkv

        Jeder direkte Unterordner von /movies wird automatisch
        als Provider behandelt.

        Keine fest codierten Provider.
        """

        provider_dirs = self._iter_dirs(root_dir)

        if provider_dirs is None:
            return None

        items: list[dict[str, Any]] = []

        for provider_dir in provider_dirs:
            movie_dirs = self._iter_dirs(provider_dir)

            if movie_dirs is None:
                # Nicht löschen, was bereits in der Datenbank steht.
                return None

            provider = provider_dir.name.strip()

            if not provider:
                continue

            for movie_dir in movie_dirs:
                item = self._build_item(
                    kind="movie",
                    provider=provider,
                    media_dir=movie_dir,
                    preferred_nfo="movie.nfo",
                )

                if item:
                    items.append(item)

        return items

    # ------------------------------------------------------------------
    # SERIES SCANNER
    # ------------------------------------------------------------------

    def _scan_series(
        self,
        root_dir: Path,
    ) -> list[dict[str, Any]] | None:
        """
        Unterstützt beide bisherigen Strukturen.

        Bevorzugt:

            /series/
                provider/
                    Show/
                        Season 1/
                            episode.mkv

        Zusätzlich kompatibel:

            /series/
                Show/
                    Season 1/
                        episode.mkv

        Im zweiten Fall wird "unknown" als Provider verwendet.
        """

        root_entries = self._iter_dirs(root_dir)

        if root_entries is None:
            return None

        items: list[dict[str, Any]] = []

        for entry in root_entries:
            # Kompatibilität mit der bisher verwendeten direkten
            # Serienstruktur.
            if self._is_likely_show(entry):
                item = self._build_item(
                    kind="series",
                    provider="unknown",
                    media_dir=entry,
                    preferred_nfo="tvshow.nfo",
                )

                if item:
                    items.append(item)

                continue

            # Standardstruktur:
            #
            # /series/provider/show
            provider_dirs = self._iter_dirs(entry)

            if provider_dirs is None:
                return None

            provider = entry.name.strip()

            if not provider:
                continue

            for show_dir in provider_dirs:
                item = self._build_item(
                    kind="series",
                    provider=provider,
                    media_dir=show_dir,
                    preferred_nfo="tvshow.nfo",
                )

                if item:
                    items.append(item)

        return items

    # ------------------------------------------------------------------
    # DATABASE SCAN
    # ------------------------------------------------------------------

    @staticmethod
    def _upsert_items(
        db: sqlite3.Connection,
        items: list[dict[str, Any]],
    ) -> None:
        """
        Upsert ohne DELETE/INSERT-Verhalten von INSERT OR REPLACE.

        Dadurch bleiben bestehende IDs stabil.
        """

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
                ON CONFLICT(path) DO UPDATE SET
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

    @staticmethod
    def _delete_missing_from_root(
        db: sqlite3.Connection,
        root_dir: Path,
        items: list[dict[str, Any]],
    ) -> None:
        """
        Entfernt nur alte Datensätze dieses Scan-Bereichs.

        Wichtig:
        Ein Scan eines anderen Verzeichnisses kann dadurch niemals
        fremde Einträge löschen.
        """

        root = str(root_dir.resolve())

        paths = {
            str(Path(item["path"]).resolve())
            for item in items
        }

        # Temporäre Tabelle verhindert tausende SQL-Parameter.
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

        if paths:
            db.executemany(
                "INSERT OR IGNORE INTO scan_paths(path) VALUES (?)",
                ((path,) for path in paths),
            )

        # GLOB benutzt * als Pfad-Wildcard.
        # Dadurch wird nur der entsprechende Medienbaum berücksichtigt.
        db.execute(
            """
            DELETE FROM media
            WHERE path GLOB ?
              AND path NOT IN (
                  SELECT path
                  FROM scan_paths
              )
            """,
            (f"{root}/*",),
        )

    def scan(self) -> int:
        """
        Führt einen vollständigen Bibliotheksscan aus.

        Der Scan wird serialisiert, damit zwei parallele
        Aktualisierungen niemals gleichzeitig SQLite verändern.

        Bei einem nicht erreichbaren Root-Verzeichnis werden die
        bestehenden Daten dieses Bereichs bewusst NICHT gelöscht.
        """

        with self._scan_lock:
            movie_items = self._scan_movies(
                self.movies_dir
            )

            series_items = self._scan_series(
                self.series_dir
            )

            with self._connect() as db:
                db.execute("BEGIN")

                # Nur erfolgreich gescannte Root-Verzeichnisse
                # werden bereinigt.
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
                        "Movie directory unavailable; "
                        "existing movie records will be preserved."
                    )

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
                        "Series directory unavailable; "
                        "existing series records will be preserved."
                    )

                db.commit()

                total = db.execute(
                    "SELECT COUNT(*) FROM media"
                ).fetchone()[0]

                logger.info(
                    "Library scan finished: %s media items",
                    total,
                )

                return int(total)

    # ------------------------------------------------------------------
    # RANDOM RECOMMENDATION
    # ------------------------------------------------------------------

    def random_item(
        self,
        kind: str | None = None,
        provider: str | None = None,
        exclude_titles: list[str] | None = None,
    ) -> dict[str, Any] | None:
        """
        Wählt ein zufälliges Medium.

        Filter:
            kind = movie / series
            provider = beliebiger erkannter Provider

        exclude_titles enthält ausschließlich Titel.
        """

        query = """
            SELECT *
            FROM media
            WHERE 1=1
        """

        params: list[Any] = []

        normalized_kind = (
            kind.strip().lower()
            if kind
            else ""
        )

        normalized_provider = (
            provider.strip()
            if provider
            else ""
        )

        if normalized_kind in {
            "movie",
            "series",
        }:
            query += " AND kind = ?"
            params.append(normalized_kind)

        if normalized_provider:
            query += " AND provider = ?"
            params.append(normalized_provider)

        excluded = list(
            dict.fromkeys(
                title.strip()
                for title in (exclude_titles or [])
                if title and title.strip()
            )
        )

        if excluded:
            placeholders = ",".join(
                "?" for _ in excluded
            )

            query += (
                f" AND title NOT IN ({placeholders})"
            )

            params.extend(excluded)

        # Zufall mit einem kleinen Rating-Einfluss.
        #
        # RANDOM() allein wird beibehalten, damit die Grundidee
        # "Roulette" erhalten bleibt. Das Rating verändert nur
        # geringfügig die Wahrscheinlichkeit.
        query += """
            ORDER BY
                (
                    ABS(RANDOM()) / 9223372036854775807.0
                ) *
                (
                    1.0 + (
                        COALESCE(rating, 0) / 10.0
                    ) * 0.20
                ) DESC
            LIMIT 1
        """

        with self._connect() as db:
            row = db.execute(
                query,
                params,
            ).fetchone()

            return dict(row) if row else None

    # ------------------------------------------------------------------
    # POSTER
    # ------------------------------------------------------------------

    def poster_for_id(
        self,
        media_id: int,
    ) -> Path | None:
        """
        Liefert nur den intern gespeicherten Posterpfad.

        Es wird niemals ein beliebiger vom Client übergebener
        Dateipfad geöffnet.
        """

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

        poster_path = row["poster_path"]

        if not poster_path:
            return None

        try:
            path = Path(poster_path)

            if path.is_file():
                return path

        except OSError:
            pass

        return None

    # ------------------------------------------------------------------
    # STATISTICS
    # ------------------------------------------------------------------

    def stats(self) -> dict[str, Any]:
        """
        Liefert Bibliotheksstatistiken.
        """

        with self._connect() as db:
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

            total = db.execute(
                "SELECT COUNT(*) FROM media"
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

            return {
                "total": int(total),
                "movies": int(movies),
                "series": int(series),
                "providers": providers,
                "avg_rating": (
                    round(float(avg_rating), 2)
                    if avg_rating is not None
                    else None
                ),
            }
