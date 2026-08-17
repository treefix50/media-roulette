from __future__ import annotations

import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".m2ts"}
VALID_PROVIDERS = {"netflix", "prime", "peacock", "paramount", "sky", "disney", "max"}

def _clean(value: str | None) -> str | None:
    """Entfernt überflüssige Leerzeichen und bereinigt Text."""
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value or None

def _nfo_value(root: ET.Element, name: str) -> str | None:
    """Extrahiert Wert aus NFO-XML-Element."""
    node = root.find(name)
    return _clean(node.text if node is not None else None)

def parse_nfo(path: Path | None) -> dict[str, Any]:
    """Parst NFO-Datei und extrahiert Metadaten."""
    if not path or not path.exists():
        return {}
    
    try:
        root = ET.parse(path).getroot()
    except (ET.ParseError, OSError, IOError):
        return {}
    
    data: dict[str, Any] = {}
    
    # Standard-Felder
    for field in ("title", "originaltitle", "year", "plot", "runtime", "rating", "premiered", "tmdbid", "imdbid"):
        value = _nfo_value(root, field)
        if value:
            data[field] = value
    
    # Genre-Extraktion
    genres = [_clean(n.text) for n in root.findall("genre")]
    genres = [g for g in genres if g]
    if genres:
        data["genres"] = ", ".join(genres)
    
    # Rating: Falls <= 5, verdoppeln (TMDB hat 0-10, manche Quellen 0-5)
    rating = data.get("rating")
    if isinstance(rating, str):
        try:
            rating = float(rating)
        except ValueError:
            rating = None
    if rating and 0 < rating <= 5:
        rating = rating * 2
    data["rating"] = rating
    
    # Laufzeit: Immer in Minuten
    runtime = data.get("runtime")
    if isinstance(runtime, str):
        try:
            runtime = int(float(runtime))
        except ValueError:
            runtime = None
    # Falls < 100, könnte in Stunden sein → Minuten umrechnen
    if runtime and 0 < runtime < 100:
        runtime = runtime * 60
    data["runtime"] = runtime
    
    return data

def parse_name(name: str) -> tuple[str, int | None]:
    """Extrahiert Titel und Jahr aus Ordnername oder Dateiname."""
    text = Path(name).stem
    text = re.sub(r"[._]", " ", text)
    
    # Jahr-Muster: (YYYY) oder YYYY
    match = re.search(r"\b(19\d{2}|20\d{2})\b(?:\s*\(\1\))?", text)
    year = int(match.group(1)) if match else None
    
    if match:
        # Entferne Jahr aus Titel
        text = text[:match.start()].strip(" -().")
    
    # Bereinige Titel (doppelte Leerzeichen entfernen, trailing Zeichen)
    text = re.sub(r"\s+", " ", text).strip()
    text = re.sub(r"[()\[\]]+$", "", text).strip()
    return text, year

class Library:
    """Verwaltet die Medienbibliothek mit SQLite-Datenbank."""
    
    def __init__(self, db_path: str, movies_dir: str, series_dir: str):
        self.db_path = db_path
        self.movies_dir = Path(movies_dir)
        self.series_dir = Path(series_dir)
        self._scan_lock = threading.Lock()
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Verbindung zur SQLite-Datenbank herstellen."""
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(self.db_path, timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute("PRAGMA journal_mode=WAL")
        return conn

    def _init_db(self):
        """Datenbank-Tabelle initialisieren."""
        with self._connect() as db:
            db.execute("""
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
                    updated_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            
            # Indizes für Performance
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_kind ON media(kind)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_provider ON media(provider)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_title ON media(title)")
            db.execute("CREATE INDEX IF NOT EXISTS idx_media_rating ON media(rating)")

    def _is_valid_provider(self, folder_name: str) -> bool:
        """Prüft ob Ordnername ein gültiger Provider ist."""
        return folder_name.lower() in VALID_PROVIDERS

    def _scan_movies(self, root_dir: Path) -> list[dict[str, Any]]:
        """Scant das movies-Verzeichnis nach Filmen.
        
        Struktur: /movies/PROVIDER/Filmname/video.mkv
        Provider: erster Unterordner (z.B. 'netflix')
        Filmname: zweiter Unterordner
        """
        if not root_dir.exists():
            return []

        items = []
        
        # Durchlaufe alle Provider-Ordner direkt unter root
        for provider_dir in sorted(root_dir.iterdir()):
            if not provider_dir.is_dir():
                continue
            if not self._is_valid_provider(provider_dir.name):
                continue
            
            provider_name = provider_dir.name.lower()
            
            # Durchlaufe alle Film-Ordner unter Provider
            for film_dir in sorted(provider_dir.iterdir()):
                if not film_dir.is_dir():
                    continue
                
                # NFO im Film-Ordner suchen
                nfo = None
                possible_nfos = [
                    film_dir / "movie.nfo",
                ]
                # Auch nach .nfo Dateien im Film-Ordner suchen
                for nfo_candidate in film_dir.glob("*.nfo"):
                    nfo = nfo_candidate
                    break
                
                # Metadaten parsen
                data = parse_nfo(nfo)
                
                # Titel und Jahr extrahieren (aus Filmordner)
                title = data.get("title")
                year = data.get("year")
                if not title:
                    title, year_from_name = parse_name(film_dir.name)
                    if not year:
                        year = year_from_name
                
                # Erste Videodatei als Referenz-Pfad
                first_video = None
                for ext in VIDEO_EXTENSIONS:
                    for video_file in film_dir.glob(f"*{ext}"):
                        first_video = video_file
                        break
                    if first_video:
                        break
                
                if not first_video:
                    # Recursive search
                    for video_file in film_dir.rglob("*"):
                        if video_file.suffix.lower() in VIDEO_EXTENSIONS:
                            first_video = video_file
                            break
                    if not first_video:
                        continue
                
                items.append({
                    "kind": "movie",
                    "provider": provider_name,
                    "title": title or first_video.stem,
                    "year": year,
                    "plot": data.get("plot"),
                    "genres": data.get("genres"),
                    "rating": data.get("rating"),
                    "runtime": data.get("runtime"),
                    "path": str(first_video),
                    "nfo_path": str(nfo) if nfo and nfo.exists() else None,
                })

        return items

    def _scan_series(self, root_dir: Path) -> list[dict[str, Any]]:
        """Scant das tv-Verzeichnis nach Serien.
        
        Struktur: /tv/SERIENNAME/Season 1/episode.mkv
        Serienname: erster Unterordner unter tv (NICHT Provider!)
        
        WICHTIG: Bei deiner Struktur sind Provider-Ordner wie 'netflix'
        NICHT direkt unter tv/, sondern der Serienname ist direkt unter tv/.
        
        Also: /tv/9-1-1/Season 1/episode.mkv -> Serie = "9-1-1"
        
        Falls du Provider-Ordner unter tv/ haben willst (wie bei Movies),
        müsste die Struktur sein: /tv/netflix/Serienname/...
        
        Bitte klären, welche Struktur korrekt ist!
        
        AKTUELLE ANNAHME: Serien sind direkt unter /tv/, ohne Provider-Ordner
        """
        if not root_dir.exists():
            return []

        items = []
        scanned_series = set()
        
        # Durchlaufe alle Serien-Ordner direkt unter root (tv/)
        for series_dir in sorted(root_dir.iterdir()):
            if not series_dir.is_dir():
                continue
            
            # Provider prüfen: Ist dies ein Provider-Ordner?
            # Wenn JA: Struktur ist /tv/PROVIDER/SERIENNAME/...
            if self._is_valid_provider(series_dir.name):
                provider_name = series_dir.name.lower()
                
                # Dann durchlaufe Serien-Ordner unter Provider
                for show_dir in sorted(series_dir.iterdir()):
                    if not show_dir.is_dir():
                        continue
                    
                    series_key = f"{provider_name}:{show_dir.name}"
                    if series_key in scanned_series:
                        continue
                    scanned_series.add(series_key)
                    
                    # NFO im Serien-Ordner suchen (nicht in Season!)
                    nfo = None
                    possible_nfos = [
                        show_dir / "tvshow.nfo",
                    ]
                    for nfo_candidate in show_dir.glob("*.nfo"):
                        nfo = nfo_candidate
                        break
                    
                    # Metadaten parsen
                    data = parse_nfo(nfo)
                    
                    # Serienname und Jahr extrahieren (aus Serienordner)
                    title = data.get("title")
                    year = data.get("year")
                    if not title:
                        title, year_from_name = parse_name(show_dir.name)
                        if not year:
                            year = year_from_name
                    
                    # First video finden (in Season-Ordner oder darunter)
                    first_video = None
                    for season_dir in show_dir.iterdir():
                        if not season_dir.is_dir():
                            continue
                        # Ignoriere nicht-Season Ordner (wie Extras, Behind the Scenes)
                        season_name_lower = season_dir.name.lower()
                        if "season" in season_name_lower or "staffel" in season_name_lower:
                            for video_file in season_dir.glob(f"*{next(iter(VIDEO_EXTENSIONS))}"):
                                first_video = video_file
                                break
                            if first_video:
                                break
                    
                    if not first_video:
                        # Recursive search
                        for video_file in show_dir.rglob("*"):
                            if video_file.suffix.lower() in VIDEO_EXTENSIONS:
                                first_video = video_file
                                break
                    
                    if not first_video:
                        continue
                    
                    items.append({
                        "kind": "series",
                        "provider": provider_name,
                        "title": title or show_dir.name,
                        "year": year,
                        "plot": data.get("plot"),
                        "genres": data.get("genres"),
                        "rating": data.get("rating"),
                        "runtime": data.get("runtime"),
                        "path": str(first_video),
                        "nfo_path": str(nfo) if nfo and nfo.exists() else None,
                    })
            
            else:
                # KEIN Provider-Ordner, direkt Serie
                # Struktur: /tv/SERIENNAME/...
                # Dann Provider = "unknown" oder aus NFO extrahieren
                series_key = series_dir.name
                if series_key in scanned_series:
                    continue
                scanned_series.add(series_key)
                
                # NFO im Serien-Ordner suchen
                nfo = None
                possible_nfos = [
                    series_dir / "tvshow.nfo",
                ]
                for nfo_candidate in series_dir.glob("*.nfo"):
                    nfo = nfo_candidate
                    break
                
                # Metadaten parsen
                data = parse_nfo(nfo)
                
                # Serienname und Jahr extrahieren
                title = data.get("title")
                year = data.get("year")
                if not title:
                    title, year_from_name = parse_name(series_dir.name)
                    if not year:
                        year = year_from_name
                
                # First video finden
                first_video = None
                for season_dir in series_dir.iterdir():
                    if not season_dir.is_dir():
                        continue
                    season_name_lower = season_dir.name.lower()
                    if "season" in season_name_lower or "staffel" in season_name_lower:
                        for video_file in season_dir.glob(f"*{next(iter(VIDEO_EXTENSIONS))}"):
                            first_video = video_file
                            break
                        if first_video:
                            break
                
                if not first_video:
                    for video_file in series_dir.rglob("*"):
                        if video_file.suffix.lower() in VIDEO_EXTENSIONS:
                            first_video = video_file
                            break
                
                if not first_video:
                    continue
                
                items.append({
                    "kind": "series",
                    "provider": "unknown",
                    "title": title or series_dir.name,
                    "year": year,
                    "plot": data.get("plot"),
                    "genres": data.get("genres"),
                    "rating": data.get("rating"),
                    "runtime": data.get("runtime"),
                    "path": str(first_video),
                    "nfo_path": str(nfo) if nfo and nfo.exists() else None,
                })

        return items

    def scan(self) -> int:
        """Scant alle Medienordner und aktualisiert Datenbank."""
        if not self._scan_lock.acquire(blocking=False):
            stats = self.stats()
            return stats["total"]

        try:
            # Beide Hauptordner scannen
            movie_items = self._scan_movies(self.movies_dir)
            series_items = self._scan_series(self.series_dir)
            all_items = movie_items + series_items
            
            with self._connect() as db:
                db.execute("BEGIN")
                
                # Alle Einträge upsertn
                for item in all_items:
                    db.execute("""
                        INSERT OR REPLACE INTO media 
                        (kind, provider, title, year, plot, genres, rating, runtime, 
                         path, nfo_path, updated_at)
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
                
                # Alte Einträge löschen
                existing_paths = {x["path"] for x in all_items}
                
                if existing_paths:
                    placeholders = ",".join("?" * len(existing_paths))
                    db.execute(f"DELETE FROM media WHERE path NOT IN ({placeholders})", 
                              tuple(existing_paths))
                else:
                    db.execute("DELETE FROM media")
                
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
        """Wählt zufälliges Medium basierend auf Filtern."""
        query = "SELECT * FROM media WHERE 1=1"
        params: list[Any] = []

        if kind in {"movie", "series"}:
            query += " AND kind=?"
            params.append(kind)

        if provider and provider.lower() != "alle":
            query += " AND provider=?"
            params.append(provider)

        excluded = [p for p in (exclude_titles or []) if p]
        if excluded:
            query += " AND title NOT IN (" + ",".join("?" * len(excluded)) + ")"
            params.extend(excluded)

        # Zufällig mit leichtem Bias zu höherem Rating
        query += " ORDER BY RANDOM() + (COALESCE(rating, 0) * 0.05) DESC LIMIT 1"

        with self._connect() as db:
            row = db.execute(query, params).fetchone()
            return dict(row) if row else None

    def stats(self) -> dict[str, Any]:
        """Statistiken der Bibliothek."""
        with self._connect() as db:
            avg_rating_result = db.execute(
                "SELECT AVG(rating) FROM media WHERE rating IS NOT NULL"
            ).fetchone()[0]
            
            return {
                "total": db.execute("SELECT COUNT(*) FROM media").fetchone()[0],
                "movies": db.execute("SELECT COUNT(*) FROM media WHERE kind='movie'").fetchone()[0],
                "series": db.execute("SELECT COUNT(*) FROM media WHERE kind='series'").fetchone()[0],
                "providers": [row["provider"] for row in 
                             db.execute("SELECT DISTINCT provider FROM media ORDER BY provider").fetchall()],
                "avg_rating": round(avg_rating_result, 2) if avg_rating_result else None,
            }
