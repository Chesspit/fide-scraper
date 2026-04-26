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

Zwei Gruppen:

- **female_top**: Alle aktiven Spielerinnen mit aktuellem Standard-ELO 2400–2600
  (Beispiele: Ju Wenjun, Hou Yifan, Koneru Humpy, Lei Tingjie, Aleksandra Goryachkina)
  Datenstand April 2026: **64 Spielerinnen** — vollständige Population, kein Sampling nötig.
- **male_control**: ursprünglich 130 männliche Spieler mit ELO 2400–2600 als Kontrollgruppe,
  age-matched zur Frauen-Gruppe (siehe Sampling-Strategie unten). In zwei Schritten erweitert:
  +150 Spieler (seed 43, April 2026) und +199 Spieler (seed 44, April 2026) →
  **479 Spieler** insgesamt (435 aktiv, 44 inaktiv). Pool: **2.685 Männer** in dieser Range.
- **elite_2600**: Zusatzgruppe aller Spieler mit `std_rating ≥ 2600` — **202 Spieler**
  (153 aktiv, 49 inaktiv). Dient als „obere Vergleichsschicht" für Analysen, in denen
  female_top-Spielerinnen gegen stärkere Gegner antreten. Backfill 2010-01 → 2026-03
  abgeschlossen am 2026-04-24.
- **swiss_2026**: Spieler der Schweizer Mannschaftsmeisterschaft 2026 (NLA + NLB, erste
  20 Teams) — **349 Spieler** exklusiv (338 aktiv, 11 inaktiv; 13 weitere Spieler
  überschneiden sich mit anderen Gruppen). Implementiert als Boolean-Flag `swiss_2026`
  in `players`, nicht als `analysis_group`-Wert. Backfill 2010-01 → 2026-03
  abgeschlossen am 2026-04-23.

Der initiale Seed erfolgt aus der globalen FIDE-Download-Liste (`players_list_foa.txt`),
gefiltert nach `SEX='F'` bzw. `SEX='M'` und `STD BETWEEN 2400 AND 2600`.

### Sampling-Strategie für male_control

Die Kontrollgruppe soll die Altersverteilung der female_top-Gruppe widerspiegeln,
damit altersbedingte Effekte (Karrierephase, K-Faktor-Unterschiede) nicht den
Geschlechtervergleich verzerren.

**Altersverteilung female_top (Geburtsjahr-Dekaden):**

| Dekade | Frauen | Anteil |
|--------|--------|--------|
| 1950er | 1      | 1.6%   |
| 1960er | 3      | 4.7%   |
| 1970er | 8      | 12.5%  |
| 1980er | 19     | 29.7%  |
| 1990er | 17     | 26.6%  |
| 2000er | 15     | 23.4%  |
| 2010er | 1      | 1.6%   |

**Algorithmus:**
1. Alle 64 Frauen in `female_top` aufnehmen (vollständige Population)
2. Geburtsjahr-Verteilung der Frauen berechnen (Anteil pro Dekade)
3. 130 Männer-Slots proportional auf Dekaden verteilen (gerundet, Summe = 130):
   - 1950er: 2, 1960er: 6, 1970er: 16, 1980er: 39, 1990er: 35, 2000er: 30, 2010er: 2
4. Pro Dekade: zufällige Stichprobe aus verfügbaren Männern (`random.sample`)
5. Falls eine Dekade zu wenige Männer hat: alle nehmen, Rest auf nächstliegende Dekade verteilen
6. Seed für Reproduzierbarkeit: `random.seed(42)` (in Config überschreibbar)

---

## Ziel-Architektur

```
fide-scraper/
├── CLAUDE.md               ← diese Datei
├── config.yaml             ← zentrale Konfiguration (Gruppen, Perioden, Scraper-Settings)
├── docker-compose.yml      ← PostgreSQL/TimescaleDB + Scraper-Container
├── .env                    ← Secrets (nicht ins Git)
├── .env.example            ← Template für .env
├── scraper/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── main.py             ← CLI-Einstiegspunkt (liest config.yaml)
│   ├── config.py           ← Konfiguration aus config.yaml + .env
│   ├── fetcher.py          ← HTTP-Requests + Retry-Logik
│   ├── parser.py           ← BeautifulSoup HTML-Parser
│   ├── db.py               ← PostgreSQL-Verbindung + Upserts
│   └── scheduler.py        ← Cron-Loop für automatischen Betrieb
├── migrations/
│   ├── 001_initial.sql     ← Schema-Setup
│   └── 002_analysis_views.sql ← SQL-Views für Analyse
├── notebooks/
│   ├── 01_opponent_structure.ipynb
│   ├── 02_rating_volatility.ipynb
│   ├── 03_tournament_frequency.ipynb
│   ├── 04_rating_progression.ipynb
│   ├── 05_rating_change_sums.ipynb     ← Σ rating_change_weighted (Jahr/Gesamt, Splits)
│   ├── 06_age_cohorts.ipynb            ← Alters-Kohorten (Anker 2015) + Spieler-Tabelle
│   └── 07_peer_performance.ipynb       ← female_top: Partien + Elo-Erfolg nach Kohorte × Stärke × Gegner-Geschlecht
├── tests/
│   ├── fixtures/           ← gespeicherte HTML-Responses für Parser-Tests
│   ├── test_parser.py
│   ├── test_db.py
│   ├── test_sampling.py
│   └── test_fetcher.py
├── data/
│   ├── players_list_foa_2026-04.txt   ← FIDE-Download April 2026 (aktuell)
│   └── players_list_foa_YYYY-MM.txt   ← historische Snapshots zur Validierung
└── scripts/
    ├── seed_players.py             ← FIDE-Liste importieren + Sampling + Lookup-Tabelle
    ├── extend_male_control.py      ← Kontrollgruppe um +N age-matched Spieler erweitern
    ├── import_rating_snapshots.py  ← historische TXT-Dateien → rating_history
    ├── resolve_opponents.py        ← Gegner-FIDE-IDs per Name+Fed+Rating nachschlagen
    ├── backfill.py                 ← Historische Daten nachladen
    └── tunnel.sh                   ← SSH-Tunnel localhost:5434 → VPS DB für Notebooks
```

---

## Schema-Designentscheidungen

Dokumentiert hier, damit spätere Änderungen nachvollziehbar bleiben.

| Entscheidung | Begründung |
|---|---|
| `scrape_periods.k_factor` | K-Faktor (10/20/40) wird pro Partie in der Calculations-Tabelle ausgewiesen (Spalte 8). Ohne dieses Feld wäre der Volatilitätsvergleich verzerrt: K=40-Spieler erzielen bei identischen Ergebnissen doppelt so große Änderungen wie K=20-Spieler. |
| `game_results.game_index` | Laufende Nummer der Partie innerhalb einer (fide_id, period)-Kombination. Löst das Duplikat-Problem: Zwei Partien gegen denselben Gegner mit identischem Rating und Ergebnis (z.B. Doppelrunden in Mannschaftskämpfen) erhalten verschiedene Indizes. |
| UNIQUE `(fide_id, period, game_index)` | Robuster als der alte Constraint über opponent_name/rating/result. game_index wird vom Parser als laufende Nummer vergeben (0, 1, 2, ...). |
| `game_results.color` | Spielerfarbe (W/B) aus CSS-Klasse `white_note`/`black_note` im HTML. Nützlich für Analysen zur Farb-Verteilung. |
| `game_results.rating_change` vs `rating_change_weighted` | FIDE zeigt beide: ungewichtet (chg) und gewichtet (K*chg). Beide speichern, um Analysen mit und ohne K-Faktor-Normalisierung zu ermöglichen. |
| `opponent_fide_id` per Lookup | Die AJAX-Response enthält keine Gegner-FIDE-IDs. Stattdessen Lookup über `opponent_name + opponent_federation + opponent_rating` gegen die `players`-Tabelle (alle Spieler aus TXT-Datei). Eindeutigkeitsanalyse: ab Rating 2000+ nur 278 Duplikat-Paare bei Name+Fed — sehr hohe Trefferquote. Wird nachträglich per `resolve_opponents.py` befüllt, nicht vom Parser. |
| `players`-Tabelle als Lookup | Enthält nicht nur die 194 Analyse-Spieler, sondern alle ~1,8 Mio Spieler aus der FIDE-Liste (mit `analysis_group = NULL` für Nicht-Analyse-Spieler). Dient als Lookup für Gegner-Auflösung und als Referenz für Name ↔ ID Zuordnung. |
| `rating_history`: Primärquelle Calculations | Die Summary-Zeile jeder Turnier-Tabelle enthält `Ro` (eigenes Rating zu Periodenbeginn). Der Parser extrahiert diesen Wert, `db.py` schreibt ihn in `rating_history`. |
| `rating_history`: Validierung via TXT-Snapshots | Historische FIDE-Download-Dateien (`data/players_list_foa_YYYY-MM.txt`) werden per `import_rating_snapshots.py` importiert. Dient als Kontrollwert: `rating_history.published_rating` (aus TXT) vs. `rating_history.std_rating` (aus Calculations). Abweichungen deuten auf Parser-Fehler oder FIDE-Korrekturen hin. |
| End-Rating nicht gespeichert | Das End-Rating einer Periode ergibt sich aus `rating_history.std_rating + SUM(game_results.rating_change_weighted)` und wird nicht redundant gespeichert. |

---

## Phase 1 — Konfiguration (config.yaml)

_Siehe unten: Task 1.1 und Task 1.2_

---

## Phase 2 — Datenbankschema

### Task 2.1 — migrations/001_initial.sql erstellen

**Status: Datei erstellt** (`migrations/001_initial.sql`) — muss aktualisiert werden (neue Felder).

Erstelle folgende Tabellen:

**players** — alle Spieler aus der FIDE-Download-Liste (Lookup + Analyse):
```sql
CREATE TABLE IF NOT EXISTS players (
    fide_id         INTEGER PRIMARY KEY,
    name            TEXT,
    federation      CHAR(3),
    title           TEXT,               -- GM, IM, FM, CM oder NULL
    women_title     TEXT,               -- WGM, WIM, WFM oder NULL
    sex             CHAR(1),            -- 'M' | 'F'
    birth_year      INTEGER,
    std_rating      INTEGER,            -- letztes bekanntes Standard-Rating
    analysis_group  TEXT,               -- 'female_top' | 'male_control' | NULL
    active          BOOLEAN DEFAULT TRUE,
    created_at      TIMESTAMPTZ DEFAULT NOW(),
    updated_at      TIMESTAMPTZ DEFAULT NOW()
);
CREATE INDEX ON players (name, federation);  -- für Gegner-Lookup
CREATE INDEX ON players (analysis_group) WHERE analysis_group IS NOT NULL;
```

`analysis_group` unterscheidet die Gruppen:
- `female_top` — Spielerinnen mit ELO 2400–2600 (64 Spielerinnen)
- `male_control` — Männer mit ELO 2400–2600, age-matched (479 Spieler nach Erweiterungen)
- `elite_2600` — alle Spieler mit ELO ≥ 2600 (202 Spieler, obere Vergleichsschicht)
- `NULL` — alle übrigen Spieler (dienen als Lookup für Gegner-Auflösung)

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
    id                      BIGSERIAL PRIMARY KEY,
    fide_id                 INTEGER NOT NULL REFERENCES players(fide_id),
    period                  DATE NOT NULL,
    opponent_name           TEXT,
    opponent_fide_id        INTEGER,        -- per Lookup aufgelöst, initial NULL
    opponent_title          TEXT,           -- 'f','m','g','c' (FM/IM/GM/CM) oder NULL
    opponent_women_title    TEXT,           -- 'wf','wm','wg' (WFM/WIM/WGM) oder NULL
    opponent_rating         INTEGER,
    opponent_federation     CHAR(3),        -- z.B. 'GER', 'CHN'; NULL wenn nicht parsebar
    result                  TEXT,           -- '1' | '0.5' | '0'
    rating_change           NUMERIC(5,2),   -- ungewichtete Änderung
    rating_change_weighted  NUMERIC(5,2),   -- K * rating_change
    color                   CHAR(1),        -- 'W' | 'B' (Weiss/Schwarz)
    tournament_name         TEXT,
    tournament_location     TEXT,           -- z.B. 'Moscow RUS'
    tournament_start_date   DATE,
    tournament_end_date     DATE,
    game_index              INTEGER,        -- laufende Nummer innerhalb (fide_id, period)
    created_at              TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE (fide_id, period, game_index)
);
CREATE INDEX ON game_results (fide_id, period);
CREATE INDEX ON game_results (period);
```

**rating_history** — monatliches Rating pro Spieler:
```sql
CREATE TABLE IF NOT EXISTS rating_history (
    fide_id           INTEGER NOT NULL REFERENCES players(fide_id),
    period            DATE NOT NULL,
    std_rating        INTEGER,        -- Rating aus Calculations (Ro aus Summary-Zeile)
    published_rating  INTEGER,        -- Rating aus FIDE-TXT-Datei (Validierung)
    num_games         INTEGER,
    PRIMARY KEY (fide_id, period)
);
```

`std_rating` wird vom Scraper befüllt (Primärquelle).
`published_rating` wird per `import_rating_snapshots.py` aus historischen TXT-Dateien importiert.
Abweichungen zwischen beiden Werten deuten auf Parser-Fehler oder FIDE-Korrekturen hin.

Nach der Erstellung die Migration ausführen:
```bash
docker compose exec db psql -U fide -d fidedb -f /migrations/001_initial.sql
```

---

## Phase 3 — Scraper-Logik

### Task 3.1 — scraper/fetcher.py

Implementiere eine Funktion `fetch_calculations(fide_id, period_str)`:

- `period_str` Format: `"YYYY-MM-01"` (z.B. `"2025-04-01"`)
- **WICHTIG**: Die Calculations-Daten werden per AJAX geladen, nicht von der `.phtml`-Seite.
  Die korrekte Daten-URL ist:
  `https://ratings.fide.com/a_indv_calculations.php?id_number={fide_id}&rating_period={period_str}&t=0`
  (Parameter: `rating_period`, nicht `period`; `t=0` für Standard-Rating)
- HTTP GET mit `requests`, Timeout 15s
- Headers:
  - `User-Agent`: Browser-ähnlich (z.B. `"Mozilla/5.0 (Macintosh; ...)"`)
  - `Referer`: `https://ratings.fide.com/calculations.phtml?id_number={fide_id}&period={period_str}&rating=0`
  - `X-Requested-With`: `XMLHttpRequest`
- Retry-Logik: bei Status 429 oder 5xx maximal 3 Versuche mit exponentiellem Backoff
  (1s, 4s, 16s), danach Exception
- Zwischen normalen Requests: `time.sleep(random.uniform(1.2, 2.5))`
- Gibt `response.text` (HTML-Fragment) zurück oder wirft Exception

### Task 3.2 — scraper/parser.py

Implementiere `parse_calculations(html, fide_id, period_str)`:

Das HTML-Fragment (aus der AJAX-Response) hat folgende Struktur (verifiziert April 2025):

**Perioden-Header** (einmalig am Anfang):
```html
<div class="rtng_line01">...<strong>Standard Ratings April 2025 </strong></div>
<div class="rtng_line02">Total change:&nbsp;<b>-8.40</b></div>
```

**Pro Turnier** wiederholt sich dieses Muster:
```html
<!-- Turnier-Header -->
<div class="rtng_line01"><a href=/report.phtml?event=406623&t=0>Aeroflot Open 2025</a></div>
<div class="rtng_line02"><strong>Moscow RUS</strong>
  <span class="dates_span">2025-03-01</span> <span class="dates_span">2025-03-06</span></div>

<!-- Turnier-Tabelle (class="calc_table") -->
<table class="calc_table">
  <!-- Header-Zeile (bgcolor=#b7b7b7): Rc | Ro | | | | w | n | chg | K | K*chg -->
  <!-- Summary-Zeile (bgcolor=#e6e6e6): Avg-Rating-Gegner | Eigenes-Rating | ... -->
  <!-- Spacer-Zeile (height=5) -->
  <!-- Partiezeilen (bgcolor=#efefef, class=list4): -->
</table>
```

**Spalten pro Partiezeile** (10 `<td>`-Elemente, alle mit `class=list4`):

| Index | Inhalt | Beispiel | Hinweise |
|-------|--------|----------|----------|
| 0 | Gegner-Name + Spielerfarbe | `<span class="black_note"> </span> Novozhilov, Semen` | `black_note` = Spieler hatte Schwarz, `white_note` = Weiss |
| 1 | Titel des Gegners | `f`, `m`, `c`, leer | f=FM, m=IM, g=GM, c=CM; Kleinbuchstaben |
| 2 | Frauen-Titel des Gegners | `wf`, `wm`, `wg`, leer | wf=WFM, wm=WIM, wg=WGM |
| 3 | Gegner-Rating | `2344 ` | Kann `<font color=blue> * </font>` enthalten (Rating-Differenz >400) |
| 4 | Gegner-Föderation | `RUS` | 3-stelliger FIDE-Code |
| 5 | Ergebnis | `0.50` | Numerisch: `1.00`, `0.50`, `0.00` |
| 6 | Anzahl Partien | `1` | Immer `1` bei Einzelpartien |
| 7 | Rating-Change (ungewichtet) | `-0.22` | Dezimal, positiv oder negativ |
| 8 | K-Faktor | `10` | 10, 20 oder 40 |
| 9 | K * Change (gewichtet) | `-2.20` | = Spalte 7 × Spalte 8 |

**Summary-Zeile** (2. Zeile der Tabelle, `bgcolor=#e6e6e6`):
- Spalte 0: **Rc** — Durchschnitts-Rating der Gegner im Turnier
- Spalte 1: **Ro** — eigenes Rating zu Periodenbeginn ← **Quelle für `rating_history`**
- Spalte 5: **w** — erzielte Punkte im Turnier
- Spalte 6: **n** — Anzahl Partien im Turnier

**Wichtige Details:**
- **Keine Gegner-FIDE-ID** in den Partiezeilen — wird nachträglich per Lookup aufgelöst
- **Gegner-Titel** als separate Spalten (title + women_title), nicht im Namen
- **Spielerfarbe** (Schwarz/Weiss) aus CSS-Klasse des Span im Namen ableitbar

Parser-Anforderungen:
- BeautifulSoup4 mit `html.parser`
- Alle `<table class="calc_table">` iterieren (eine pro Turnier)
- Turnier-Name und -Infos aus den `div`-Elementen vor jeder Tabelle extrahieren
- Partiezeilen identifizieren: `<tr bgcolor=#efefef>` mit `<td class=list4>`
- Summary-Zeile identifizieren: `<tr bgcolor=#e6e6e6>` → `Ro` extrahieren für rating_history
- Robustes Parsen: `int()`/`float()` mit try/except, `None` bei Fehler
- Rating-Feld bereinigen: `<font>`-Tags und `*` entfernen vor dem Parsen
- Result normalisieren: `"1.00"` → `"1"`, `"0.50"` → `"0.5"`, `"0.00"` → `"0"`
- Gibt Tupel `(games, k_factor, own_rating)` zurück:
  - `games`: Liste von Dicts mit Keys:
    `fide_id, period, opponent_name, opponent_title, opponent_women_title,
     opponent_rating, opponent_federation, result, rating_change,
     rating_change_weighted, color, tournament_name, tournament_location,
     tournament_start_date, tournament_end_date`
  - `k_factor`: Integer (10/20/40), aus der ersten Partiezeile (Spalte 8)
  - `own_rating`: Integer, aus der Summary-Zeile (Ro) des ersten Turniers
- Bei leerer Seite / keinem Match: `([], None, None)` zurückgeben

**Referenz-HTML** gespeichert in `/tmp/fide_calc_sample.html` (Spieler 24171760, April 2025).

### Task 3.3 — scraper/db.py

Implementiere mit `psycopg2`:

```python
def get_connection():
    # Connection aus config.py (DATABASE_URL)

def upsert_games(conn, games: list[dict]):
    # INSERT INTO game_results ... ON CONFLICT (fide_id, period, game_index) DO NOTHING

def upsert_rating_history(conn, fide_id, period, own_rating):
    # INSERT INTO rating_history (fide_id, period, std_rating)
    # ON CONFLICT DO UPDATE SET std_rating=...
    # own_rating kommt aus der Parser-Ausgabe (Ro aus Summary-Zeile)

def mark_period_scraped(conn, fide_id, period, status='ok', k_factor=None):
    # INSERT INTO scrape_periods ... ON CONFLICT DO UPDATE SET scraped_at=NOW(), status=..., k_factor=...

def save_period(conn, fide_id, period, games, k_factor, own_rating):
    # Alles in EINER Transaktion:
    # 1. upsert_games(conn, games)
    # 2. upsert_rating_history(conn, fide_id, period, own_rating)
    # 3. mark_period_scraped(conn, fide_id, period, 'ok', k_factor)
    # Bei Fehler: Rollback, dann mark_period_scraped mit status='error'

def get_pending_periods(conn, periods: list[str], fide_ids: list[int] = None):
    # Gibt (fide_id, period)-Paare zurück, die noch NICHT in scrape_periods stehen
    # Falls fide_ids=None: alle aktiven Spieler aus players-Tabelle nehmen
```

### Task 3.4 — scraper/main.py

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

## Phase 4 — Spielerliste

### Task 4.1 — scripts/seed_players.py

Import **aller** Spieler aus der FIDE-Download-Liste + Age-Matched Sampling für die Kontrollgruppe.

**Datenquelle:**
- Dateien in `data/` (z.B. `players_list_foa_2026-04.txt`)
- Format: fixed-width mit folgenden Spaltenpositionen (0-indexed, Stand April 2026):
  - ID: Pos 0–14, Name: Pos 15–75, Fed: Pos 76–79, Sex: Pos 80,
  - Title: Pos 84–86, WTitle: Pos 89–91, SRtng: Pos 113–117, B-day: Pos 152–156
- **Robustheit**: Header-Zeile parsen und Spaltenpositionen dynamisch ableiten,
  statt nur hardcodierte Offsets zu verwenden. Fallback auf die obigen Positionen.

**Ablauf:**
1. TXT-Datei einlesen und **alle** Spieler in `players`-Tabelle laden (UPSERT)
   — ca. 1,8 Mio Einträge, `analysis_group = NULL`
2. Spieler mit `STD BETWEEN 2400 AND 2600` identifizieren:
   - **female_top**: Alle Frauen (`SEX='F'`) → `analysis_group = 'female_top'`
   - **male_control**: Age-Matched Sampling von 130 Männern:
     - Geburtsjahr-Verteilung der Frauen berechnen (Dekaden-Buckets)
     - 130 Slots proportional auf Dekaden verteilen
     - Pro Dekade zufällig aus dem Männer-Pool sampeln
     - `random.seed(42)` für Reproduzierbarkeit (in `config.yaml` überschreibbar)
   - → `analysis_group = 'male_control'` setzen
3. Zusammenfassung ausgeben

**CLI-Optionen:**
```bash
python seed_players.py                              # alle Spieler + beide Gruppen
python seed_players.py --group female_top           # nur Gruppen-Zuweisung Frauen
python seed_players.py --group male_control         # nur Gruppen-Zuweisung Männer (mit Sampling)
python seed_players.py --group male_control --n 200 # andere Stichprobengröße
python seed_players.py --min-rating 2400 --max-rating 2550  # engere Range
python seed_players.py --seed 123                   # anderer Random-Seed
python seed_players.py --file data/players_list_foa_2026-04.txt  # spezifische Datei
```

**Ausgabe:** Zusammenfassung mit Gesamt-Import-Statistik und Dekaden-Verteilung beider Gruppen.

**Hinweis zum Volumen:** 1,8 Mio INSERT/UPSERT braucht Batch-Verarbeitung
(`executemany` oder `COPY`), nicht Einzelinserts.

### Task 4.2 — scripts/import_rating_snapshots.py

Importiert historische FIDE-TXT-Dateien in `rating_history.published_rating` zur Validierung.

**Ablauf:**
1. Alle Dateien in `data/` matchen: `players_list_foa_YYYY-MM.txt`
2. Periode aus Dateiname ableiten (z.B. `2025-01` → `2025-01-01`)
3. Pro Datei: für alle Spieler mit `analysis_group IS NOT NULL` das Rating extrahieren
4. `INSERT INTO rating_history (fide_id, period, published_rating) ... ON CONFLICT DO UPDATE SET published_rating = ...`

```bash
python import_rating_snapshots.py                        # alle Dateien in data/
python import_rating_snapshots.py --file data/players_list_foa_2024-06.txt  # einzelne Datei
```

**Validierungsabfrage** (nach Scraping + Import):
```sql
SELECT fide_id, period, std_rating, published_rating,
       std_rating - published_rating AS diff
FROM rating_history
WHERE std_rating IS NOT NULL AND published_rating IS NOT NULL
  AND std_rating != published_rating;
```

### Task 4.3 — scripts/resolve_opponents.py

Löst `opponent_fide_id` in `game_results` nachträglich auf.

**Algorithmus:**
1. Alle Zeilen mit `opponent_fide_id IS NULL` laden
2. Pro Zeile: Lookup in `players`-Tabelle per `name = opponent_name AND federation = opponent_federation`
3. Falls genau 1 Match → `opponent_fide_id` setzen
4. Falls mehrere Matches → zusätzlich `std_rating` vergleichen (nächstes Rating ±50)
5. Falls kein Match → NULL lassen, loggen

**Erwartete Trefferquote:** >95% für Gegner mit Rating 2000+.

```bash
python resolve_opponents.py                    # alle unaufgelösten
python resolve_opponents.py --period 2025-04-01  # nur eine Periode
python resolve_opponents.py --dry-run          # nur zählen, nicht schreiben
```

---

## Phase 5 — Docker & Deployment

### Task 5.1 — scraper/Dockerfile

```dockerfile
FROM python:3.12-slim
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
CMD ["python", "main.py", "run", "--latest"]
```

### Task 5.2 — docker-compose.yml

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

### Task 5.3 — .env.example

```
DB_PASSWORD=changeme
DATABASE_URL=postgresql://fide:changeme@db:5432/fidedb
LOG_LEVEL=INFO
```

### Task 5.4 — Cron-Setup auf VPS

Monatlichen Cron-Job anlegen, der den Scraper automatisch triggert:

```bash
# crontab -e
5 6 3 * * cd /opt/fide-scraper && docker compose run --rm scraper python main.py run --latest >> /var/log/fide-scraper.log 2>&1
```

Am 3. jeden Monats um 6:05 Uhr — FIDE publiziert die neue Liste am 1., braucht manchmal 1–2 Tage.

---

## Phase 6 — Historische Daten (Backfill)

### Task 6.1 — scripts/backfill.py

Skript zum Nachladen vergangener Perioden:

```bash
python backfill.py --from 2022-01-01 --to 2025-10-01
```

- Generiert alle Monats-Perioden zwischen `--from` und `--to`
- Nutzt fetcher/parser/db direkt (kein Subprocess)
- Höflicheres Rate-Limiting beim Backfill: `sleep(random.uniform(2.0, 4.0))`
- Checkpoint-Mechanismus: bereits abgehakte Perioden (Status `'ok'`) werden übersprungen
- Empfehlung: in einer `tmux`-Session auf dem VPS laufen lassen

Erwartetes Datenvolumen beim initialen Backfill (3 Jahre = 36 Perioden, 64 Frauen + 130 Männer = 194 Spieler):
ca. 6.984 Requests → bei 3s Ø ca. 5,8 Stunden.

---

## Phase 1 — Konfiguration (config.yaml) — Details

### Task 1.1 — config.yaml

Zentrale Konfigurationsdatei, die von `main.py` und `seed_players.py` gelesen wird.
Ersetzt harte CLI-Flags — CLI-Overrides bleiben optional möglich.

```yaml
scraper:
  rate_limit:
    min_sleep: 1.2
    max_sleep: 2.5
  backfill_rate_limit:
    min_sleep: 2.0
    max_sleep: 4.0
  retry:
    max_attempts: 3
    backoff_base: 4       # Sekunden: 1s, 4s, 16s
  timeout: 15

groups:
  female_top:
    sex: F
    min_rating: 2400
    max_rating: 2600
    sample_size: all      # vollständige Population
  male_control:
    sex: M
    min_rating: 2400
    max_rating: 2600
    sample_size: 130
    sampling:
      method: age_matched # Geburtsjahr-Dekaden proportional zu female_top
      seed: 42            # für Reproduzierbarkeit

periods:
  mode: latest            # "latest" | "range" | "list"
  # from: 2022-01-01      # nur bei mode: range
  # to: 2025-10-01
  # list:                  # nur bei mode: list
  #   - 2025-01-01
  #   - 2025-04-01
```

### Task 1.2 — scraper/config.py anpassen

- `config.yaml` mit PyYAML lesen
- `.env` weiterhin für Secrets (DATABASE_URL, DB_PASSWORD)
- Merged Config-Objekt bereitstellen, das beide Quellen kombiniert
- CLI-Args überschreiben Config-Werte (Precedence: CLI > config.yaml > Defaults)

---

## Phase 7 — Tests

### Task 7.1 — tests/

Mindest-Testabdeckung:

- **test_parser.py**: Parser gegen gespeicherte HTML-Fixtures testen
  (`tests/fixtures/calc_24171760_2025-04-01.html`).
  Prüft: Anzahl Partien, Gegnernamen, Ratings, Ergebnisse, K-Faktor, own_rating, Farbe.
- **test_db.py**: Upsert-Logik testen (INSERT + Duplikat-Handling + Transaktion).
  Nutzt eine Test-DB oder SQLite-Mock.
- **test_sampling.py**: Validiert, dass die Dekaden-Verteilung der Kontrollgruppe
  proportional zur Frauen-Gruppe ist. Prüft Reproduzierbarkeit mit fixem Seed.
- **test_fetcher.py**: Testet Retry-Logik und Header-Konfiguration (mit `responses`-Mock).

Fixture-Datei: `/tmp/fide_calc_sample.html` → `tests/fixtures/` kopieren.

---

## Phase 8 — Analyse (SQL-Views + Jupyter Notebooks)

### Task 8.1 — migrations/002_analysis_views.sql

SQL-Views für die vier Kernfragen. Greifen auf `game_results`, `players`,
`scrape_periods` und `rating_history` zu.

**Wichtig:** Alle Views filtern `p.active = TRUE`, damit laut FIDE aktuell inaktive
Spieler (Flag `i`/`wi`) aus den Auswertungen ausgeschlossen werden — siehe Sektion
"Bekannte Limitationen des Datensatzes".

```sql
-- Gegnerstruktur: Durchschnitts-Rating der Gegner vs. eigenes Rating pro Gruppe
CREATE OR REPLACE VIEW v_opponent_strength AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    rh.std_rating AS own_rating,
    AVG(gr.opponent_rating) AS avg_opponent_rating,
    AVG(gr.opponent_rating) - rh.std_rating AS avg_opponent_diff
FROM game_results gr
JOIN players p USING (fide_id)
LEFT JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period, rh.std_rating;

-- Rating-Volatilität: mittlere absolute Rating-Änderung, normalisiert nach K-Faktor
CREATE OR REPLACE VIEW v_rating_volatility AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    sp.k_factor,
    AVG(ABS(gr.rating_change)) AS avg_abs_change,
    CASE WHEN sp.k_factor > 0
         THEN AVG(ABS(gr.rating_change)) / sp.k_factor
         ELSE NULL END AS normalized_volatility
FROM game_results gr
JOIN players p USING (fide_id)
LEFT JOIN scrape_periods sp ON sp.fide_id = gr.fide_id AND sp.period = gr.period
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period, sp.k_factor;

-- Turnierfrequenz: Anzahl Partien pro Periode pro Gruppe
CREATE OR REPLACE VIEW v_tournament_frequency AS
SELECT
    p.analysis_group,
    gr.fide_id,
    gr.period,
    COUNT(*) AS num_games,
    COUNT(DISTINCT gr.tournament_name) AS num_tournaments
FROM game_results gr
JOIN players p USING (fide_id)
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
GROUP BY p.analysis_group, gr.fide_id, gr.period;

-- Rating-Progression: Rating-Verlauf über Zeit
CREATE OR REPLACE VIEW v_rating_progression AS
SELECT
    p.analysis_group,
    rh.fide_id,
    rh.period,
    rh.std_rating,
    rh.std_rating - FIRST_VALUE(rh.std_rating)
        OVER (PARTITION BY rh.fide_id ORDER BY rh.period) AS rating_delta_from_start
FROM rating_history rh
JOIN players p USING (fide_id)
WHERE p.active = TRUE AND p.analysis_group IS NOT NULL
ORDER BY rh.fide_id, rh.period;
```

### Task 8.2 — Jupyter Notebooks

Vier Notebooks in `notebooks/`, die auf die Views zugreifen und visualisieren:

| Notebook | Quelle | Visualisierung |
|----------|--------|----------------|
| `01_opponent_structure.ipynb` | `v_opponent_strength` | Boxplot avg_opponent_diff pro Gruppe; Histogramm Gegner-Ratings |
| `02_rating_volatility.ipynb`  | `v_rating_volatility` | Vergleich normalized_volatility pro Gruppe; Zeitreihe |
| `03_tournament_frequency.ipynb` | `v_tournament_frequency` | Boxplot Partien/Monat pro Gruppe; Saisonalität |
| `04_rating_progression.ipynb` | `v_rating_progression` | Linienplot Rating über Zeit (Median + Quantile pro Gruppe) |
| `05_rating_change_sums.ipynb` | direktes SQL auf `game_results` | Σ `rating_change_weighted` pro Jahr/gesamt; Splits nach Gegner-Geschlecht, Farbe, Stärke-Bucket |
| `06_age_cohorts.ipynb`        | direktes SQL auf `game_results` | Alters-Kohorten (Anker **2015**: <20, 20–30, 30–40, 40–50, >50); Heatmap Kohorte × Jahr; Spieler-Tabelle (CSV-Export) |
| `07_peer_performance.ipynb`   | direktes SQL auf `game_results` | Nur `female_top`: Partien-Anzahl, Σ `rating_change_weighted`, Ø pro Partie — aufgeteilt nach Kohorte (Anker 2015) × Stärke-Bucket (±80 Elo: stärker/gleich/schwächer) × Gegner-Geschlecht (F/M). Nur aufgelöste Gegner. Export als XLSX + CSVs. |

Alle Notebooks nutzen:
- `psycopg2` für DB-Verbindung — liest `DATABASE_URL` aus `.env.notebook`
  (zeigt typischerweise auf `localhost:5434`, gepatcht via `scripts/tunnel.sh` zur VPS-DB)
- `pandas` für Datenhandling
- `matplotlib` + `seaborn` für Plots
- Notebooks 05/06/07 werden aus `_generate_05.py` / `_generate_06.py` / `_generate_07.py`
  generiert (Quelle der Wahrheit), Notebooks 01–04 aus `_generate_notebooks.py`.

---

## Aktueller Datensatz-Stand (2026-04-26)

- **Range:** 2010-01-01 – 2026-03-01 (196 Perioden)
- **986.403+ Partien** in `game_results` (wächst mit male_control-Backfill)
- **Gegner-Auflösung:** 911.359 / 935.162 (**97,5 %**) aufgelöst
- **Spieler:** female_top 66, male_control 649 (⏳ Backfill läuft), elite_2600 202,
  swiss_2026 349 exklusiv, female_2200 321 ✅, male_2200 170 (⬜ pending) — **1.583 total**
- **Neue Tabelle:** `groups` (Migration 010) — zentrale Gruppenübersicht mit Status
- **Neue Spalten in `game_results`** (migrations 007–009):
  - `opponent_sex` (CHAR 1) — 98,1 % befüllt
  - `tournament_type` — `open` | `women` | `team` | `women_team` | `closed` | `knockout`
  - `expected_score` — Elo-Erwartungswert: 1/(1+10^((opp−own)/400))
  - `over_performance` — result − expected_score
  - `opponent_match_quality` — `ok` | `wide_gap` (diff>200) | `unresolved`

### QC-System (Stand 2026-04-25)

- **Dateien:** `migrations/004–006`, `scripts/quality_check.py`, `notebooks/08_qc_elo_analysis.ipynb`
- **TXT-Snapshots:** **195 Perioden** in `data/` — **Jan 2006 – Apr 2026**
  - Jan 2006 – Aug 2012: quartalsweise (kein `standard_`-Präfix, kein Sex/WTit-Feld)
  - Sep 2012 – Sep 2023: monatlich (mit einzelnen Lücken)
  - Okt 2023 – Apr 2026: vollständig monatlich
  - Skip-Logik 2026-04-26: bereits importierte Perioden werden übersprungen
- **Ergebnisse (Stand 2026-04-26, 242.028 Fenster):**
  OK **97,6 %** | Warn 1,2 % | Error 1,2 % | Avg |Δ| 0,7
  - 2009–2026: 99,8–100 % OK (2009 nach Backfill validiert, MissingP = 0)
  - 2006–2008: 44–59 % OK (kein Scraping → MissingP = alle Fenster)
  - 2026: MissingP durch April 2026 noch nicht gescrapt
- **Bug-Fix 2026-04-24:** Off-by-one in Perioden-Bedingung: `>= T1 / < T2` → `> T1 / <= T2`
- **FIDE-Korrektur März 2024:** +0,4×(2000−Post-Game) für sub-2000-Spieler; in
  `rating_corrections` erfasst, QC berücksichtigt Korrekturen automatisch

---

## Reihenfolge der Implementierung

1. **Phase 1**: `config.yaml` erstellen + `config.py` implementieren
2. **Phase 2**: `migrations/001_initial.sql` aktualisieren (neue Felder) + DB hochfahren + testen
3. **Phase 3**: `fetcher.py` implementieren + AJAX-Endpoint manuell testen
4. **Phase 3**: `parser.py` implementieren + Ausgabe gegen Fixture prüfen ← **kritischer Schritt**
5. **Phase 3**: `db.py` implementieren (mit Transaktionssicherheit via `save_period()`)
6. **Phase 3**: `main.py` end-to-end testen: 2 Spieler-IDs (1 weiblich, 1 männlich), 1 Periode
7. **Phase 4**: `seed_players.py` — alle Spieler + Gruppen-Zuweisung mit Sampling
8. **Phase 4**: `import_rating_snapshots.py` — historische TXT-Dateien importieren
9. **Phase 5**: Docker-Setup finalisieren + Cron konfigurieren
10. **Phase 6**: Backfill für gewünschten Zeitraum starten
11. **Phase 4**: `resolve_opponents.py` — Gegner-FIDE-IDs auflösen (nach Backfill, braucht Daten)
12. **Phase 4**: Validierung: `std_rating` vs. `published_rating` vergleichen
13. **Phase 7**: Tests schreiben (Parser-Fixtures, DB-Integration, Sampling-Validierung)
14. **Phase 8**: `002_analysis_views.sql` deployen
15. **Phase 8**: Jupyter Notebooks erstellen und erste Auswertungen fahren

---

## Bekannte Limitationen des Datensatzes

### Gegner-Auflösung: Zweifelhafte Mehrfachmatches

`scripts/resolve_opponents.py` arbeitet bei mehreren Kandidaten mit demselben
`name + federation` per Closest-Rating **ohne harte Toleranz**. Das löst Fälle wie
Stocek (diff=52) und Nagy (diff=105), bei denen Rating-Drift zwischen Spielzeit
und April-2026-Snapshot die Zuordnung verhindert hätte.

Einige Treffer haben jedoch große Rating-Abstände (>200), was auf falsche
Namensgleichheit hindeutet — z.B. `Petrov, Nikita (RUS)` mit 6 Kandidaten, bester
Abstand 1017. Diese Zeilen sind in `game_results.opponent_fide_id` eingetragen,
inhaltlich aber zweifelhaft. Künftig per Query identifizierbar:

```sql
SELECT gr.opponent_name, gr.opponent_federation,
       gr.opponent_rating, p.std_rating,
       ABS(gr.opponent_rating - p.std_rating) AS diff
FROM game_results gr
JOIN players p ON p.fide_id = gr.opponent_fide_id
WHERE ABS(gr.opponent_rating - p.std_rating) > 200
ORDER BY diff DESC;
```

Nach dem 2015–2019-Backfill (Stand 2026-04-19) bleiben **25.625 Zeilen (16,2 %
von 158.429 Partien)** unresolved — überwiegend indische Spieler mit abweichender
Schreibweise. Fuzzy-Matching ist nicht implementiert.

### Inaktive Spieler im initialen Seed (2026-04)

Der initiale Seed (2026-04-17) hat die `Flag`-Spalte der FIDE-TXT-Datei nicht ausgelesen.
Dadurch sind in beiden Analyse-Gruppen Spieler enthalten, die FIDE aktuell als **inaktiv**
markiert (Flag `i` oder `wi`, z.B. die Polgar-Schwestern, Xie Jun, Chiburdanidze,
Kosintsevas). Der Flag-Parser wurde am 2026-04-18 nachgezogen
(`seed_players.py --refresh-metadata`); die `players.active`-Spalte reflektiert seitdem
den FIDE-Status aus April 2026. Die ursprüngliche `analysis_group`-Zuordnung wurde
**bewusst nicht** neu ausgeführt; die spätere Erweiterung um +150 Männer
(`extend_male_control.py`) hat dagegen von Anfang an nur aktive Spieler gesampelt.

**Ist-Stand (nach allen Erweiterungen, Stand 2026-04-24):**

| Gruppe         | seeded | aktiv (FIDE 2026-04) | inaktiv |
|----------------|-------:|---------------------:|--------:|
| female_top     |     64 |                   43 |      21 |
| male_control   |    479 |                  435 |      44 |
| elite_2600     |    202 |                  153 |      49 |
| swiss_2026     |    349 |                  338 |      11 |

Die 17 Spieler, die als "inaktiv" markiert sind, aber im Backfill-Range (2022–2025)
Partien gespielt haben, sind in 2025/2026 inaktiv geworden — sie tauchen in späteren
Perioden nicht mehr auf.

**Konsequenzen für die Analyse:**

- Alle SQL-Views in `migrations/002_analysis_views.sql` **müssen** nach `analysis_group`
  UND `active = TRUE` filtern, damit die Auswertung nur aktuell aktive Spieler umfasst.
- Das Age-Matching der Kontrollgruppe basiert auf der Geburtsjahr-Verteilung **aller 64
  Frauen** (inkl. inaktiver). Eine strengere Auswertung müsste die Verteilung auf die
  43 aktiven Frauen neu rechnen und ggf. Männer nachsampeln — wurde bewusst zurückgestellt.
- Für künftige Seeds (z.B. auf neueren TXT-Snapshots) wird `active` nun automatisch
  korrekt gesetzt. Das Sampling selbst filtert inaktive Spieler derzeit noch nicht aus;
  bei einem künftigen Re-Seed sollte `scripts/seed_players.py` entsprechend erweitert werden.

---

## Bekannte Risiken

| Risiko | Massnahme |
|---|---|
| FIDE ändert HTML-Layout | Parser-Tests gegen Fixtures; rohe HTML bei Parse-Fehlern loggen; Cron-Fehler erzeugt Alert |
| AJAX-Endpoint ändert sich | Fetcher nutzt die indirekte URL `/a_indv_calculations.php`; bei Fehler auch die `.phtml`-Seite prüfen |
| Transaktionsfehler beim Scrapen | `save_period()` in db.py kapselt alles in einer Transaktion; Rollback bei Fehler |
| IP-Sperre bei zu vielen Requests | Sleep-Jitter + Backoff; max. ~500 Requests/Tag |
| Leere Calculations-Seite | Status `'no_data'` → wird nicht erneut versucht |
| DB-Konflikt mit tunnelbliq | Eigene DB `fidedb`, eigener Port 5433 |
| Verzerrung Kontrollgruppe | Age-Matched Sampling mit reproduzierbarem Seed; Rating-Range identisch |

---

## Abhängigkeiten (requirements.txt)

```
requests==2.32.3
beautifulsoup4==4.12.3
psycopg2-binary==2.9.10
python-dotenv==1.0.1
pyyaml==6.0.2
pandas==2.2.3
matplotlib==3.9.3
seaborn==0.13.2
jupyter==1.1.1
pytest==8.3.4
responses==0.25.6
```
