# FIDE Calculations Scraper — Implementierungsplan

## Projektziel

Einen Python-basierten Web-Scraper aufbauen, der für eine Liste von FIDE-Spieler-IDs und
konfigurierbaren Zeiträumen die Einzelpartien (Gegner, Ergebnis, Rating-Änderung) von der
FIDE-Calculations-Seite extrahiert und in einer PostgreSQL/TimescaleDB-Datenbank auf dem
Hostinger VPS speichert. Der Scraper läuft als systemd-Service mit monatlichem Cron-Trigger.

## Analysekontext

Der Scraper dient als Datenbasis für einen **Vergleich von Top-Spielerinnen (ELO ~2500,
Titel WGM/IM/GM) mit männlichen Spielern gleicher Spielstärke**. Konkrete Fragestellungen:

- Unterscheidet sich die Gegnerstruktur? (Spielen Frauen auf diesem Niveau häufiger gegen
  schwächere oder stärkere Gegner als gleichstarke Männer?)
- Gibt es Unterschiede in der Rating-Volatilität (mittlere Änderung pro Periode)?
- Unterscheidet sich die Turnierfrequenz (Anzahl Partien pro Periode)?
- Entwickelt sich das Rating über Zeit anders (Progression/Stagnation/Rückgang)?

Diese Fragestellungen bestimmen die Spielerauswahl und das DB-Schema.

### Spielerauswahl

Zwei Gruppen, je ca. 50–150 Spieler:

- **female_top**: Aktive Spielerinnen mit aktuellem Standard-ELO 2400–2600
  (Beispiele: Ju Wenjun, Hou Yifan, Koneru Humpy, Lei Tingjie, Aleksandra Goryachkina)
- **male_control**: Männliche Spieler mit ELO 2400–2600 als Kontrollgruppe,
  möglichst ähnliche Rating-Verteilung wie female_top

Der initiale Seed erfolgt aus der globalen FIDE-Download-Liste gefiltert nach
`SEX='F'` bzw. `SEX='M'` und `STD BETWEEN 2400 AND 2600`.

---

## Ziel-Architektur

```
fide-scraper/
├── CLAUDE.md               ← diese Datei
├── docker-compose.yml      ← PostgreSQL/TimescaleDB + Scraper-Container
├── .env                    ← Secrets (nicht ins Git)
├── .env.example            ← Template für .env
├── scraper/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py             ← CLI-Einstiegspunkt
│   ├── config.py           ← Konfiguration aus .env
│   ├── fetcher.py          ← HTTP-Requests + Retry-Logik
│   ├── parser.py           ← BeautifulSoup HTML-Parser
│   ├── db.py               ← PostgreSQL-Verbindung + Upserts
│   └── scheduler.py        ← Cron-Loop für automatischen Betrieb
├── migrations/
│   └── 001_initial.sql     ← Schema-Setup
└── scripts/
    ├── seed_players.py     ← Einmalig: FIDE-Download-Liste importieren
    └── backfill.py         ← Historische Daten nachladen
```

---

## Schema-Designentscheidungen

Dokumentiert hier, damit spätere Änderungen nachvollziehbar bleiben.

| Entscheidung | Begründung |
|---|---|
| `scrape_periods.k_factor` | K-Faktor (10/20/40) wird pro Periode auf der FIDE-Seite ausgewiesen. Ohne dieses Feld wäre der Volatilitätsvergleich verzerrt: K=40-Spieler erzielen bei identischen Ergebnissen doppelt so große Änderungen wie K=20-Spieler. |
| `game_results.opponent_federation` | CHAR(3)-Feld, das die FIDE-Föderationscode des Gegners speichert. Optional für die Kernanalyse, nützlich für geografische Auswertungen. NULL wenn nicht parsebar. |
| UNIQUE `(fide_id, period, opponent_name, opponent_rating, result)` | `opponent_rating` statt nur `result` als Diskriminator, um Kollisionen bei Remis-Serien gegen denselben Gegner zu vermeiden. Bekannte Schwäche: zwei Partien mit identischem Gegner, Rating und Ergebnis werden dedupliziert — in der Praxis sehr selten. |
| End-Rating nicht gespeichert | Das End-Rating einer Periode ergibt sich aus `rating_history.std_rating + SUM(game_results.rating_change)` und wird nicht redundant gespeichert. |

---

## Phase 1 — Datenbankschema

### Task 1.1 — migrations/001_initial.sql erstellen

**Status: Datei erstellt** (`migrations/001_initial.sql`)

Erstelle folgende Tabellen:

**players** — bekannte Spieler-IDs inkl. Analyse-Gruppe:
```sql
CREATE TABLE IF NOT EXISTS players (
    fide_id         INTEGER PRIMARY KEY,
    name            TEXT,
    federation      TEXT,
    title           TEXT,
    sex             CHAR(1),            -- 'M' | 'F'
    birth_year      INTEGER,
    std_rating      INTEGER,
    analysis_group  TEXT,               -- 'female_top' | 'male_control'
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
```

`analysis_group` unterscheidet die zwei Vergleichsgruppen:
- `female_top` — Spielerinnen mit ELO 2400–2600
- `male_control` — Männer mit ELO 2400–2600 als Kontrollgruppe

**scrape_periods** — welche (player, period)-Kombinationen bereits gescraped wurden:
```sql
CREATE TABLE IF NOT EXISTS scrape_periods (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,      -- immer erster des Monats, z.B. 2025-10-01
    scraped_at  TIMESTAMPTZ DEFAULT NOW(),
    status      TEXT DEFAULT 'ok', -- 'ok' | 'no_data' | 'error'
    k_factor    INTEGER,           -- 10 | 20 | 40; NULL wenn nicht parsebar
    PRIMARY KEY (fide_id, period)
);
```

**game_results** — Einzelpartien aus den Calculations-Seiten:
```sql
CREATE TABLE IF NOT EXISTS game_results (
    id                  BIGSERIAL PRIMARY KEY,
    fide_id             INTEGER NOT NULL REFERENCES players(fide_id),
    period              DATE NOT NULL,
    opponent_name       TEXT,
    opponent_fide_id    INTEGER,
    opponent_federation CHAR(3),        -- z.B. 'GER', 'CHN'; NULL wenn nicht parsebar
    opponent_rating     INTEGER,
    result              TEXT,           -- '1' | '0.5' | '0'
    rating_change       NUMERIC(5,1),
    tournament_name     TEXT,
    created_at          TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fide_id, period, opponent_name, opponent_rating, result)
);
CREATE INDEX ON game_results (fide_id, period);
CREATE INDEX ON game_results (period);
```

**rating_history** — monatliches Rating pro Spieler (aus FIDE-Download-Liste):
```sql
CREATE TABLE IF NOT EXISTS rating_history (
    fide_id     INTEGER NOT NULL REFERENCES players(fide_id),
    period      DATE NOT NULL,
    std_rating  INTEGER,            -- publiziertes Rating zu Periodenbeginn
    num_games   INTEGER,
    PRIMARY KEY (fide_id, period)
);
```

Nach der Erstellung die Migration ausführen:
```bash
docker compose exec db psql -U fide -d fidedb -f /migrations/001_initial.sql
```

---

## Phase 2 — Scraper-Logik

### Task 2.1 — scraper/fetcher.py

Implementiere eine Funktion `fetch_calculations(fide_id, period_str)`:

- `period_str` Format: `"YYYY-MM-01"` (z.B. `"2025-10-01"`)
- URL: `https://ratings.fide.com/calculations.phtml?id_number={fide_id}&period={period_str}&rating=0`
- HTTP GET mit `requests`, Timeout 15s
- User-Agent Header setzen: `"Mozilla/5.0 (compatible; research-scraper/1.0)"`
- Retry-Logik: bei Status 429 oder 5xx maximal 3 Versuche mit exponentiellem Backoff
  (1s, 4s, 16s), danach Exception
- Zwischen normalen Requests: `time.sleep(random.uniform(1.2, 2.5))`
- Gibt `response.text` (HTML-String) zurück oder wirft Exception

### Task 2.2 — scraper/parser.py

Implementiere `parse_calculations(html, fide_id, period_str)`:

Die FIDE-Calculations-Seite enthält eine HTML-Tabelle mit diesen Spalten (Stand 2024/2025):
- Opponent name (mit Link zu Profil, enthält FIDE-ID als href-Parameter)
- Opponent FIDE ID (aus href extrahieren: `/profile/12345678`)
- Opponent federation (3-stelliger Code, z.B. `GER`)
- Result (W/D/L oder 1/0.5/0)
- Opponent rating
- Rating change (+/-)
- Tournament name (kann über mehrere Zeilen gehen / als Gruppen-Header erscheinen)

Zusätzlich im Perioden-Header sichtbar:
- K-Faktor des Spielers (10 / 20 / 40)

Parser-Anforderungen:
- BeautifulSoup4 mit `html.parser`
- Zuerst alle `<table>`-Elemente inspizieren und die richtige Tabelle identifizieren
  (sie enthält typischerweise "Opponent", "Result", "Rtg" als Header)
- K-Faktor aus dem Perioden-Header extrahieren und als eigenen Rückgabewert liefern
- Robustes Parsen: fehlende Spalten mit `None` auffüllen, nicht abbrechen
- Numeric-Parsing: `int()` und `float()` mit try/except wrappen
- FIDE-ID des Gegners: aus `href="/profile/{id}"` extrahieren via Regex `r'/profile/(\d+)'`
- Result normalisieren: `"1"` → `"1"`, `"="` oder `"½"` oder `"0.5"` → `"0.5"`, `"0"` → `"0"`
- Gibt Tupel `(games, k_factor)` zurück:
  - `games`: Liste von Dicts mit Keys:
    `fide_id, period, opponent_name, opponent_fide_id, opponent_federation,
     opponent_rating, result, rating_change, tournament_name`
  - `k_factor`: Integer (10/20/40) oder `None`
- Bei leerer Tabelle oder keinem Match: `([], None)` zurückgeben

**WICHTIG**: Nach dem ersten Deployment die Parser-Ausgabe für eine bekannte Calculations-Seite
loggen und manuell gegen die Web-Ansicht prüfen. Das HTML-Layout von FIDE kann sich ändern.

### Task 2.3 — scraper/db.py

Implementiere mit `psycopg2`:

```python
def get_connection():
    # Connection aus config.py (DATABASE_URL)

def upsert_games(conn, games: list[dict]):
    # INSERT INTO game_results ... ON CONFLICT DO NOTHING

def mark_period_scraped(conn, fide_id, period, status='ok', k_factor=None):
    # INSERT INTO scrape_periods ... ON CONFLICT DO UPDATE SET scraped_at=NOW(), status=..., k_factor=...

def get_pending_periods(conn, periods: list[str], fide_ids: list[int] = None):
    # Gibt (fide_id, period)-Paare zurück, die noch NICHT in scrape_periods stehen
    # Falls fide_ids=None: alle aktiven Spieler aus players-Tabelle nehmen
```

### Task 2.4 — scraper/main.py

CLI mit `argparse`:

```
python main.py run --periods 2025-01-01 2025-04-01 2025-07-01 2025-10-01
python main.py run --periods 2025-10-01 --fide-ids 24171760 12345678
python main.py run --latest          # nur letzten abgeschlossenen Monat
python main.py status                # zeigt Scrape-Fortschritt aus DB
```

Ablauf für `run`:
1. `get_pending_periods()` aufrufen — bereits gescrapete Kombinationen überspringen
2. Für jedes Paar: `fetch_calculations()` → `parse_calculations()` → `upsert_games()` → `mark_period_scraped()`
3. Fehler loggen (nicht abbrechen), Status `'error'` in `scrape_periods` schreiben
4. Fortschrittsanzeige: `logging.info(f"[{i}/{total}] fide_id={fide_id} period={period} games={len(games)}")`

---

## Phase 3 — Spielerliste

### Task 3.1 — scripts/seed_players.py

Einmaliger Import aus der globalen FIDE-Download-Liste:

- Download-URL: `https://ratings.fide.com/download_lists.phtml` (TXT-Format)
- Aktuellen Monats-Link herausfinden und die TXT-Datei parsen
- Format der TXT-Datei (fixed-width): `ID_NUMBER, NAME, FED, B-day, SEX, TIT, STD, RPD, BLZ, K, ...`
- Immer globale Liste verwenden (keine Federation-Filterung), da die Analyse international ist
- Filter-Optionen als CLI-Args:
  - `--group female_top` → filtert `SEX='F'` und `STD BETWEEN 2400 AND 2600`
  - `--group male_control` → filtert `SEX='M'` und `STD BETWEEN 2400 AND 2600`
  - `--min-rating` / `--max-rating` für manuelle Anpassung der Grenzen
- `analysis_group`-Spalte entsprechend belegen
- Resultate in `players`-Tabelle schreiben (UPSERT)

Beispielaufrufe:
```bash
python seed_players.py --group female_top
python seed_players.py --group male_control --max-rating 2550
```

---

## Phase 4 — Docker & Deployment

### Task 4.1 — scraper/Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py", "run", "--latest"]
```

### Task 4.2 — docker-compose.yml

Ergänze (oder erstelle) eine `docker-compose.yml` mit zwei Services:

**db** (falls noch keine TimescaleDB läuft):
```yaml
db:
  image: timescale/timescaledb:latest-pg16
  environment:
    POSTGRES_DB: fidedb
    POSTGRES_USER: fide
    POSTGRES_PASSWORD: ${DB_PASSWORD}
  volumes:
    - fide_pgdata:/var/lib/postgresql/data
    - ./migrations:/migrations
  ports:
    - "127.0.0.1:5433:5432"   # Port 5433 um Konflikt mit tunnelbliq-DB zu vermeiden
```

**scraper**:
```yaml
scraper:
  build: ./scraper
  environment:
    DATABASE_URL: postgresql://fide:${DB_PASSWORD}@db:5432/fidedb
  depends_on:
    - db
  profiles: ["manual"]   # wird nicht automatisch mit `docker compose up` gestartet
```

Falls bereits eine tunnelbliq TimescaleDB läuft: stattdessen eine neue Datenbank `fidedb`
in der bestehenden Instanz anlegen und den `db`-Service weglassen.

### Task 4.3 — .env.example

```
DB_PASSWORD=changeme
DATABASE_URL=postgresql://fide:changeme@db:5432/fidedb
LOG_LEVEL=INFO
```

### Task 4.4 — Cron-Setup auf VPS

Monatlichen Cron-Job anlegen, der den Scraper automatisch triggert:

```bash
# crontab -e
5 6 3 * * cd /opt/fide-scraper && docker compose run --rm scraper python main.py run --latest >> /var/log/fide-scraper.log 2>&1
```

Am 3. jeden Monats um 6:05 Uhr — FIDE publiziert die neue Liste am 1., braucht manchmal 1–2 Tage.

---

## Phase 5 — Historische Daten (Backfill)

### Task 5.1 — scripts/backfill.py

Skript zum Nachladen vergangener Perioden:

```bash
python backfill.py --from 2022-01-01 --to 2025-10-01
```

- Generiert alle Monats-Perioden zwischen `--from` und `--to`
- Nutzt fetcher/parser/db direkt (kein Subprocess)
- Höflicheres Rate-Limiting beim Backfill: `sleep(random.uniform(2.0, 4.0))`
- Checkpoint-Mechanismus: bereits abgehakte Perioden (Status `'ok'`) werden übersprungen
- Empfehlung: in einer `tmux`-Session auf dem VPS laufen lassen

Erwartetes Datenvolumen beim initialen Backfill (3 Jahre, ~100 Spielerinnen + ~100 Männer):
ca. 7200 Requests → bei 3s Ø ca. 6 Stunden.

---

## Reihenfolge der Implementierung

1. `migrations/001_initial.sql` erstellen + DB-Verbindung testen
2. `fetcher.py` implementieren + eine URL manuell testen (Browser-Vergleich)
3. `parser.py` implementieren + Ausgabe manuell gegen FIDE-Website prüfen ← **kritischer Schritt**
4. `db.py` Upserts implementieren und testen
5. `main.py` end-to-end testen: 2 Spieler-IDs (1 weiblich, 1 männlich), 1 Periode
6. `seed_players.py` ausführen: erst `--group female_top`, dann `--group male_control`
7. Docker-Setup finalisieren
8. Cron konfigurieren
9. Backfill für gewünschten Zeitraum in `tmux` starten

---

## Bekannte Risiken

| Risiko | Massnahme |
|---|---|
| FIDE ändert HTML-Layout | Parser loggt rohe HTML bei Parse-Fehlern; Cron-Fehler erzeugt Alert |
| IP-Sperre bei zu vielen Requests | Sleep-Jitter + Backoff; max. ~500 Requests/Tag |
| Leere Calculations-Seite | Status `'no_data'` → wird nicht erneut versucht |
| DB-Konflikt mit tunnelbliq | Eigene DB `fidedb`, eigener Port 5433 |
| Verzerrung Kontrollgruppe | male_control Rating-Range eng halten (z.B. ±50 ELO zum female_top-Median) |

---

## Abhängigkeiten (requirements.txt)

```
requests==2.32.3
beautifulsoup4==4.12.3
psycopg2-binary==2.9.10
python-dotenv==1.0.1
```
