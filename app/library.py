from __future__ import annotations

import re
import sqlite3
import threading
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

VIDEO_EXTENSIONS = {".mkv", ".mp4", ".m4v", ".avi", ".mov", ".wmv", ".webm", ".ts", ".m2ts"}

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
    """Parst NFO-Datei und extrahiert Metadaten inkl. Poster."""
    if not path:
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
    
    # TMDB-Rating: Wenn > 5, dann schon 10er Skala, sonst verdoppeln
    rating = data.get("rating")
    if isinstance(rating, str):
        try:
            rating = float(rating)
        except ValueError:
            rating = None
    if rating and 0 < rating <= 5:
        rating = rating * 2
    data["rating"] = rating
    
    # Laufzeit bereinigen (immer in Minuten)
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

def determine_series_title_from_path(series_dir: Path, file_entry: Path) -> tuple[str, int | None]:
    """Extrahiert Seriennamen aus Pfadstruktur: /tv/provider/Serienname/..."""
    # Pfad: /tv/netflix/Serie-Y/episode.mkv
    # Wir wollen: "Serie-Y"
    try:
        # Relativer Pfad vom series_dir
        relative = file_entry.relative_to(series_dir)
        parts = relative.parts
        
        # Teile: ['provider', 'Serienname', 'episode.mkv']
        # Serienname ist zweite Komponente (Index 1)
        if len(parts) >= 2:
            series_name = parts[1]
        elif len(parts) == 1:
            series_name = parts[0]
        else:
            series_name = file_entry.stem
        
        # Parse Titel und Jahr aus Seriennamen
        title, year = parse_name(series_name)
        return title, year
    except ValueError:
        return file_entry.stem, None

def parse_name(name: str) -> tuple[str, int | None]:
    """Extrahiert Titel und Jahr aus Dateinamen oder Ordnername."""
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
                    poster TEXT,
                    local_poster TEXT,
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

    def _scan_movies(self, root_dir: Path) -> list[dict[str, Any]]:
        """Scant das movies-Verzeichnis nach Filmen."""
        if not root_dir.exists():
            return []

        items = []
        
        # Rekursiv alle Video-Dateien finden
        for entry in sorted(root_dir.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            
            # Provider ermitteln (nächstes Eltern-Verzeichnis unter root)
            # Pfad: /movies/provider/Filmname/video.mkv
            relative = entry.relative_to(root_dir)
            parts = relative.parts
            
            # Provider = erste Komponente
            provider_name = parts[0] if len(parts) >= 1 else "unknown"
            
            # NFO-Datei suchen (im Filmordner)
            nfo = None
            possible_nfos = [
                entry.parent / "movie.nfo",
                entry.parent / f"{entry.stem}.nfo",
            ]
            for candidate in possible_nfos:
                if candidate.exists():
                    nfo = candidate
                    break
            
            # Metadaten parsen
            data = parse_nfo(nfo)
            
            # Titel und Jahr extrahieren
            title = data.get("title")
            year = data.get("year")
            if not title:
                # Titel aus Pfad extrahieren: /movies/provider/Filmname/video.mkv
                if len(parts) >= 3:
                    film_name = parts[2]
                else:
                    film_name = entry.stem
                title, year_from_name = parse_name(film_name)
                if not year:
                    year = year_from_name
            
            items.append({
                "kind": "movie",
                "provider": provider_name,
                "title": title or entry.name,
                "year": year,
                "plot": data.get("plot"),
                "genres": data.get("genres"),
                "rating": data.get("rating"),
                "runtime": data.get("runtime"),
                "path": str(entry),
                "nfo_path": str(nfo) if nfo and nfo.exists() else None,
            })

        return items

    def _scan_series(self, root_dir: Path) -> list[dict[str, Any]]:
        """Scant das tv-Verzeichnis nach Serien (auf Serien-Ebene, nicht Episoden)."""
        if not root_dir.exists():
            return []

        items = []
        
        # Rekursiv nach Serienordnern suchen (nicht einzelnen Episoden)
        # Struktur: /tv/provider/Serienname/episode1.mkv
        # Wir wollen jeden Serienordner nur EINMAL scannen
        
        scanned_series = set()
        
        for entry in sorted(root_dir.rglob("*")):
            if not entry.is_file():
                continue
            if entry.suffix.lower() not in VIDEO_EXTENSIONS:
                continue
            
            # Pfad: /tv/provider/Serienname/episode.mkv
            relative = entry.relative_to(root_dir)
            parts = relative.parts
            
            if len(parts) < 3:
                continue
            
            provider_name = parts[0]
            series_name_folder = parts[1]  # Serienordner
            
            # Serien-Eindeutigkeit vermeiden (gleiche Serie nicht mehrfach)
            series_key = f"{provider_name}:{series_name_folder}"
            if series_key in scanned_series:
                continue
            scanned_series.add(series_key)
            
            # NFO-Datei im Serienordner suchen
            nfo = None
            possible_nfos = [
                entry.parent / "tvshow.nfo",
                entry.parent / "season.nfo",
            ]
            for candidate in possible_nfos:
                if candidate.exists():
                    nfo = candidate
                    break
            
            # Falls kein NFO im aktuellen Ordner, im Elternordner suchen
            if not nfo:
                parent = Path(str(root_dir) + "/" + "/".join(parts[:2]))
                possible_nfos_parent = [
                    parent / "tvshow.nfo",
                ]
                for candidate in possible_nfos_parent:
                    if candidate.exists():
                        nfo = candidate
                        break
            
            # Metadaten parsen
            data = parse_nfo(nfo)
            
            # Serienname und Jahr extrahieren (aus Ordnername, nicht Dateiname!)
            title = data.get("title")
            year = data.get("year")
            if not title:
                title, year_from_name = determine_series_title_from_path(root_dir, entry)
                if not year:
                    year = year_from_name
            
            items.append({
                "kind": "series",
                "provider": provider_name,
                "title": title or series_name_folder,
                "year": year,
                "plot": data.get("plot"),
                "genres": data.get("genres"),
                "rating": data.get("rating"),
                "runtime": data.get("runtime"),
                "path": str(entry),  # Erste Video-Datei als Referenz
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
