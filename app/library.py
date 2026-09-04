"""
Media Roulette - media library scanner and SQLite repository.

The library scanner discovers movies and TV series from the configured
read-only media directories.

Expected directory layout:

```
/data/movies/
    <provider>/
        Movie Name/
            Movie Name.mkv
            Movie Name.nfo
            poster.jpg

/data/tv/
    <provider>/
        Series Name/
            Series Name.nfo
            poster.jpg
            Season 01/
                Series Name - S01E01.mkv
```

The provider name is taken from the first directory below the configured
media root. Providers are therefore dynamic and are NOT restricted to a
hard-coded list.

The module uses SQLite directly. No SQLAlchemy dependency is required.

Important:

* Media directories are treated as read-only.
* Database state lives under DATABASE_PATH.
* Filesystem paths are never intended to be returned to API clients.
* Poster paths are validated before being served.
  """

from **future** import annotations

import logging
import os
import re
import sqlite3
import threading
import unicodedata
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator, Sequence

logger = logging.getLogger("media_roulette.library")

# ============================================================================

# CONFIGURATION

# ============================================================================

DATABASE_PATH = os.getenv(
"DATABASE_PATH",
"/state/media_roulette.db",
).strip()

MOVIES_DIR = os.getenv(
"MOVIES_DIR",
"/data/movies",
).strip()

SERIES_DIR = os.getenv(
"SERIES_DIR",
"/data/tv",
).strip()

# Supported video extensions.

VIDEO_EXTENSIONS: frozenset[str] = frozenset(
{
".avi",
".divx",
".flv",
".m2ts",
".m4v",
".mkv",
".mov",
".mp4",
".mpeg",
".mpg",
".ts",
".webm",
".wmv",
}
)

# Supported poster extensions.

POSTER_EXTENSIONS: tuple[str, ...] = (
".jpg",
".jpeg",
".png",
".webp",
)

# Filename patterns.

SEASON_EPISODE_RE = re.compile(
r"(?i)\bS(?P<season>\d{1,3})E(?P<episode>\d{1,4})\b"
)

SEASON_ONLY_RE = re.compile(
r"(?i)\bS(?P<season>\d{1,3})\b"
)

YEAR_RE = re.compile(
r"\b(19\d{2}|20\d{2})\b"
)

# ============================================================================

# DATA CLASSES

# ============================================================================

@dataclass(frozen=True)
class MediaRoot:
"""
Description of a configured media root.
"""

```
kind: str
path: Path
```

# ============================================================================

# DATABASE

# ============================================================================

def _database_parent() -> Path:
"""
Return the directory containing the SQLite database.
"""

```
path = Path(
    DATABASE_PATH,
).expanduser()

parent = path.parent

if str(parent) in {"", "."}:
    return Path(".")

return parent
```

def _configure_connection(
conn: sqlite3.Connection,
) -> None:
"""
Configure a SQLite connection for the application.
"""

```
conn.row_factory = sqlite3.Row

conn.execute(
    "PRAGMA foreign_keys = ON"
)

conn.execute(
    "PRAGMA journal_mode = WAL"
)

conn.execute(
    "PRAGMA synchronous = NORMAL"
)

conn.execute(
    "PRAGMA busy_timeout = 30000"
)
```

@contextmanager
def get_db() -> Iterator[sqlite3.Connection]:
"""
Open a configured SQLite connection.

```
The connection is committed on successful exit and rolled back when an
exception occurs.

A fresh connection is used per operation. This avoids sharing SQLite
connections across FastAPI threads.
"""

database = Path(
    DATABASE_PATH,
).expanduser()

parent = database.parent

if parent and str(parent) != ".":
    parent.mkdir(
        parents=True,
        exist_ok=True,
    )

conn = sqlite3.connect(
    str(database),
    timeout=30,
    check_same_thread=False,
)

try:
    _configure_connection(
        conn,
    )

    yield conn

    conn.commit()

except Exception:
    conn.rollback()
    raise

finally:
    conn.close()
```

# ============================================================================

# DATABASE SCHEMA

# ============================================================================

def _table_columns(
conn: sqlite3.Connection,
table: str,
) -> set[str]:
"""
Return the columns of a table.
"""

```
rows = conn.execute(
    f"PRAGMA table_info({table})"
).fetchall()

return {
    str(row["name"])
    for row in rows
}
```

def _ensure_column(
conn: sqlite3.Connection,
table: str,
column: str,
definition: str,
) -> None:
"""
Add a missing SQLite column.

```
Column/table identifiers are internal constants and are never derived from
user input.
"""

columns = _table_columns(
    conn,
    table,
)

if column in columns:
    return

conn.execute(
    f"ALTER TABLE {table} ADD COLUMN {column} {definition}"
)
```

def init_db() -> None:
"""
Create and migrate the SQLite schema.

```
Migrations are deliberately lightweight because Media Roulette is a small
single-application SQLite deployment.
"""

with get_db() as conn:

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS media (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            title TEXT NOT NULL,
            year INTEGER,
            provider TEXT,
            path TEXT NOT NULL UNIQUE,
            nfo_path TEXT,
            poster_path TEXT,
            poster TEXT,
            rating REAL,
            runtime INTEGER,
            season INTEGER,
            episode INTEGER,
            added_at TEXT,
            updated_at TEXT
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_kind
        ON media(kind)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_provider
        ON media(provider)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_title
        ON media(title)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_kind_provider
        ON media(kind, provider)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_media_updated_at
        ON media(updated_at)
        """
    )

    # --------------------------------------------------------------------
    # Backwards-compatible migration for databases created by older
    # versions of Media Roulette.
    # --------------------------------------------------------------------

    legacy_columns = {
        "kind": "TEXT",
        "title": "TEXT",
        "year": "INTEGER",
        "provider": "TEXT",
        "path": "TEXT",
        "nfo_path": "TEXT",
        "poster_path": "TEXT",
        "poster": "TEXT",
        "rating": "REAL",
        "runtime": "INTEGER",
        "season": "INTEGER",
        "episode": "INTEGER",
        "added_at": "TEXT",
        "updated_at": "TEXT",
    }

    for column, definition in legacy_columns.items():
        _ensure_column(
            conn,
            "media",
            column,
            definition,
        )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            version INTEGER PRIMARY KEY
        )
        """
    )

    row = conn.execute(
        """
        SELECT version
        FROM schema_version
        ORDER BY version DESC
        LIMIT 1
        """
    ).fetchone()

    if row is None:
        conn.execute(
            """
            INSERT INTO schema_version(version)
            VALUES (1)
            """
        )
```

# Initialize the database when this module is imported.

init_db()

# ============================================================================

# NFO PARSING

# ============================================================================

def _clean_text(
value: Any,
) -> str | None:
"""
Normalize an arbitrary metadata value to a clean string.
"""

```
if value is None:
    return None

text = str(value).strip()

if not text:
    return None

return text
```

def _parse_float(
value: Any,
) -> float | None:
"""
Parse a numeric rating.
"""

```
text = _clean_text(
    value,
)

if text is None:
    return None

try:
    result = float(
        text.replace(",", ".")
    )
except ValueError:
    return None

if result < 0:
    return None

# Some NFO sources use a 0-10 scale; others may contain percentages.
# We intentionally do not blindly transform values because the NFO
# rating semantics should be preserved.
return result
```

def _parse_int(
value: Any,
) -> int | None:
"""
Parse an integer from arbitrary metadata.
"""

```
text = _clean_text(
    value,
)

if text is None:
    return None

match = re.search(
    r"-?\d+",
    text,
)

if not match:
    return None

try:
    return int(
        match.group(0)
    )
except ValueError:
    return None
```

def _normalize_runtime(
value: Any,
) -> int | None:
"""
Normalize runtime to minutes.
"""

```
result = _parse_int(
    value,
)

if result is None:
    return None

if result <= 0:
    return None

# Runtime in NFO files is normally already expressed in minutes.
return result
```

def _parse_nfo(
path: Path,
) -> dict[str, Any]:
"""
Parse a Kodi-style NFO file.

```
xml.etree.ElementTree is part of the Python standard library and is
sufficient for the simple metadata structure used here.
"""

import xml.etree.ElementTree as ET

result: dict[str, Any] = {}

try:
    tree = ET.parse(
        path,
    )
    root = tree.getroot()

except (
    OSError,
    ET.ParseError,
) as exc:
    logger.warning(
        "Could not parse NFO %s: %s",
        path,
        exc,
    )
    return result

def text(
    name: str,
) -> str | None:
    element = root.find(
        name,
    )

    if element is None:
        return None

    return _clean_text(
        element.text,
    )

result["title"] = text("title")
result["originaltitle"] = text("originaltitle")
result["year"] = _parse_int(
    text("year")
)
result["rating"] = _parse_float(
    text("rating")
)
result["runtime"] = _normalize_runtime(
    text("runtime")
)
result["premiered"] = text(
    "premiered"
)
result["plot"] = text(
    "plot"
)
result["outline"] = text(
    "outline"
)
result["poster"] = text(
    "thumb"
)

# Some NFO files use <season>/<episode>.
result["season"] = _parse_int(
    text("season")
)

result["episode"] = _parse_int(
    text("episode")
)

return result
```

# ============================================================================

# FILESYSTEM HELPERS

# ============================================================================

def _is_video_file(
path: Path,
) -> bool:
"""
Return True if the path has a supported video extension.
"""

```
return (
    path.is_file()
    and path.suffix.casefold()
    in VIDEO_EXTENSIONS
)
```

def _safe_resolve(
path: Path,
) -> Path | None:
"""
Resolve a filesystem path without following invalid/nonexistent paths.

```
Returns None when resolution fails.
"""

try:
    return path.expanduser().resolve(
        strict=False,
    )
except (
    OSError,
    RuntimeError,
):
    return None
```

def _is_within(
path: Path,
root: Path,
) -> bool:
"""
Return whether path is contained by root.

```
Both paths are resolved before comparison.
"""

resolved_path = _safe_resolve(
    path,
)

resolved_root = _safe_resolve(
    root,
)

if (
    resolved_path is None
    or resolved_root is None
):
    return False

try:
    resolved_path.relative_to(
        resolved_root,
    )
    return True
except ValueError:
    return False
```

def _find_nfo(
directory: Path,
) -> Path | None:
"""
Find the preferred NFO file in a media directory.

```
Preference:
1. directory-name.nfo
2. movie.nfo
3. first *.nfo file
"""

if not directory.is_dir():
    return None

candidates: list[Path] = []

candidates.append(
    directory / f"{directory.name}.nfo"
)

candidates.append(
    directory / "movie.nfo"
)

for candidate in candidates:
    if candidate.is_file():
        return candidate

try:
    nfos = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold() == ".nfo"
        ),
        key=lambda p: p.name.casefold(),
    )
except OSError:
    return None

return nfos[0] if nfos else None
```

def _find_poster(
directory: Path,
nfo_poster: str | None = None,
) -> Path | None:
"""
Find a safe local poster for a media item.

```
Remote poster URLs are deliberately ignored.

An NFO <thumb> value may point to:
- a local relative filename
- an absolute path inside the media directory

It may NOT escape the media directory.
"""

if not directory.is_dir():
    return None

# ------------------------------------------------------------------------
# 1. NFO-specified local poster.
# ------------------------------------------------------------------------

if nfo_poster:
    candidate_text = nfo_poster.strip()

    if candidate_text and not re.match(
        r"^[a-z][a-z0-9+.-]*://",
        candidate_text,
        flags=re.IGNORECASE,
    ):
        candidate = Path(
            candidate_text,
        )

        if not candidate.is_absolute():
            candidate = directory / candidate

        if (
            candidate.is_file()
            and candidate.suffix.casefold()
            in POSTER_EXTENSIONS
            and _is_within(
                candidate,
                directory,
            )
        ):
            return _safe_resolve(
                candidate,
            )

# ------------------------------------------------------------------------
# 2. Conventional poster filenames.
# ------------------------------------------------------------------------

preferred_names = (
    "poster",
    "folder",
    "cover",
    "thumb",
    directory.name,
)

for name in preferred_names:
    for extension in POSTER_EXTENSIONS:
        candidate = (
            directory
            / f"{name}{extension}"
        )

        if candidate.is_file():
            return _safe_resolve(
                candidate,
            )

# ------------------------------------------------------------------------
# 3. First matching image.
# ------------------------------------------------------------------------

try:
    images = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and path.suffix.casefold()
            in POSTER_EXTENSIONS
        ),
        key=lambda p: p.name.casefold(),
    )
except OSError:
    return None

for image in images:
    resolved = _safe_resolve(
        image,
    )

    if resolved and _is_within(
        resolved,
        directory,
    ):
        return resolved

return None
```

def _find_first_video(
directory: Path,
) -> Path | None:
"""
Find the first video file in a movie directory.

```
The scan is recursive to support layouts where the actual video is stored
in a nested directory.
"""

try:
    candidates = sorted(
        (
            path
            for path in directory.rglob("*")
            if _is_video_file(path)
        ),
        key=lambda p: (
            len(p.parts),
            p.name.casefold(),
        ),
    )
except OSError:
    return None

return candidates[0] if candidates else None
```

def _find_episode_video(
directory: Path,
) -> Path | None:
"""
Find the first video file that looks like a TV episode.
"""

```
try:
    candidates = sorted(
        (
            path
            for path in directory.rglob("*")
            if _is_video_file(path)
            and SEASON_EPISODE_RE.search(
                path.stem
            )
        ),
        key=lambda p: p.name.casefold(),
    )
except OSError:
    return None

if candidates:
    return candidates[0]

return _find_first_video(
    directory,
)
```

# ============================================================================

# METADATA HELPERS

# ============================================================================

def _title_from_directory(
directory: Path,
) -> str:
"""
Return a human-readable title from a directory name.
"""

```
name = directory.name.strip()

if not name:
    return "Unknown"

# Replace common separators while retaining intentional punctuation.
name = re.sub(
    r"[._]+",
    " ",
    name,
)

name = re.sub(
    r"\s+",
    " ",
    name,
).strip()

return name or "Unknown"
```

def _title_from_nfo_or_directory(
nfo_data: dict[str, Any],
directory: Path,
) -> str:
"""
Determine a media title.
"""

```
title = (
    _clean_text(
        nfo_data.get("title")
    )
    or _clean_text(
        nfo_data.get("originaltitle")
    )
    or _title_from_directory(
        directory,
    )
)

return title
```

def _year_from_metadata(
nfo_data: dict[str, Any],
directory: Path,
) -> int | None:
"""
Determine the media year.

```
NFO metadata takes precedence. If absent, attempt to find a year in the
directory name.
"""

year = _parse_int(
    nfo_data.get("year")
)

if year and 1800 <= year <= 2200:
    return year

match = YEAR_RE.search(
    directory.name
)

if match:
    try:
        return int(
            match.group(1)
        )
    except ValueError:
        return None

return None
```

def _normalize_provider(
provider: str | None,
) -> str | None:
"""
Normalize a provider name while preserving human-readable casing.
"""

```
if provider is None:
    return None

value = provider.strip()

if not value:
    return None

value = unicodedata.normalize(
    "NFKC",
    value,
)

value = re.sub(
    r"\s+",
    " ",
    value,
).strip()

return value or None
```

def _provider_for_directory(
media_root: Path,
item_directory: Path,
) -> str | None:
"""
Determine the provider from the first directory below the media root.

```
Example:

    /movies/Netflix/Movie A

returns:

    Netflix
"""

try:
    relative = item_directory.resolve().relative_to(
        media_root.resolve()
    )
except (
    ValueError,
    OSError,
    RuntimeError,
):
    return None

parts = relative.parts

if len(parts) < 2:
    # The media item is directly below the root and therefore has no
    # provider directory.
    return None

return _normalize_provider(
    parts[0]
)
```

def _extract_episode_numbers(
video_path: Path,
) -> tuple[int | None, int | None]:
"""
Extract season and episode numbers from a video filename.
"""

```
match = SEASON_EPISODE_RE.search(
    video_path.stem
)

if not match:
    return (
        None,
        None,
    )

try:
    season = int(
        match.group("season")
    )
except (TypeError, ValueError):
    season = None

try:
    episode = int(
        match.group("episode")
    )
except (TypeError, ValueError):
    episode = None

return (
    season,
    episode,
)
```

def _extract_season(
path: Path,
) -> int | None:
"""
Extract a season number from a path.
"""

```
match = SEASON_ONLY_RE.search(
    str(path),
)

if not match:
    return None

try:
    return int(
        match.group("season")
    )
except (TypeError, ValueError):
    return None
```

# ============================================================================

# SCAN DATA

# ============================================================================

def _build_media_record(
*,
kind: str,
directory: Path,
media_root: Path,
) -> dict[str, Any]:
"""
Build one database record from a media directory.
"""

```
nfo_path = _find_nfo(
    directory,
)

nfo_data = (
    _parse_nfo(nfo_path)
    if nfo_path
    else {}
)

if kind == "movie":
    video_path = _find_first_video(
        directory,
    )
else:
    video_path = _find_episode_video(
        directory,
    )

title = _title_from_nfo_or_directory(
    nfo_data,
    directory,
)

year = _year_from_metadata(
    nfo_data,
    directory,
)

provider = _provider_for_directory(
    media_root,
    directory,
)

poster_path = _find_poster(
    directory,
    _clean_text(
        nfo_data.get("poster")
    ),
)

season = _parse_int(
    nfo_data.get("season")
)

episode = _parse_int(
    nfo_data.get("episode")
)

if kind == "series" and video_path:
    detected_season, detected_episode = (
        _extract_episode_numbers(
            video_path
        )
    )

    season = (
        season
        if season is not None
        else detected_season
    )

    episode = (
        episode
        if episode is not None
        else detected_episode
    )

return {
    "kind": kind,
    "title": title,
    "year": year,
    "provider": provider,
    "path": str(
        directory.resolve()
    ),
    "nfo_path": (
        str(
            nfo_path.resolve()
        )
        if nfo_path
        else None
    ),
    "poster_path": (
        str(poster_path)
        if poster_path
        else None
    ),
    "poster": _clean_text(
        nfo_data.get("poster")
    ),
    "rating": _parse_float(
        nfo_data.get("rating")
    ),
    "runtime": _normalize_runtime(
        nfo_data.get("runtime")
    ),
    "season": season,
    "episode": episode,
}
```

# ============================================================================

# SCAN DISCOVERY

# ============================================================================

def _iter_provider_directories(
root: Path,
) -> Iterator[Path]:
"""
Yield directories directly below a media root.

```
Each direct child is interpreted as a provider directory.

If media is stored directly below the root, that layout is still handled
separately by _iter_item_directories().
"""

if not root.is_dir():
    return

try:
    children = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
        ),
        key=lambda p: p.name.casefold(),
    )
except OSError:
    return

yield from children
```

def _iter_item_directories(
root: Path,
) -> Iterator[Path]:
"""
Discover media item directories.

```
Provider layout is preferred:

    root/provider/item

A direct layout is also accepted:

    root/item

Directories containing an NFO or video file are considered candidates.
"""

if not root.is_dir():
    return

try:
    first_level = sorted(
        (
            path
            for path in root.iterdir()
            if path.is_dir()
            and not path.is_symlink()
        ),
        key=lambda p: p.name.casefold(),
    )
except OSError:
    return

# ------------------------------------------------------------------------
# Provider layout:
#
# root/provider/item
# ------------------------------------------------------------------------

for provider_dir in first_level:

    try:
        second_level = sorted(
            (
                path
                for path in provider_dir.iterdir()
                if path.is_dir()
                and not path.is_symlink()
            ),
            key=lambda p: p.name.casefold(),
        )
    except OSError:
        continue

    for item_dir in second_level:
        if _looks_like_media_item(
            item_dir,
        ):
            yield item_dir

    # --------------------------------------------------------------------
    # Also support:
    #
    # root/provider/file.mkv
    #
    # by treating the provider directory as the item when it itself
    # contains media metadata.
    # --------------------------------------------------------------------

    if _looks_like_media_item(
        provider_dir,
    ):
        yield provider_dir

# ------------------------------------------------------------------------
# Direct layout:
#
# root/item
# ------------------------------------------------------------------------

for candidate in first_level:
    if _looks_like_media_item(
        candidate,
    ):
        yield candidate
```

def _looks_like_media_item(
directory: Path,
) -> bool:
"""
Determine whether a directory appears to represent a media item.
"""

```
if not directory.is_dir():
    return False

if _find_nfo(
    directory,
) is not None:
    return True

# Avoid recursively scanning the complete tree more than necessary.
try:
    for path in directory.iterdir():
        if path.is_file() and _is_video_file(path):
            return True
except OSError:
    return False

return False
```

# ============================================================================

# LIBRARY CLASS

# ============================================================================

class Library:
"""
Media library scanner and repository.
"""

```
def __init__(
    self,
    db_path: str | None = None,
    movies_dir: str | None = None,
    series_dir: str | None = None,
) -> None:

    self.db_path = (
        db_path
        or DATABASE_PATH
    )

    self.movies_dir = Path(
        movies_dir
        or MOVIES_DIR
    ).expanduser()

    self.series_dir = Path(
        series_dir
        or SERIES_DIR
    ).expanduser()

    self._scan_lock = threading.Lock()

# ========================================================================
# DATABASE
# ========================================================================

@contextmanager
def _db(self) -> Iterator[sqlite3.Connection]:
    """
    Open the configured library database.

    The global get_db() uses DATABASE_PATH. For the default Library this
    is identical to self.db_path. A custom db_path is supported for tests
    and isolated instances.
    """

    if self.db_path == DATABASE_PATH:
        with get_db() as conn:
            yield conn
        return

    database = Path(
        self.db_path,
    ).expanduser()

    database.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    conn = sqlite3.connect(
        str(database),
        timeout=30,
        check_same_thread=False,
    )

    try:
        _configure_connection(
            conn,
        )

        yield conn

        conn.commit()

    except Exception:
        conn.rollback()
        raise

    finally:
        conn.close()

# ========================================================================
# SCANNING
# ========================================================================

def scan(self) -> int:
    """
    Scan movies and series.

    Returns the number of media records currently stored after scanning.
    """

    if not self._scan_lock.acquire(
        blocking=False
    ):
        raise RuntimeError(
            "A library scan is already running."
        )

    try:
        return self._scan_locked()

    finally:
        self._scan_lock.release()

def _scan_locked(self) -> int:
    """
    Execute the actual scan while holding the process-local lock.
    """

    init_db()

    discovered: dict[str, dict[str, Any]] = {}

    roots = (
        MediaRoot(
            kind="movie",
            path=self.movies_dir,
        ),
        MediaRoot(
            kind="series",
            path=self.series_dir,
        ),
    )

    for root in roots:

        if not root.path.exists():
            logger.warning(
                "Media root does not exist: %s",
                root.path,
            )
            continue

        if not root.path.is_dir():
            logger.warning(
                "Media root is not a directory: %s",
                root.path,
            )
            continue

        logger.info(
            "Scanning %s library: %s",
            root.kind,
            root.path,
        )

        for directory in _iter_item_directories(
            root.path,
        ):

            if not _is_within(
                directory,
                root.path,
            ):
                logger.warning(
                    "Skipping path outside media root: %s",
                    directory,
                )
                continue

            try:
                record = _build_media_record(
                    kind=root.kind,
                    directory=directory,
                    media_root=root.path,
                )
            except Exception:
                logger.exception(
                    "Failed to process media directory: %s",
                    directory,
                )
                continue

            path_key = record["path"]

            if path_key in discovered:
                continue

            discovered[path_key] = record

    self._replace_or_update_records(
        discovered.values()
    )

    self._remove_missing_records(
        set(discovered.keys())
    )

    count = self.count()

    logger.info(
        "Library scan complete: %s media items",
        count,
    )

    return count

# ========================================================================
# UPSERT
# ========================================================================

def _replace_or_update_records(
    self,
    records: Sequence[dict[str, Any]],
) -> None:
    """
    Insert or update discovered media records.
    """

    with self._db() as conn:

        for record in records:

            existing = conn.execute(
                """
                SELECT id
                FROM media
                WHERE path = ?
                LIMIT 1
                """,
                (
                    record["path"],
                ),
            ).fetchone()

            now = _utc_now()

            if existing is None:

                conn.execute(
                    """
                    INSERT INTO media (
                        kind,
                        title,
                        year,
                        provider,
                        path,
                        nfo_path,
                        poster_path,
                        poster,
                        rating,
                        runtime,
                        season,
                        episode,
                        added_at,
                        updated_at
                    )
                    VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, ?, ?
                    )
                    """,
                    (
                        record["kind"],
                        record["title"],
                        record["year"],
                        record["provider"],
                        record["path"],
                        record["nfo_path"],
                        record["poster_path"],
                        record["poster"],
                        record["rating"],
                        record["runtime"],
                        record["season"],
                        record["episode"],
                        now,
                        now,
                    ),
                )

            else:

                conn.execute(
                    """
                    UPDATE media
                    SET
                        kind = ?,
                        title = ?,
                        year = ?,
                        provider = ?,
                        nfo_path = ?,
                        poster_path = ?,
                        poster = ?,
                        rating = ?,
                        runtime = ?,
                        season = ?,
                        episode = ?,
                        updated_at = ?
                    WHERE path = ?
                    """,
                    (
                        record["kind"],
                        record["title"],
                        record["year"],
                        record["provider"],
                        record["nfo_path"],
                        record["poster_path"],
                        record["poster"],
                        record["rating"],
                        record["runtime"],
                        record["season"],
                        record["episode"],
                        now,
                        record["path"],
                    ),
                )

# ========================================================================
# CLEANUP
# ========================================================================

def _remove_missing_records(
    self,
    current_paths: set[str],
) -> None:
    """
    Remove database records whose media directories no longer exist.
    """

    with self._db() as conn:

        rows = conn.execute(
            """
            SELECT id, path
            FROM media
            """
        ).fetchall()

        for row in rows:

            path = str(
                row["path"]
            )

            if path in current_paths:
                continue

            # Do not use SQL GLOB against arbitrary filenames. Path
            # comparisons are done in Python to avoid wildcard semantics.
            if not Path(path).exists():
                conn.execute(
                    """
                    DELETE FROM media
                    WHERE id = ?
                    """,
                    (
                        row["id"],
                    ),
                )

# ========================================================================
# QUERIES
# ========================================================================

def count(self) -> int:
    """
    Return the total number of media records.
    """

    with self._db() as conn:

        row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM media
            """
        ).fetchone()

    return int(
        row["count"]
    ) if row else 0

def stats(self) -> dict[str, Any]:
    """
    Return library statistics.
    """

    with self._db() as conn:

        total_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM media
            """
        ).fetchone()

        movie_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM media
            WHERE kind = 'movie'
            """
        ).fetchone()

        series_row = conn.execute(
            """
            SELECT COUNT(*) AS count
            FROM media
            WHERE kind = 'series'
            """
        ).fetchone()

        providers = conn.execute(
            """
            SELECT
                provider,
                COUNT(*) AS count
            FROM media
            WHERE provider IS NOT NULL
            AND provider != ''
            GROUP BY provider
            ORDER BY provider COLLATE NOCASE
            """
        ).fetchall()

    return {
        "total": int(
            total_row["count"]
        ) if total_row else 0,
        "movies": int(
            movie_row["count"]
        ) if movie_row else 0,
        "series": int(
            series_row["count"]
        ) if series_row else 0,
        "providers": [
            {
                "name": row["provider"],
                "count": int(
                    row["count"]
                ),
            }
            for row in providers
        ],
    }

def providers(self) -> list[str]:
    """
    Return all discovered provider names.
    """

    with self._db() as conn:

        rows = conn.execute(
            """
            SELECT DISTINCT provider
            FROM media
            WHERE provider IS NOT NULL
            AND provider != ''
            ORDER BY provider COLLATE NOCASE
            """
        ).fetchall()

    return [
        str(
            row["provider"]
        )
        for row in rows
    ]

def random_item(
    self,
    kind: str | None = None,
    provider: str | None = None,
    exclude: Sequence[str] | None = None,
) -> dict[str, Any] | None:
    """
    Return one random media item.

    SQLite's RANDOM() is sufficient for the scale expected by this
    application and keeps the query simple.
    """

    conditions: list[str] = []
    parameters: list[Any] = []

    if kind:
        conditions.append(
            "kind = ?"
        )
        parameters.append(
            kind
        )

    if provider:
        if provider.casefold() in {
            "alle",
            "all",
            "*",
        }:
            pass
        else:
            conditions.append(
                "provider = ?"
            )
            parameters.append(
                provider
            )

    if exclude:
        cleaned_titles = [
            str(title).strip()
            for title in exclude
            if str(title).strip()
        ]

        if cleaned_titles:
            placeholders = ",".join(
                "?"
                for _ in cleaned_titles
            )

            conditions.append(
                f"title NOT IN ({placeholders})"
            )

            parameters.extend(
                cleaned_titles
            )

    query = """
        SELECT
            id,
            kind,
            title,
            year,
            provider,
            path,
            nfo_path,
            poster_path,
            poster,
            rating,
            runtime,
            season,
            episode,
            added_at,
            updated_at
        FROM media
    """

    if conditions:
        query += (
            " WHERE "
            + " AND ".join(
                conditions
            )
        )

    query += """
        ORDER BY RANDOM()
        LIMIT 1
    """

    with self._db() as conn:

        row = conn.execute(
            query,
            parameters,
        ).fetchone()

    return (
        dict(row)
        if row
        else None
    )

def get_by_id(
    self,
    media_id: int,
) -> dict[str, Any] | None:
    """
    Return a media record by ID.
    """

    with self._db() as conn:

        row = conn.execute(
            """
            SELECT
                id,
                kind,
                title,
                year,
                provider,
                path,
                nfo_path,
                poster_path,
                poster,
                rating,
                runtime,
                season,
                episode,
                added_at,
                updated_at
            FROM media
            WHERE id = ?
            LIMIT 1
            """,
            (
                media_id,
            ),
        ).fetchone()

    return (
        dict(row)
        if row
        else None
    )

# ========================================================================
# POSTERS
# ========================================================================

def poster_for_id(
    self,
    media_id: int,
) -> str | None:
    """
    Resolve a safe local poster path for a media ID.

    Multiple validation layers are used:
    1. database lookup
    2. path resolution
    3. extension validation
    4. media-root containment
    5. regular-file check
    """

    item = self.get_by_id(
        media_id,
    )

    if not item:
        return None

    poster_path_value = item.get(
        "poster_path"
    )

    if not poster_path_value:
        return None

    poster_path = Path(
        str(poster_path_value)
    )

    resolved_poster = _safe_resolve(
        poster_path,
    )

    if resolved_poster is None:
        return None

    if (
        resolved_poster.suffix.casefold()
        not in POSTER_EXTENSIONS
    ):
        return None

    if not resolved_poster.is_file():
        return None

    roots = (
        self.movies_dir,
        self.series_dir,
    )

    if not any(
        _is_within(
            resolved_poster,
            root,
        )
        for root in roots
    ):
        logger.warning(
            "Rejected poster outside media roots: %s",
            resolved_poster,
        )
        return None

    return str(
        resolved_poster
    )
```

# ============================================================================

# UTC TIME

# ============================================================================

def _utc_now() -> str:
"""
Return current UTC time in ISO-8601 form.
"""

```
from datetime import datetime, timezone

return datetime.now(
    timezone.utc
).isoformat()
```

# ============================================================================

# DEFAULT LIBRARY INSTANCE

# ============================================================================

library = Library()

**all** = [
"DATABASE_PATH",
"MOVIES_DIR",
"POSTER_EXTENSIONS",
"SERIES_DIR",
"VIDEO_EXTENSIONS",
"Library",
"get_db",
"init_db",
"library",
]
