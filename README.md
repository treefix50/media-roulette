# Media Roulette

Lokaler Zufalls- und Empfehlungsdienst für eine Unraid-Medienbibliothek.

## Unraid

Die Anwendung verwendet standardmäßig `/mnt/data/movies` und `/mnt/data/series`. Die direkten Unterordner darunter werden automatisch als Quellen/Anbieter erkannt. Es gibt keine fest codierten Anbieter wie Netflix, Disney oder Prime.

Der Medienbaum wird read-only eingebunden. Die SQLite-Datenbank liegt getrennt unter `/state`.

### Docker Compose

```yaml
services:
  media-roulette:
    build: .
    container_name: media-roulette
    ports:
      - "8000:8000"
    environment:
      MOVIES_DIR: /data/movies
      SERIES_DIR: /data/series
      DATABASE_PATH: /state/media-roulette.db
    volumes:
      - /mnt/data:/data:ro
      - ./state:/state
    restart: unless-stopped
```

Danach ist die Oberfläche unter `http://UNRAID-IP:8000` erreichbar.

## Metadaten

Wenn eine `.nfo` vorhanden ist, werden unter anderem Titel, Jahr, Beschreibung, Genres, Bewertung und Laufzeit daraus gelesen. Für Filme wird bevorzugt `movie.nfo`, für Serien `tvshow.nfo` verwendet. Fehlt eine NFO, wird auf Ordnernamen und vorhandene Videodateien zurückgegriffen.

Ein Scan passiert beim Containerstart. Über **Bibliothek aktualisieren** kann jederzeit erneut gescannt werden.
