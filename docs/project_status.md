# FIDE Scraper — Projektdokumentation

Stand: 24. April 2026

---

## 1. Projektziel

Der FIDE Scraper lädt für eine definierte Gruppe von Schachspielern die monatlichen
Einzelpartien von der FIDE-Calculations-Seite und speichert sie in einer PostgreSQL-Datenbank.
Ziel ist eine **quantitative Analyse von Top-Spielerinnen (ELO ~2400–2600) im Vergleich
mit gleichstarken männlichen Spielern** entlang vier Kernfragen:

| Frage | Beschreibung |
|---|---|
| Gegnerstruktur | Spielen Frauen auf diesem Niveau häufiger gegen stärkere oder schwächere Gegner? |
| Rating-Volatilität | Unterscheiden sich die mittleren Rating-Änderungen pro Partie (normalisiert nach K-Faktor)? |
| Turnierfrequenz | Wie viele Partien spielen die Gruppen pro Monat? |
| Rating-Progression | Entwickelt sich das Rating über Zeit anders? |

Als zusätzliche Vergleichsschicht wurde eine Gruppe der stärksten Spieler weltweit
(ELO ≥ 2600) sowie Spieler der Schweizer Mannschaftsmeisterschaft (SMM 2026)
aufgenommen.

---

## 2. Technischer Aufbau

### 2.1 Infrastruktur

| Komponente | Beschreibung |
|---|---|
| VPS | Hostinger, IP `187.124.181.116`, `/opt/fide-scraper/` |
| Datenbank | TimescaleDB (PostgreSQL 16), läuft als Docker-Container auf dem VPS |
| Scraper | Python 3.12 in eigenem Docker-Container, läuft on-demand via `docker compose run` |
| Verbindung lokal | SSH-Tunnel `localhost:5434 → VPS:5432` via `scripts/tunnel.sh` |
| Repository | `https://github.com/Chesspit/fide-scraper` |

### 2.2 Datenfluss

```
FIDE Calculations-Seite (AJAX)
        │
        ▼
scraper/fetcher.py       → HTTP GET mit Retry (max. 3, exponentieller Backoff)
        │
        ▼
scraper/parser.py        → BeautifulSoup: Partien + K-Faktor + eigenes Rating (Ro)
        │
        ▼
scraper/db.py            → PostgreSQL UPSERT (Transaktion, Reconnect-Wrapper)
        │
        ▼
PostgreSQL / TimescaleDB → Tabellen: players, game_results, scrape_periods,
                           rating_history, rating_corrections, qc_rating_check
        │
        ▼
scripts/resolve_opponents.py  → Gegner-FIDE-IDs per Name+Föderation+Rating nachschlagen
        │
        ▼
notebooks/               → Pandas + Matplotlib / Seaborn Analysen
```

### 2.3 Scraper-Konfiguration (`config.yaml`)

```yaml
scraper:
  rate_limit:
    min_sleep: 1.2      # Sekunden zwischen normalen Requests
    max_sleep: 2.5
  backfill_rate_limit:
    min_sleep: 2.0      # Langsameres Rate-Limit beim Backfill
    max_sleep: 4.0
  retry:
    max_attempts: 3
    backoff_base: 4     # 1s → 4s → 16s
  timeout: 15
```

---

## 3. Spielergruppen

| Gruppe | Kriterium | Spieler (aktiv/inaktiv) | Beschreibung |
|---|---|---|---|
| `female_top` | ELO 2400–2600, Geschlecht F | 45 / 21 | Vollständige Population; 2 Spielerinnen 2026-04-24 nachgetragen (Moser AUT, Khurtsidze GEO) |
| `male_control` | ELO 2400–2600, Geschlecht M, age-matched | 435 / 44 | Proportional zur Altersverteilung der Frauen gesampelt (Seed 42/43/44), drei Erweiterungen → 479 total |
| `elite_2600` | ELO ≥ 2600 | 153 / 49 | Obere Vergleichsschicht |
| `swiss_2026` | SMM 2026 NLA + NLB, erste 20 Teams | 338 / 11 (349 exkl.) | Boolean-Flag `swiss_2026`; 13 Spieler überschneiden sich mit anderen Gruppen |
| `female_2200` | ELO 2200–2399, Geschlecht F, aktiv | 321 / 0 | Erweiterungsgruppe; Backfill 2010-01–2026-03 gestartet 2026-04-24 (~52h) |

Das Age-Matching der Kontrollgruppe orientiert sich an der Geburtsjahr-Dekaden-
Verteilung der 64 Frauen, damit altersbedingte Effekte (K-Faktor, Karrierephase)
den Vergleich nicht verzerren.

Die `swiss_2026`-Gruppe nutzt eine eigene Boolean-Spalte statt `analysis_group`,
damit Spieler gleichzeitig in mehreren Gruppen sein können (z.B. Santos Ruiz in
`elite_2600` und `swiss_2026`).

---

## 4. Datenbankschema

### 4.1 Tabelle `players`

Enthält alle ~1,8 Mio Spieler aus der FIDE-Download-Datei (April 2026) als Lookup-
Tabelle. Spieler der Analysegruppen erhalten einen `analysis_group`-Wert.

**Schlüsselfelder:**

| Spalte | Typ | Bedeutung |
|---|---|---|
| `fide_id` | INTEGER PK | FIDE-ID |
| `name` | TEXT | Name (Format: `Nachname, Vorname`) |
| `federation` | CHAR(3) | FIDE-Föderationscode |
| `title` | TEXT | GM, IM, FM, CM oder NULL |
| `women_title` | TEXT | WGM, WIM, WFM oder NULL |
| `sex` | CHAR(1) | M / F |
| `birth_year` | INTEGER | Geburtsjahr |
| `std_rating` | INTEGER | Letztes bekanntes Standard-Rating (April 2026) |
| `analysis_group` | TEXT | `female_top` \| `male_control` \| `elite_2600` \| NULL |
| `swiss_2026` | BOOLEAN | TRUE = Spieler in SMM 2026 (NLA/NLB, erste 20 Teams) |
| `active` | BOOLEAN | FIDE-Aktivitätsstatus (April 2026) |

---

### 4.2 Tabelle `scrape_periods`

Protokolliert, welche (Spieler, Periode)-Kombinationen bereits abgerufen wurden.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `fide_id` | INTEGER FK | Spieler-ID |
| `period` | DATE PK | Erster des Monats, z.B. `2025-01-01` |
| `status` | TEXT | `ok` \| `no_data` \| `error` |
| `k_factor` | INTEGER | K-Faktor (10 / 20 / 40) |

---

### 4.3 Tabelle `game_results`

Eine Zeile = eine Einzelpartie.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `id` | BIGSERIAL PK | Interne ID |
| `fide_id` | INTEGER FK | Analyse-Spieler |
| `period` | DATE | Ratingperiode |
| `game_index` | INTEGER | Laufende Nummer innerhalb (fide_id, period) |
| `opponent_fide_id` | INTEGER | FIDE-ID des Gegners (per Lookup aufgelöst) |
| `opponent_rating` | INTEGER | Rating des Gegners zur Spielzeit |
| `result` | TEXT | `1` \| `0.5` \| `0` |
| `rating_change_weighted` | NUMERIC(5,2) | K × rating_change |
| `color` | CHAR(1) | `W` (Weiss) \| `B` (Schwarz) |

---

### 4.4 Tabelle `rating_history`

Monatliches Rating pro Spieler aus zwei Quellen:

| Spalte | Typ | Bedeutung |
|---|---|---|
| `std_rating` | INTEGER | Rating aus FIDE-Calculations (Ro aus Summary-Zeile) |
| `published_rating` | INTEGER | Rating aus historischen FIDE-TXT-Snapshots |

Abweichungen > ±5 deuten auf Scraping-Fehler oder FIDE-Nachkorrekturen hin.

---

### 4.5 Tabelle `rating_corrections` (neu 2026-04-24)

Speichert bekannte nicht-spielbasierte FIDE-Ratinganpassungen.

| Spalte | Typ | Bedeutung |
|---|---|---|
| `fide_id` | INTEGER FK | Spieler-ID |
| `period` | DATE | Periode, in der die Korrektur wirksam wurde |
| `amount` | INTEGER | ELO-Punkte (positiv = Bonus) |
| `corr_type` | TEXT | `fide_one_off` |
| `source` | TEXT | `snapshot_delta` (exakt) \| `formula` (Näherung) |

**Aktuell befüllt:** FIDE-Einmalkorrektur März 2024 für alle Spieler mit Rating < 2000.
Formel: `+0,4 × (2000 − Post-Game-Rating)`. 379.276 Einträge.

---

### 4.6 Tabelle `qc_rating_check` (neu 2026-04-22)

Ergebnis der QC-Prüfung pro (Spieler, Zeitfenster).

| Spalte | Typ | Bedeutung |
|---|---|---|
| `expected_change` | NUMERIC | `published[T2] − published[T1]` |
| `scraped_change` | NUMERIC | `SUM(rating_change_weighted)` im Fenster |
| `delta` | NUMERIC | `expected − scraped` (roh) |
| `correction` | NUMERIC | Summe bekannter Korrekturen im Fenster |
| `flag` | TEXT | `ok` / `warn` / `error` — basiert auf `delta − correction` |

---

## 5. Aktueller Datensatz-Stand

| Kennzahl | Wert |
|---|---|
| **Gesamt-Partien** | **696.820** |
| **Gegner aufgelöst** | **681.096 (97,7 %)** |
| **Spieler mit Daten** | **1.094** |
| **Perioden** | **196 (2010-01 bis 2026-03)** |

### 5.1 Scraping-Status pro Gruppe

| Gruppe | Spieler | ok-Perioden | no_data | Range |
|---|---|---|---|---|
| female_top | 64 | 4.620 | 8.627 | 2010-01 – 2026-03 |
| male_control | 479 | 38.412 | 60.739 | 2010-01 – 2026-03 |
| elite_2600 | 202 | 18.420 | 23.394 | 2010-01 – 2026-03 |
| swiss_2026 | 349 | 26.061 | 46.182 | 2010-01 – 2026-03 |

### 5.2 TXT-Snapshot-Coverage

66 Snapshot-Dateien in `data/`:
- **Feb 2015**: Einzeln
- **Jan + Apr + Jul + Okt 2015–2025**: quartalsweise
- **Monatlich ab Okt 2023** bis Apr 2026

→ QC-Fenster: 3 Monate bis Sep 2023, danach 1 Monat.

### 5.3 Abgedeckte Backfills (chronologisch)

| Zeitraum | Gruppe | Abgeschlossen |
|---|---|---|
| 2022-01 → 2025-04 | female_top + male_control (194) | 2026-04-18 |
| 2020-01 → 2021-12 | female_top + male_control | 2026-04-18 |
| 2020-01 → 2025-12 | male_control +150 Männer | 2026-04-18 |
| 2015-01 → 2019-12 | alle 344 Spieler | 2026-04-19 |
| 2015-01 → 2026-03 | elite_2600 (202) | 2026-04-19 |
| 2015-01 → 2026-03 | male_control +199 Männer | 2026-04-20 |
| 2014-01 → 2014-12 | alle | 2026-04-21 |
| 2011-01 → 2013-12 | alle (swiss_2026 inklusive) | 2026-04-23 |
| 2010-01 → 2010-12 | alle 1.094 Spieler | 2026-04-24 |

---

## 6. QC-System

Datei: `scripts/quality_check.py`, Tabellen: `qc_rating_check`, `rating_corrections`

### 6.1 Methodik

```
expected_change = published_rating[T2] − published_rating[T1]
scraped_change  = SUM(rating_change_weighted) für T1 ≤ period < T2
correction      = SUM(rating_corrections.amount) für T1 < period ≤ T2
delta_adj       = (expected_change − scraped_change) − correction

Flag: ok (|Δ_adj| ≤ 5) | warn (≤ 15) | error (> 15)
```

### 6.2 Ergebnisse (69.041 Fenster, Stand 2026-04-24, nach Bug-Fix)

| Jahr | Fenster | OK% | Warn | Error | Avg\|Δ\| |
|---|---|---|---|---|---|
| 2015 | 3.867 | 99,6% | 10 | 7 | 0,4 |
| 2016 | 3.997 | 99,8% | 4 | 3 | 0,3 |
| 2017 | 4.088 | 99,8% | 7 | 0 | 0,3 |
| 2018 | 4.167 | 99,9% | 3 | 2 | 0,3 |
| 2019 | 4.215 | 99,9% | 2 | 2 | 0,3 |
| 2020 | 4.253 | 100,0% | 1 | 0 | 0,1 |
| 2021 | 4.272 | 100,0% | 0 | 2 | 0,1 |
| 2022 | 4.291 | 99,7% | 4 | 7 | 0,3 |
| 2023 | 6.493 | 99,8% | 10 | 1 | 0,2 |
| 2024 | 13.027 | 95,6% | 434 | 135 | 1,2 |
| 2025 | 13.091 | 99,9% | 8 | 3 | 0,1 |
| 2026 | 3.280 | 89,1% | 264 | 95 | 1,9 |
| **Gesamt** | **69.041** | **98,5%** | **747** | **257** | **0,5** |

> **Vor dem Bug-Fix** (falsche Perioden-Bedingung `>= T1 / < T2`): 60,7% OK, 15,7% Error.
> Nach Fix (`> T1 / <= T2`): 98,5% OK. Die meisten früheren „Errors" waren Randeffekt-Rauschen.

### 6.3 Verbleibende Abweichungen (257 Errors, 0,4%)

1. **Spiegel-Deltas** — FIDE verbucht eine Korrektur in Periode T und dreht sie in T+1 um.
   Typisch bei verspäteter Turnierverarbeitung. Beispiele: Radzimski (±186), Smirnov (±92).
2. **2026-03→2026-04-Fenster** — April 2026 noch nicht gescrapt; 1.094 Spieler mit
   `missing_periods=1`. Erwartet, wird nach nächstem monatlichem Scraping verschwinden.
3. **Einzelfälle** — verspätete Turniere, retroaktive FIDE-Korrekturen.

### 6.4 FIDE Einmalkorrektur März 2024 — Details

- **Beschlossen:** Dezember 2023 | **Wirksam:** 2024-03-01
- **Formel:** `+0,4 × (2000 − Post-Game-Rating)` für alle Spieler mit Rating < 2000
- **Verifikation (Viktor Guba, AUT, ID 1662279):**
  - Feb-2024-Rating: 1808 | Spielergebnis März: +12 → Post-Game: 1820
  - Korrektur: 0,4 × (2000 − 1820) = **+72** | März-Published: 1808+12+72 = **1892** ✓
- **Stichprobe (379.219 inaktive sub-2000-Spieler):** 87,7% exakter Match (Residual = 0)
- **Unsere Analysegruppen (≥ 2400):** nicht betroffen
- **swiss_2026:** 57 Spieler (Rating 1308–1996) betroffen; Ø Korrektur +61 ELO

### 6.5 CLI

```bash
# Lokal via Tunnel:
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 -m scripts.quality_check --rebuild

# Auf VPS via Docker:
docker compose -f /opt/fide-scraper/docker-compose.yml run --no-deps --rm \
  -e DATABASE_URL=postgresql://fide:nimzo194.@10.0.3.1:5432/fidedb \
  scraper python -m scripts.quality_check --rebuild
```

---

## 7. Offene Punkte

| Aufgabe | Priorität | Status |
|---|---|---|
| Analyse-Notebooks 01–07 starten | Hoch | ⬜ Daten + QC vollständig validiert |
| NB08 (QC) neu generieren | Mittel | ⬜ Zahlen nach Bug-Fix aktualisieren |
| resolve_opponents erneut ausführen | Mittel | ✅ 97,7 % aufgelöst (war 74,4 %) |
| female_2200 Backfill | Hoch | ⏳ läuft auf VPS (~52h, 62.206 Perioden) |
| female_top nachgetragen (Moser, Khurtsidze) | — | ✅ erledigt 2026-04-24 |
| April 2026 scrapen | Niedrig | ⬜ beseitigt 1.094 Missing-Period-Warnings |
| TXT-Snapshots 2013–2014 | Niedrig | ⬜ würde QC-Coverage auf 2010+ erweitern |
| Re-Sampling male_control (nur Aktive) | Niedrig | ⬜ optional, methodisch sauber |
