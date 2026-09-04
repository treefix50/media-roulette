"""
Media Roulette - media library scanner and SQLite repository.

The library scanner discovers movies and TV series from the configured,
read-only media directories.

Expected layouts:

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

The provider is taken from the first directory below the configured media
root. Providers are therefore dynamic and are not restricted to a
hard-coded list.

Important security properties:

* Media directories are treated as read-only.
* The SQLite database is stored separately from the media library.
* Filesystem paths are never intended to be exposed directly to clients.
* Poster paths are validated against the configured media roots.
* Remote poster URLs are never followed by this module.
* Symlinked media directories are not scanned.
* Database writes are limited to the application state database.
  """

from **future** import annotations

import logging
import os
import re
import sqlite3
import threading
import unicodedata
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
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

# ============================================================================

# SUPPORTED FILE TYPES

# ============================================================================

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

POSTER_EXTENSIONS: tuple[str, ...] = (
".jpg",
".jpeg",
".png",
".webp",
)

# ============================================================================

# REGULAR EXPRESSIONS

# ============================================================================

SEASON_EPISODE_RE = re.compile(
r"(?i)\bS(?P<season>\d{1,3})E(?P<episode>\d{1,4})\b"
)

SEASON_ONLY_RE = re.compile(
r"(?i)\bS(?P<season>\d{1,3})\b"
)

YEAR_RE = re.compile(
r"\b(19\d{2}|20\d{2})\b"
)

REMOTE_URL_RE = re.compile(
r"^[a-z][a-z0-9+.-]*://",
re.IGNORECASE,
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
    DATABASE_PATH
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
A fresh connection is used per operation. This avoids sharing SQLite
connections across FastAPI requests and threads.

Successful operations are committed automatically. Failed operations
are rolled back.
"""

database = Path(
    DATABASE_PATH
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
        conn
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

```
Table names are internal constants and never originate from HTTP input.
"""

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
Identifiers are internal application constants.
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
Migrations are intentionally lightweight because Media Roulette uses a
small SQLite database.
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
    value
)

if text is None:
    return None

try:
    result = float(
        text.replace(
            ",",
            ".",
        )
    )
except ValueError:
    return None

if not result == result:
    return None

if result < 0:
    return None

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
    value
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

```
Kodi-style NFO files normally store runtime in minutes.
"""

result = _parse_int(
    value
)

if result is None:
    return None

if result <= 0:
    return None

return result
```

def _parse_nfo(
path: Path,
) -> dict[str, Any]:
"""
Parse a Kodi-style NFO file.

```
Only the small metadata subset needed by Media Roulette is extracted.
"""

result: dict[str, Any] = {}

try:
    tree = ET.parse(
        path
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
        name
    )

    if element is None:
        return None

    return _clean_text(
        element.text
    )

result["title"] = text(
    "title"
)

result["originaltitle"] = text(
    "originaltitle"
)

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
Return True if path is a supported regular video file.
"""

```
try:
    return (
        path.is_file()
        and not path.is_symlink()
        and path.suffix.casefold()
        in VIDEO_EXTENSIONS
    )
except OSError:
    return False
```

def _safe_resolve(
path: Path,
) -> Path | None:
"""
Resolve a filesystem path safely.

```
Returns None when resolution fails.
"""

try:
    return path.expanduser().resolve(
        strict=False
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
    path
)

resolved_root = _safe_resolve(
    root
)

if (
    resolved_path is None
    or resolved_root is None
):
    return False

try:
    resolved_path.relative_to(
        resolved_root
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

1. <directory-name>.nfo
2. movie.nfo
3. first *.nfo alphabetically
"""

if not directory.is_dir():
    return None

candidates = (
    directory / f"{directory.name}.nfo",
    directory / "movie.nfo",
)

for candidate in candidates:

    try:
        if (
            candidate.is_file()
            and not candidate.is_symlink()
        ):
            return candidate
    except OSError:
        continue

try:
    nfos = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() == ".nfo"
        ),
        key=lambda p: p.name.casefold(),
    )

except OSError:
    return None

return (
    nfos[0]
    if nfos
    else None
)
```

def _find_poster(
directory: Path,
nfo_poster: str | None = None,
) -> Path | None:
"""
Find a safe local poster for a media directory.

```
Remote URLs are ignored.

A local NFO poster may be relative or absolute, but it must resolve
inside the media directory.
"""

if not directory.is_dir():
    return None

# ------------------------------------------------------------------------
# 1. NFO-specified local poster.
# ------------------------------------------------------------------------

if nfo_poster:

    candidate_text = (
        nfo_poster.strip()
    )

    if (
        candidate_text
        and not REMOTE_URL_RE.match(
            candidate_text
        )
    ):

        candidate = Path(
            candidate_text
        )

        if not candidate.is_absolute():
            candidate = (
                directory
                / candidate
            )

        if (
            candidate.is_file()
            and not candidate.is_symlink()
            and candidate.suffix.casefold()
            in POSTER_EXTENSIONS
            and _is_within(
                candidate,
                directory,
            )
        ):

            return _safe_resolve(
                candidate
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

        try:
            valid = (
                candidate.is_file()
                and not candidate.is_symlink()
            )
        except OSError:
            valid = False

        if valid:

            resolved = _safe_resolve(
                candidate
            )

            if (
                resolved
                and _is_within(
                    resolved,
                    directory,
                )
            ):
                return resolved

# ------------------------------------------------------------------------
# 3. First matching local image.
# ------------------------------------------------------------------------

try:
    images = sorted(
        (
            path
            for path in directory.iterdir()
            if path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold()
            in POSTER_EXTENSIONS
        ),
        key=lambda p: p.name.casefold(),
    )

except OSError:
    return None

for image in images:

    resolved = _safe_resolve(
        image
    )

    if (
        resolved
        and _is_within(
            resolved,
            directory,
        )
    ):
        return resolved

return None
```

def _find_first_video(
directory: Path,
) -> Path | None:
"""
Find the first supported video file below a media directory.

```
Recursive discovery is used to support nested movie structures.
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

return (
    candidates[0]
    if candidates
    else None
)
```

def _find_episode_video(
directory: Path,
) -> Path | None:
"""
Find the first video file that looks like a TV episode.

```
Episode filenames containing SxxEyy are preferred.
"""

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
    directory
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
return (
    _clean_text(
        nfo_data.get("title")
    )
    or _clean_text(
        nfo_data.get("originaltitle")
    )
    or _title_from_directory(
        directory
    )
)
```

def _year_from_metadata(
nfo_data: dict[str, Any],
directory: Path,
) -> int | None:
"""
Determine the media year.

```
NFO metadata takes precedence. If unavailable, a year is extracted from
the directory name.
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
    relative = (
        item_directory
        .resolve()
        .relative_to(
            media_root.resolve()
        )
    )

except (
    ValueError,
    OSError,
    RuntimeError,
):
    return None

parts = relative.parts

if len(parts) < 2:
    return None

return _normalize_provider(
    parts[0]
)
```

def _extract_episode_numbers(
video_path: Path,
) -> tuple[
int | None,
int | None,
]:
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
except (
    TypeError,
    ValueError,
):
    season = None

try:
    episode = int(
        match.group("episode")
    )
except (
    TypeError,
    ValueError,
):
    episode = None

return (
    season,
    episode,
)
```

# ============================================================================

# MEDIA RECORD BUILDING

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
    directory
)

nfo_data = (
    _parse_nfo(
        nfo_path
    )
    if nfo_path
    else {}
)

if kind == "movie":

    video_path = _find_first_video(
        directory
    )

else:

    video_path = _find_episode_video(
        directory
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

    if season is None:
        season = detected_season

    if episode is None:
        episode = detected_episode

resolved_directory = _safe_resolve(
    directory
)

if resolved_directory is None:
    raise OSError(
        f"Could not resolve media directory: {directory}"
    )

return {
    "kind": kind,
    "title": title,
    "year": year,
    "provider": provider,
    "path": str(
        resolved_directory
    ),
    "nfo_path": (
        str(
            _safe_resolve(
                nfo_path
            )
        )
        if nfo_path
        and _safe_resolve(
            nfo_path
        )
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

def _iter_item_directories(
root: Path,
) -> Iterator[Path]:
"""
Discover media item directories.

```
Preferred layout:

    root/provider/item

Direct layout is also supported:

    root/item

A directory is considered a media item when it contains either an NFO
file or a video file.
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
            item_dir
        ):
            yield item_dir

    # Also support:
    #
    # root/provider/file.mkv
    #
    # by treating provider_dir itself as the item.
    if _looks_like_media_item(
        provider_dir
    ):
        yield provider_dir

# ------------------------------------------------------------------------
# Direct layout:
#
# root/item
# ------------------------------------------------------------------------

for candidate in first_level:

    if _looks_like_media_item(
        candidate
    ):
        yield candidate
```

def _looks_like_media_item(
directory: Path,
) -> bool:
"""
Determine whether a directory appears to represent a media item.

```
This intentionally performs only a shallow check. The expensive
recursive video search happens later for confirmed candidates.
"""

if not directory.is_dir():
    return False

try:
    for path in directory.iterdir():

        if (
            path.is_file()
            and not path.is_symlink()
            and path.suffix.casefold() == ".nfo"
        ):
            return True

        if _is_video_file(
            path
        ):
            return True

except OSError:
    return False

return False
```

# ============================================================================

# LIBRARY

# ============================================================================

class Library:
"""
Media library scanner and SQLite repository.
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
def _db(
    self,
) -> Iterator[sqlite3.Connection]:
    """
    Open the configured library database.

    Custom db_path values are supported for tests.
    """

    if self.db_path == DATABASE_PATH:

        with get_db() as conn:
            yield conn

        return

    database = Path(
        self.db_path
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
            conn
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

def scan(
    self,
) -> int:
    """
    Scan movies and series.

    Returns the number of media records currently stored.
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

def _scan_locked(
    self,
) -> int:
    """
    Execute the actual scan while holding the process-local lock.
    """

    # For the default application database init_db() is correct.
    # For custom test databases, initialize the schema locally.
    self._initialize_instance_database()

    discovered: dict[
        str,
        dict[str, Any],
    ] = {}

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

        resolved_root = _safe_resolve(
            root.path
        )

        if resolved_root is None:

            logger.warning(
                "Could not resolve media root: %s",
                root.path,
            )

            continue

        logger.info(
            "Scanning %s library: %s",
            root.kind,
            resolved_root,
        )

        for directory in _iter_item_directories(
            resolved_root
        ):

            if not _is_within(
                directory,
                resolved_root,
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
                    media_root=resolved_root,
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
        set(
            discovered.keys()
        )
    )

    count = self.count()

    logger.info(
        "Library scan complete: %s media items",
        count,
    )

    return count

def _initialize_instance_database(
    self,
) -> None:
    """
    Initialize this Library instance's database.

    This is needed when tests use a custom db_path.
    """

    if self.db_path == DATABASE_PATH:
        init_db()
        return

    with self._db() as conn:

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

            try:
                exists = Path(
                    path
                ).exists()
            except OSError:
                exists = False

            if not exists:

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

def count(
    self,
) -> int:
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

    return (
        int(
            row["count"]
        )
        if row
        else 0
    )

def stats(
    self,
) -> dict[str, Any]:
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
        "total": (
            int(
                total_row["count"]
            )
            if total_row
            else 0
        ),
        "movies": (
            int(
                movie_row["count"]
            )
            if movie_row
            else 0
        ),
        "series": (
            int(
                series_row["count"]
            )
            if series_row
            else 0
        ),
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

def providers(
    self,
) -> list[str]:
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
    """

    conditions: list[str] = []
    parameters: list[Any] = []

    if kind:

        if kind not in {
            "movie",
            "series",
        }:
            raise ValueError(
                "kind must be 'movie' or 'series'."
            )

        conditions.append(
            "kind = ?"
        )

        parameters.append(
            kind
        )

    if provider:

        if provider.casefold() not in {
            "alle",
            "all",
            "*",
        }:

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

    Validation layers:

    1. database lookup
    2. path resolution
    3. extension validation
    4. regular-file check
    5. containment inside configured media roots
    """

    item = self.get_by_id(
        media_id
    )

    if not item:
        return None

    poster_path_value = item.get(
        "poster_path"
    )

    if not poster_path_value:
        return None

    poster_path = Path(
        str(
            poster_path_value
        )
    )

    resolved_poster = _safe_resolve(
        poster_path
    )

    if resolved_poster is None:
        return None

    if (
        resolved_poster.suffix.casefold()
        not in POSTER_EXTENSIONS
    ):
        return None

    try:
        if (
            not resolved_poster.is_file()
            or resolved_poster.is_symlink()
        ):
            return None
    except OSError:
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
return datetime.now(
    timezone.utc
).isoformat()
```

# ============================================================================

# DEFAULT LIBRARY INSTANCE

# ============================================================================

library = Library()

# ============================================================================

# DATABASE INITIALIZATION

# ============================================================================

# Initialize the default application database on module import.

#

# This preserves compatibility with the existing application structure.

init_db()

# ============================================================================

# EXPORTS

# ============================================================================

**all** = [
"DATABASE_PATH",
"MOVIES_DIR",
"POSTER_EXTENSIONS",
"SERIES_DIR",
"VIDEO_EXTENSIONS",
"Library",
"MediaRoot",
"get_db",
"init_db",
"library",
]
