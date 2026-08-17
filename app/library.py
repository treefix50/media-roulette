from __future__ import annotations

import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts"}


def _clean(value: str | None) -> str | None:
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None


def _nfo_value(root: ET.Element, name: str) -> str | None:
    node = root.find(name)
    return _clean(node.text if node is not None else None)


def parse_nfo(path: Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError):
        return {}

    data: dict[str, Any] = {}
    for field in ("title", "originaltitle", "year", "plot", "runtime", "rating", "premiered", "tmdbid", "imdbid"):
        value = _nfo_value(root, field)
        if value:
            data[field] = value

    genres = [_clean(n.text) for n in root.findall("genre")]
    genres = [g for g in genres if g]
    if genres:
        data["genres"] = ", ".join(genres)

    return data


def parse_name(name: str) -> tuple[str, int | None]:
    text = Path(name).stem
    text = re.sub(r"[._]", " ", text)
    match = re.search(r"\b(19\d{2}|20\d{2})\b", text)
    year = int(match.group(1)) if match else None
    if match:
        text = text[:match.start()].strip(" -")
    return re.sub(r"\s+", " ", text).strip(), year


class Library:
    def __init__(self, db_path: str, movies_dir: str, series_dir: str):
        self.db_path = db_path
        self.movies_dir = Path(movies_dir)
        self.series_dir = Path(series_dir)
        self._scan_lock = threading.Lock()
        self._init_db()

    def _connect(self):
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    def _init_db(self):
        with self._connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""
                CREATE TABLE IF NOT EXISTS media (
                    id INTEGER PRIMARY KEY,
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
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_kind ON media(kind)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_provider ON media(provider)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_title ON media(title)")

    def _scan_root(self, root_dir: Path, kind: str) -> tuple[list[dict[str, Any]], bool]:
        if not root_dir.exists():
            return [], False

        items = []
        for provider_dir in sorted(root_dir.iterdir()):
            if not provider_dir.is_dir():
                continue

            for entry in sorted(provider_dir.rglob("*")):
                if not entry.is_file():
                    continue
                if entry.suffix.lower() not in VIDEO_EXTENSIONS:
                    continue

                nfo = None
                if kind == "movie":
                    nfo = entry.parent / "movie.nfo"
                    if not nfo.exists():
                        nfo = entry.parent / f"{entry.stem}.nfo"
                elif kind == "series":
                    nfo = entry.parent / "tvshow.nfo"
                    if not nfo.exists():
                        nfo = entry.parent / entry.name.replace(entry.suffix, ".nfo")

                data = parse_nfo(nfo)
                title = data.get("title")
                year = data.get("year")
                if not title:
                    title, year_from_name = parse_name(entry.name)
                    if not year:
                        year = year_from_name

                rating = data.get("rating")
                if isinstance(rating, str):
                    try:
                        rating = float(rating)
                    except ValueError:
                        rating = None

                runtime = data.get("runtime")
                if isinstance(runtime, str):
                    try:
                        runtime = int(float(runtime))
                    except ValueError:
                        runtime = None

                items.append({
                    "kind": kind,
                    "provider": provider_dir.name,
                    "title": title or entry.name,
                    "year": year,
                    "plot": data.get("plot"),
                    "genres": data.get("genres"),
                    "rating": rating,
                    "runtime": runtime,
                    "path": str(entry),
                    "nfo_path": str(nfo) if nfo and nfo.exists() else None,
                })

        return items, True

    def scan(self) -> int:
        if not self._scan_lock.acquire(blocking=False):
            stats = self.stats()
            return stats["total"]

        try:
            movie_items, movies_ok = self._scan_root(self.movies_dir, "movie")
            series_items, series_ok = self._scan_root(self.series_dir, "series")
            items = movie_items + series_items

            with self._connect() as db:
                db.execute("BEGIN")

                for item in items:
                    db.execute("""
                        INSERT OR REPLACE INTO media 
                        (kind, provider, title, year, plot, genres, rating, runtime, path, nfo_path, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (
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
                    ))

                if movies_ok:
                    movie_paths = {x["path"] for x in movie_items}
                    if movie_paths:
                        placeholders = ",".join("?" * len(movie_paths))
                        db.execute(f"DELETE FROM media WHERE kind='movie' AND path NOT IN ({placeholders})", tuple(movie_paths))
                    else:
                        db.execute("DELETE FROM media WHERE kind='movie'")

                if series_ok:
                    series_paths = {x["path"] for x in series_items}
                    if series_paths:
                        placeholders = ",".join("?" * len(series_paths))
                        db.execute(f"DELETE FROM media WHERE kind='series' AND path NOT IN ({placeholders})", tuple(series_paths))
                    else:
                        db.execute("DELETE FROM media WHERE kind='series'")

                db.commit()

                total_count = db.execute("SELECT COUNT(*) FROM media").fetchone()[0]
                return total_count

        finally:
            self._scan_lock.release()

    def random_item(
        self,
        kind: str | None = None,
        provider: str | None = None,
        exclude_titles: list[str] | None = None,
    ) -> dict[str, Any] | None:
        query = "SELECT * FROM media WHERE 1=1"
        params: list[Any] = []

        if kind in {"movie", "series"}:
            query += " AND kind=?"
            params.append(kind)

        if provider:
            query += " AND provider=?"
            params.append(provider)

        excluded = [p for p in (exclude_titles or []) if p]
        if excluded:
            query += " AND title NOT IN (" + ",".join("?" * len(excluded)) + ")"
            params.extend(excluded)

        query += " ORDER BY RANDOM() LIMIT 1"

        with self._connect() as db:
            row = db.execute(query, params).fetchone()
            return dict(row) if row else None

    def stats(self) -> dict[str, Any]:
        with self._connect() as db:
            return {
                "total": db.execute("SELECT COUNT(*) FROM media").fetchone()[0],
                "movies": db.execute("SELECT COUNT(*) FROM media WHERE kind='movie'").fetchone()[0],
                "series": db.execute("SELECT COUNT(*) FROM media WHERE kind='series'").fetchone()[0],
                "providers": [row["provider"] for row in db.execute("SELECT DISTINCT provider FROM media ORDER BY provider").fetchall()],
            }
