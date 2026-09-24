# FIDE Scraper — Projektdokumentation

Stand: 3. Juli 2026

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
| Scraper / Orchestrator | Python 3.13; Worker + Dashboard als Docker-Container auf VPS (restart: unless-stopped) |
| Verbindung lokal | SSH-Tunnel `localhost:5434 → VPS:5432` via `scripts/tunnel.sh` |
| Dashboard | `https://scelo.chesspit.net` (Traefik via Coolify, BasicAuth) — Routing-Labels in `orchestrator/docker-compose.yml` |
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
    min_sleep: 3.0      # Menschlich wirkende Pausen
    max_sleep: 5.0
  retry:
    max_attempts: 3
    backoff_base: 4     # 1s → 4s → 16s
  timeout: 15
```

Sleep-Verteilung: Beta(2,5) — meistens ~3–3,5s, gelegentlich länger.
Zusätzlich 8 % Chance auf Extra-Pause von 4–6s (simuliert menschliches Lesen).
Bei HTTP 429: automatische Pause von 45 Minuten.
Bei HTTP 403: sofortiger Stopp mit Fehlermeldung.

### 2.4 Lokales Scraping vom Mac Mini

Ab 2026-04-29 wird **ausschliesslich lokal** gescrapt. Die VPS-IP (187.124.181.116)
ist von FIDE dauerhaft gesperrt (bestätigt 2026-05-09: Timeout auf allen Requests).
Das Script `scripts/run_local_backfill.sh` übernimmt:

```bash
bash scripts/run_local_backfill.sh global_03
```

Funktionen:
- `caffeinate -i` verhindert Mac-Ruhemodus
- SSH-Tunnel wird automatisch gestartet falls nicht aktiv
- Auto-Restart bei Prozessabsturz (inkl. Tunnel-Neustart)
- Logs unter `/tmp/backfill_GRUPPE_local.log`

---

## 3. Spielergruppen

### 3.1 Kern-Analysegruppen

| Gruppe | Kriterium | Spieler | Scraping-Stand |
|---|---|---|---|
| `female_top` | ELO 2400–2600, F | 66 | 2008-04 – 2026-03 ✅ |
| `male_control` | ELO 2400–2600, M, age-matched (Seeds 42/43/44/46) | 649 | 2008-04 – 2026-03 ✅ |
| `elite_2600` | ELO ≥ 2600 | 202 | 2008-04 – 2026-03 ✅ |
| `swiss_2026` | SMM 2026 NLA+NLB, Boolean-Flag | 349 exkl. | 2008-04 – 2026-03 ✅ |
| `female_2200` | ELO 2200–2399, F | 321 | 2008-04 – 2026-03 ✅ |
| `male_2200` | ELO 2200–2399, M, age-matched (Seed 45) | 170 | 2013-01 – 2026-03 ✅ |

### 3.2 Erweiterte Gruppen (in `groups`-Tabelle definiert, 159 total)

Die `groups`-Tabelle ist die zentrale Quelle aller Gruppen-Definitionen mit
Feldern: `elo_min`, `elo_max`, `federations`, `sampling`, `priority`,
`backfill_status`, `scraped_from`, `scraped_to`.

| Prio | Gruppen | Spieler | Inhalt | Status |
|-----:|--------:|--------:|--------|--------|
| 1 | 5 | 1.587 | Kern-Analysegruppen | partial |
| 2 | 1 | 170 | male_2200 | ⛔ skipped (gestrichen 2026-05-18) |
| 3 | 51 | ~5.500 | global_02–28b (weltweit ≥2300) | **51/51 complete** ✅ |
| — | 15 | 1.021 | female_2100 + female_2000 | **15/15 complete** ✅ |
| — | 16 | ~925 | female_1900 | **16/16 complete** ✅ |
| — | 24 | ~1.769 | female_1800 | 19/24 complete, 5 pending (_20–24) |
| 4 | 8 | 1.163 | dach_01–08 (SUI+AUT+GER ≥2200) | pending |
| 5 | 40 | 5.988 | sui_01–20 + aut_01–20 (1400–2199) | pending |
| 6 | 86 | 12.948 | ger_01–86 (≥2000 priorisiert, <2000 deprioritisiert) | pending |

Das Age-Matching der Kontrollgruppe orientiert sich an der Geburtsjahr-Dekaden-
Verteilung der 64 Frauen, damit altersbedingte Effekte (K-Faktor, Karrierephase)
den Vergleich nicht verzerren.

Die `swiss_2026`-Gruppe nutzt eine eigene Boolean-Spalte statt `analysis_group`,
damit Spieler gleichzeitig in mehreren Gruppen sein können (z.B. Santos Ruiz in
`elite_2600` und `swiss_2026`).

### 3.3 Sampling-Strategie male_control

Geburtsjahr-Dekaden der 64 female_top-Spielerinnen bestimmen die Slot-Verteilung:

| Dekade | Frauen | Anteil | 130er-Slots |
|--------|-------:|-------:|------------:|
| 1950er | 1 | 1,6 % | 2 |
| 1960er | 3 | 4,7 % | 6 |
| 1970er | 8 | 12,5 % | 16 |
| 1980er | 19 | 29,7 % | 39 |
| 1990er | 17 | 26,6 % | 35 |
| 2000er | 15 | 23,4 % | 30 |
| 2010er | 1 | 1,6 % | 2 |

Seeds: 42 (130), 43 (+150), 44 (+199), 46 (+170) → 649 Männer total.

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

## 5. Aktueller Datensatz-Stand (2026-07-03 — Details/Live-Zahlen: `docs/scraping_status.md`)

| Kennzahl | Wert |
|---|---|
| **Gesamt-Partien** | **9.506.714** |
| **Spieler mit ≥ 1 gescrapter Periode** | **141.845** |
| **DB-Größe** | **~9,49 GB** |
| **Früheste Periode** | **2008-04** |
| **Neueste published_rating-Periode** | **2026-07-01** |
| **Neueste gescrapte Spiel-Periode** | **2026-06-01** (läuft über P1/P2/P3-Monatsrefresh nach) |
| **Gruppen complete (Mac-Mini-Analysegruppen)** | **107 / 253** |
| **global_XX ELO ≥ 2300** | **51/51 complete** ✅ |
| **female_2100–female_1800 (alle 55 Gruppen)** | **complete** ✅ (seit 2026-06-28) |
| **P1/P2/P3-Monatsrefresh** | P1 ✅ / P2 ✅ komplett, P3: 13/40 Batches |

### 5.1 Scraping-Status Kern-Gruppen

| Gruppe | Spieler | Gescrapt | Status |
|---|---|---|---|
| female_top | 23 aktiv | 2008-04 – 2026-04 | ℹ️ inaktive Spielerinnen → wenige ok-Perioden |
| male_control | 48 aktiv | 2008-04 – 2026-04 | ℹ️ Spieler weitgehend inaktiv |
| elite_2600 | 190 | 2008-04 – 2026-05 | ✅ complete |
| female_2200 | 207 | 2008-04 – 2026-05 | ✅ complete |
| swiss_2026 | 349 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_2200 | 112 | 2008-04 – 2026-04 | ✅ complete |
| global_02–28b | 56–250 | **2008-04 – 2026-04** | **✅ alle 51 Gruppen complete** |

> **FIDE-Perioden:** Quartalsweise 2008-04 – 2009-11, zweimonatlich 2010-01 – 2012-07,
> monatlich ab 2012-08. `is_valid_fide_period()` filtert automatisch.

### 5.2 Angereicherte Spalten in `game_results`

| Spalte | Migration | Befüllung | Beschreibung |
|---|---|---|---|
| `opponent_sex` | 007 | 98,1 % | Geschlecht des Gegners (M/F) |
| `tournament_type` | 007/008 | 100 % | `open`\|`women`\|`team`\|`women_team`\|`closed`\|`knockout` |
| `expected_score` | 009 | 99,2 % | Elo-Erwartungswert |
| `over_performance` | 009 | 99,2 % | result − expected_score |
| `opponent_match_quality` | 009 | 100 % | `ok`\|`wide_gap`\|`unresolved` |

### 5.2b Angereicherte Spalten in `scrape_periods`

| Spalte | Migration | Beschreibung |
|---|---|---|
| `no_data_reason` | 011 | `system_gap` \| `too_young` \| `inactive` — unterscheidet echte Inaktivität von strukturellen Lücken |

### 5.2c Views

| View | Migration | Beschreibung |
|---|---|---|
| `v_dynamic_membership` | 012 | Dynamische Gruppenzugehörigkeit pro (fide_id, period) basierend auf `published_rating` — Gegenstück zur statischen `players.analysis_group` |

### 5.3 TXT-Snapshot-Coverage

**195 Snapshot-Dateien** in `data/` — **Jan 2006 – Apr 2026**

| Zeitraum | Rhythmus | Abdeckung |
|---|---|---|
| 2006–2008 | quartalsweise (Jan/Apr/Jul/Okt) | ✅ (Jan 2007 fehlt) |
| 2009 | 5 Snapshots (Jan/Apr/Jul/Sep/Nov) | ✅ |
| 2010–2012 | unregelmässig, 3–9/Jahr | ✅ (einzelne Lücken) |
| 2013–2026-04 | vollständig monatlich | ✅ |

Parser-Fixes 2026-04-25/26: pre-2013-Format (kein `standard_`-Präfix, kein Sex/WTit),
`re.IGNORECASE`, Dedup für Doppeleinträge, Skip-Logik für bereits importierte Perioden.

### 5.4 Abgedeckte Backfills (chronologisch)

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
| 2010-01 → 2026-03 | female_2200 (321) | ✅ 2026-04-26 |
| 2009-01 → 2009-12 | alle 1.413 Spieler | ⏳ läuft (~6h, ETA 2026-04-26) |

---

## 6. QC-System

Datei: `scripts/quality_check.py`, Tabellen: `qc_rating_check`, `rating_corrections`

### 6.1 Methodik

**Monatliche Prüfung** (konsekutive Snapshot-Fenster):
```
expected_change = published_rating[T2] − published_rating[T1]
scraped_change  = SUM(rating_change_weighted) für T1 < period ≤ T2
correction      = SUM(rating_corrections.amount) für T1 < period ≤ T2
delta_adj       = (expected_change − scraped_change) − correction

Flag: ok (|Δ_adj| ≤ 5) | warn (≤ 15) | error (> 15)
```

**Jährliche Prüfung** (neu 2026-05-03, nur gescrapte Spieler, ab 2013):
```
annual_diff = ELO_Dez[Y-1] + Σ(game_Δ Jan–Dez[Y]) + Σ(corrections[Y]) − ELO_Dez[Y]
→ ok wenn |annual_diff| ≤ 3
```
Fängt Spiegel-Deltas auf: monatliche Timing-Verschiebungen die sich über das Jahr aufheben.

### 6.2 Ergebnisse (Stand 2026-05-03)

| Jahr | M-Fenster | M-OK% | M-Warn | M-Err | J-Spieler | J-OK% | J-Warn | J-Err |
|---|---|---|---|---|---|---|---|---|
| 2006 | 2.192 | 49,2% | 486 | 627 | — | — | — | — |
| 2007 | 2.338 | 39,9% | 628 | 777 | — | — | — | — |
| 2008 | 3.314 | 44,8% | 830 | 998 | — | — | — | — |
| 2009 | 4.323 | 99,8% | 8 | 2 | — | — | — | — |
| 2010–2012 | — | 99,8–99,9% | — | — | — | — | — | — |
| 2013 | 13.221 | 99,9% | 7 | 6 | 1.642 | 96,3% | 32 | 28 |
| 2014–2018 | — | 99,7–99,9% | — | — | — | 97–98% | — | — |
| 2019–2021 | — | 99,9–100% | — | — | — | 99,8–100% | — | — |
| 2022–2023 | — | 99,8–99,9% | — | — | — | 99,2–99,7% | — | — |
| 2024 | 25.647 | **99,7%** | 33 | 40 | 2.135 | 98,5% | 13 | 18 |
| 2025 | 16.866 | 99,9% | 11 | 4 | 2.139 | 99,9% | 1 | 1 |
| 2026 | 5.633 | 91,5% | 316 | 160 | — | — | — | — |

> **2006–2008:** Strukturell schlecht (quarterly Fenster, global-Gruppen ohne 2008-Scraping).
> **2009+:** Gut (kern-Gruppen vollständig gescrapt).
> **2024:** Von 95,0% auf 99,7% verbessert durch Jahres-Prüfsummen-Korrektur (Schwelle |Δ|≤3).
> **2026:** Nur bis März gescrapt → Apr-Fenster offen (91,5%).
> **Analyse-Empfehlung:** Nur Perioden ab 2009 (besser: ab 2013) verwenden.

### 6.3 Verbleibende Abweichungen

1. **2006–2008** — quarterly Fenster + global-Gruppen ohne Early-Scraping (strukturell)
2. **Spiegel-Deltas** — FIDE-Korrekturen über zwei Monate (Radzimski ±186)
3. **2026-04** — April 2026 noch nicht gescrapt
4. **2013–2018 Jahres-Δ** — 2–4% Jahresabweichungen bei schnell aufgestiegenen Spielern (K=40)

### 6.3a Ursachen-Kategorisierung (neu 2026-07-11)

Jedes non-ok-Fenster wird automatisch klassifiziert (`qc_rating_check.category`,
Migration 014; Regeln + Präzedenz: `scripts/quality_check.py::classify()`, läuft
nach jedem `run_qc()` automatisch mit):

| Kategorie (Präzedenz ↓) | Regel |
|---|---|
| `struktur_2008` | `period_start < 2009-01-01` |
| `fehlende_perioden` | `missing_periods > 0` |
| `spiegel_delta` | direkt benachbartes Fenster mit entgegengesetztem Δadj, beide ≥ warn, Paarsumme ≤ warn |
| `korrektur_rest` | `correction ≠ 0`, Residuum trotzdem über Schwelle |
| `k40_verdacht` | K=40-Monat (`scrape_periods.k_factor`) im Fenster |
| `unerklaert` | Rest — Kandidaten für genauere Analyse |

Erstklassifikation 2026-07-11 (5.150 non-ok-Fenster): 4.352 struktur_2008,
472 fehlende_perioden (fast alle 2026, offene Fenster), 150 spiegel_delta,
78 k40_verdacht, 15 korrektur_rest (März 2024), **83 unerklaert**.
OK-Fenster tragen `category = NULL`.

**QC-Dashboard** (Analytics-Frontend, Navbar-Gruppe „QC"):
- `/qc` QC Übersicht — KPIs (inkl. „Unerklärt"), Gruppen-Filter, Jahres→Monats-Drill-Down, **Ursachen nach Jahr** (Jahr × Kategorie)
- `/qc-cases` **QC Fälle** (neu) — filterbare Fall-Liste (Jahr/Kategorie/Flag/Gruppe/Föderation/Name) → Klick öffnet Spieler-Monatszerlegung (Publiziert ELO / Partien-Δ / Korrektur / Unerklärtes Δ / Kumulativ + Jahres-Prüfsumme; quartals-tauglich vor 2012 — generalisiert Notebooks 10/11)
- `/qc-corrections` FIDE 2024 Korrekturen

### 6.4 CLI

```bash
# Lokal via Tunnel:
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 -m scripts.quality_check [--rebuild] [--from-year YYYY] [--to-year YYYY]

# Jahres-Report ohne Neuberechnung:
python3 -m scripts.quality_check --report-only

# Nur Ursachen-Kategorien neu berechnen (ohne Fenster-Neuberechnung, schnell):
python3 -m scripts.quality_check --classify-only
```

**Naht für den Monats-Update-Prozess** (noch nicht automatisiert): nach dem
P1/P2/P3-Refresh `python3 -m scripts.quality_check --from-year <Jahr>` laufen
lassen — klassifiziert automatisch mit; `MAX(checked_at)` zeigt den letzten Lauf.

### 6.4a Datenprüfung ab Stichtag (`scripts/audit_data.py`, neu 2026-09-16)

Ergänzt QC und Reconciliation um eine Gesamtantwort: *Ist ab Stichtag alles da, und ergibt
die Elo-Entwicklung Sinn?* Maßstab ist die offizielle Liste: Soll = (Spieler, Periode) mit
`num_games > 0`, **auch heute inaktive Spieler**. Vier Ebenen (Referenz → Vollständigkeit →
Partien → Plausibilität), Logik in `orchestrator/audit.py`, läuft automatisch als Schritt 5
von `monthly_update.sh` (Bericht unter `~/backups/fide-scraper/audit/`).

```bash
python scripts/audit_data.py --since 2020-01 --report audit.md   # ~31 min
python scripts/audit_data.py --since 2026-01 --federation LIE    # ~1 min
```

**Aus den Daten abgeleitete FIDE-Regeln** (Migration 018, jeweils mit Test):

| Regel | Beleg |
|---|---|
| Δ je Partie = Ergebnis − E(Ro − Gegner), E aus der FIDE-Tabelle | 509.691 Partien, 0 Abweichungen; im Vollauf 62 von 7,4 Mio |
| 400-Punkte-Kappung nach **Turnierbeginn**: keine Kappung für Start 2022-01 … 2024-02 | 57.902 Partien mit \|Δ\| > 400, alle ungekappt; sonst zeigt FIDE das Gegnerrating bereits auf Ro ± 400 begrenzt |
| K ist jede ganze Zahl 1–40 (700er-Regel) und kann je Turnier abweichen | 393k Partien mit K 30/36/38 …; `K×Δ / Δ` ist immer ganzzahlig |
| März-2024-Korrektur = 0,4 × (2000 − (Vorliste + Σ Partien)) | schließt 96 % der Fenster; mit den gespeicherten `rating_corrections` nur 12 % |
| Kette schließt fachlich nicht immer | Reste fast nur bei K=40 und Ro ≠ Vorliste (nachgewertete Turniere) → Schwelle je Periode statt Einzelbefund |
| Liste und Berechnungsseite weichen bei FIDE selbst ab | Stichprobe: FIDE zeigt 7 statt gelisteter 8 Partien bzw. „No records" trotz Listeneintrag |

**Erster Vollauf 2020-01 bis 2026-08** (16.09.2026; der Bericht liegt nicht im Repo, ein neuer entsteht mit `--report`):

| Kennzahl | Wert |
|---|---:|
| Soll-Spieler / davon im Zeitraum mind. einmal gescrapt | 402.629 / 59,3 % |
| Soll-Kombinationen / gescrapt / vollständig | 3.335.249 / 75,7 % / 73,0 % |
| Partien vorhanden | 75,2 % (12,47 von 16,59 Mio) |
| Kette Liste ↔ Σ K×Δ | 91,0 % ok, 0,4 % unerklärt |
| Integritätschecks, K×Δ, Wertebereiche | 0 Befunde |

**Befunde daraus** (offen, siehe Abschnitt 9):
- ✅ **Liste 2024-06 war falsch importiert** (faktisch eine Juli-Fassung, 73 % identisch mit
  2024-07). **Behoben 17.09.2026:** echte `standard_jun24frl.zip` eingespielt (464.401 Zeilen =
  Datei; Juli-Ro passt jetzt zu 79 % statt 1,7 %). Mai und Juli 2024 waren korrekt.
  **Folgeschaden:** Der Pre-Filter in `worker.py::scrape_group()` hatte 22.830 Juni-Kombos mit
  laut falscher Liste 0 Partien ohne Abruf als `no_data` markiert. Die Zeilen sind gelöscht
  (Backup `~/backups/fide-scraper/manual/`), 12 GAP-Gruppen (`only_period`, Migration 019,
  `generate_period_repair_batches.py`) holen 25.453 Juni-Kombos über `dc_newplayers_1/2` nach.
  Der Ausreißer `game_count_fewer` 2024-07 kam von der falschen Liste (jetzt 0,8 %).
- **Liste 2026-03 ist korrekt**, enthält aber FIDE-seitig 16.538 Spieler, die weder im Februar
  noch im April stehen (fast alle 6 oder 8 Partien, Jahrgänge 2009–2014). Ursache unklar.
- **`rating_corrections` für 2024-03 veraltet:** Die `source='formula'`-Zeilen nutzen Ro statt
  des Ratings nach den Partien. Die Routine rechnet die Korrektur selbst nach, aber
  `quality_check.py` nutzt noch die gespeicherten Werte.
- **148.302 heute inaktive Spieler** mit Partien seit 2020 sind nie gescrapt worden — die
  Orchestrator-Population (System B) umfasst nur aktive Spieler. **Entscheidung 17.09.2026:
  werden nicht aufgenommen.** Dazu 15.433 aktive, davon ~12.600 aus der März-2026-Anomalie und
  433 mit Rating ≥ 1400.
- **Reaktivierte Spieler fallen durchs Raster:** `players.active` wird beim Monatsimport nie
  aufgefrischt; 2.293 bei uns inaktive Spieler haben Partien in der Liste 2026-09.
- `periods_listed_no_data` über 1 % in 2020-01, 2020-05, 2022-01 — Stichproben gegen FIDE stehen aus.

### 6.5 FIDE Einmalkorrektur März 2024 — Details

- **Beschlossen:** Dezember 2023 | **Wirksam:** 2024-03-01
- **Formel:** `+0,4 × (2000 − Post-Game-Rating)` für alle Spieler mit Rating < 2000
- **Verifikation (Viktor Guba, AUT, ID 1662279):**
  - Feb-2024-Rating: 1808 | Spielergebnis März: +12 → Post-Game: 1820
  - Korrektur: 0,4 × (2000 − 1820) = **+72** | März-Published: 1808+12+72 = **1892** ✓
- **Stichprobe (379.219 inaktive sub-2000-Spieler):** 87,7% exakter Match (Residual = 0)
- **Unsere Analysegruppen (≥ 2400):** nicht betroffen
- **swiss_2026:** 57 Spieler (Rating 1308–1996) betroffen; Ø Korrektur +61 ELO

### 6.6 Neue Notebooks (QC-Detail)

| Notebook | Inhalt |
|---|---|
| `10_qc_2024_detail.ipynb` | Pro Spieler: ELO Dez-23–Dez-24, Partien-Δ, unerklärtes Δ, Jahres-Prüfsumme |
| `11_qc_2008_detail.ipynb` | Pro Spieler: ELO Okt-07–Jan-09, Partien-Δ (Apr/Jul/Okt-08), Jahres-Prüfsumme |

### 6.7 Notebook 13 — Absoluter Elo-Vergleich female_top vs. male_control

Beantwortet direkt die Kernfrage „Ist eine Frau mit Elo X gegen einen gleich starken Mann
leichter/stärker/schwächer?" — bisher deckten Notebooks 01–06 nur Gruppenvergleiche ohne
Gegner-Geschlecht ab, Notebook 07 nur `female_top` intern (nach *relativer* Gegnerstärke,
nicht absolutem Rating).

- **Primärachse:** eigenes Rating in 50-Punkte-Bändern (nicht relative Differenz wie in nb07)
  — vergleicht `female_top` und `male_control` bei identischem absoluten Elo-Niveau.
- **±50-Elo-Schwelle als Projekt-Standard:** die relative Stärke-Bucket-Schwelle wurde auf ±50
  (wie Notebook 05) vereinheitlicht; Notebook 07 (±80) bleibt unverändert als historischer Stand.
- **Turniertyp:** nicht gefiltert — jede Kerntabelle wird zusätzlich für `open_mixed`
  (ohne `women`/`women_team`) parallel ausgewiesen, um den bekannten Bias (65,4 % der
  `female_top`-Partien gegen Frauen) sichtbar statt versteckt zu halten.
- **Signifikanztest:** Permutationstest auf Spieler-Ebene (nicht Partie-Ebene, wegen
  Cluster-Effekt durch ungleiche Partienzahl pro Spieler:in), 10.000 Permutationen, kein
  scipy/statsmodels nötig.
- **Stand bei Erstellung (2026-07-31):** `female_top`/`male_control` sind laut `groups`-Tabelle
  `backfill_status='partial'` — aktuell nur 23 von 66 bzw. 48 von 649 Spielern haben
  `analysis_group` gesetzt, mit `active=TRUE` bleiben nur 2+4. Das Notebook läuft damit
  fehlerfrei durch, zeigt aber noch sehr dünne/leere Zellen (856 Partien gesamt).
- **⚠️ Noch am selben Tag ersetzt durch Notebook 14** (siehe 6.8) — die Gruppen-Zuordnung
  erwies sich als methodisch schwächer als eine direkt aus `rating_history` abgeleitete
  Kohorte. Notebook 13 bleibt als Code/Referenz im Repo, ist aber als veraltet markiert.

### 6.8 Notebook 14 — Top-40-Frauen (Jahresende) vs. Männer im Elo-Band, dynamisch

Ersetzt Notebook 13. Statt der statischen, nur teilweise befüllten Gruppen
`female_top`/`male_control` wird die Studienkohorte direkt aus `rating_history.published_rating`
abgeleitet — survivorship-bias-frei, ohne Label-Pflege, ohne neues Scraping (Umsetzung der
Ideen **F2/F7** aus `docs/ideen_verbesserungen.md`).

- **`top40_female`:** Union aller Frauen, die an mind. einem Jahresende Dez-2016…Dez-2025 zu
  den Top 40 nach `published_rating` gehörten → **65 Spielerinnen** (Cutoff-Rating je Jahr
  ~2448–2460). 47 davon aktuell `active=TRUE`, 18 nicht mehr.
  52 von 65 haben bereits gescrapte Partien im Fenster 2016–2025 (27.059 Partien).
  **2026-08-03 auf Top 40/10 Jahre erweitert** (vorher Top 50/2021–2025, 70 Spielerinnen).
- **`male_2400_2600`:** alle Männer mit `std_rating` 2400–2600 zum jeweiligen Partie-Zeitpunkt
  (2016–2025), keine Top-40-Beschränkung (sonst wäre das eine andere Rating-Klasse, ~2650+).
  175.456 bereits gescrapte Partien, 587 distinkte Spieler — keine Nachscraping-Aktion nötig.
- Downstream-Logik (Elo-Band 50pt, Stärke-Bucket ±50, `scope` open_mixed/women_only,
  Permutationstest auf Spieler-Ebene) unverändert von Notebook 13 übernommen.
- **Warum diese Definition besser ist:** unabhängig von `players.analysis_group` und dessen
  Backfill-Status; jederzeit reproduzierbar per SQL-CTE ohne Datenbank-Schreibzugriff; deckt
  auch inzwischen aus dem Elo-Band gefallene/inaktive Spielerinnen ab (kein Survivorship-Bias
  wie beim April-2026-Snapshot).
- Notebooks 01–04 und 07 (basieren ebenfalls auf `female_top`/`male_control`) sind als
  methodisch überholt markiert (Hinweis-Banner in jedem Notebook), bleiben aber im Repo.

### 6.9 Notebook 15 — Gleichstarke & stärkere Partien je Spielerin (Top-40-Kohorte)

Pro Spielerin der Top-40-Jahresende-Kohorte aus Notebook 14 (siehe 6.8), über die ganze
Karriere seit 2008: Partien gegen ungefähr gleich starke Gegner (±50 Elo, Rating zum
Partie-Zeitpunkt) und separat gegen mindestens 50 Elo stärkere Gegner, jeweils
Siege/Remis/Niederlagen, Punktequote und Ø `rating_change_weighted`, aufgeschlüsselt nach
Gegner-Geschlecht. Zusätzlich eine Jahres-Übersicht der Ø-Gegner-Elo je Paarungs-Gruppe.

- Datenbasis: 55.973 Partien gesamt über alle 65 Spielerinnen (ganze Karriere, nicht nur
  2016–2025), davon 11.611 ±50-Elo-„gleich stark" und 12.084 ≥50-Elo-„stärker".
- Export: `top40_equal_strength_summary_per_player.csv`,
  `top40_stronger_opponent_summary_per_player.csv`,
  `top40_yearly_avg_opponent_rating_equal.csv`, `top40_yearly_avg_opponent_rating_stronger.csv`
  (alle in `notebooks/`).
- **Hinweis zur DB-Verbindung:** `notebooks/_setup.py::get_conn()` setzt seit 2026-08-03
  `max_parallel_workers_per_gather = 0` für jede Notebook-Session — der VPS-Postgres-Container
  hat ein sehr kleines `/dev/shm`, größere Ad-hoc-Joins wie in diesem Notebook liefen davor in
  `could not resize shared memory segment ... No space left on device`, sobald Postgres einen
  parallelen Hash-Join/Sort startete.

---

## 7. Scraping Orchestrator (deployed 2026-05-09, 10-Thread-Setup ab 2026-05-25)

Ein eigenständiges Tool zur Verwaltung des globalen Scrapings via Rotating-Proxy (aktuell Webshare, siehe 7.2 — providerneutral gebaut, Wechsel seit 2026-07-03 nur noch Config/Credentials).

### 7.1 Architektur

```
orchestrator/
├── app.py              ← Dash-Dashboard (7 Tabs, s. 7.5)
├── worker.py           ← Worker: Residential-Slots + DC-Threads unabhängig
├── queue_manager.py    ← SQLite-Queue, thread_affinity-Filter, Optimistic Locking
├── proxy_manager.py    ← Rotating-Proxy-Client (Pool-Modus: viele IP:PORT-Einträge + 1 Credential-Paar; oder Single-Host-Modus für Provider mit echtem Gateway)
├── profile_manager.py  ← Scrape-Profile + Fuzzy-Auswahl
├── generate_groups.py  ← 24.588 Gruppen (Föd. × Jahr × ELO-Band) generieren
├── setup_db.py         ← SQLite-Schema (inkl. thread_affinity-Spalte)
├── profiles.yaml       ← Profile + worker_slots + datacenter_threads (in git, Bind-Mount)
├── assets/bericht2.css ← Hover-CSS für Bericht-Länder-Tabelle
├── Dockerfile, docker-compose.yml, requirements.txt
```

### 7.2 Thread-Architektur (ab 2026-05-25, dc_dach + dc_update ab 2026-06-07)

**Bis zu 12 parallele Threads** — 2 Residential + 10 Datacenter:

```yaml
concurrency:
  worker_slots:                  # Residential: unabhängiges enabled-Flag pro Slot
    - {slot: 0, enabled: true, profile: semi_aggressive}    # T1
    - {slot: 1, enabled: true, profile: normal}             # T2
    - {slot: 2, enabled: false, profile: semi_conservative} # T3 (bereit)
    - {slot: 3, enabled: false, profile: semi_aggressive}   # T4 (bereit)
  datacenter_threads:            # 10 DC-Threads, pool_file (Webshare, regional) + eigener Timezone
    - {id: dc_de, slot: 99,  pool_file: orchestrator/webshare_proxies_europe.txt,    timezone: Europe/Berlin,     federations: [POL,UKR,LAT,LIT,EST,CZE,SVK,FID]}  # disabled
    - {id: dc_in, slot: 100, pool_file: orchestrator/webshare_proxies_mena_asia.txt, timezone: Asia/Kolkata,      federations: [IND,IRI]}
    - {id: dc_uk, slot: 101, pool_file: orchestrator/webshare_proxies_europe.txt,    timezone: Europe/London,     federations: [ENG,SCO,WLS,IRL,NIR,DEN,NOR,SWE,FIN,ISL]}
    - {id: dc_us, slot: 102, pool_file: orchestrator/webshare_proxies_americas.txt,  timezone: America/New_York,  federations: [USA,CAN]}  # disabled
    - {id: dc_hk, slot: 103, pool_file: orchestrator/webshare_proxies_mena_asia.txt, timezone: Asia/Hong_Kong,    federations: [CHN,VIE,...Ozeanien]}
    - {id: dc_es, slot: 104, pool_file: orchestrator/webshare_proxies_europe.txt,    timezone: Europe/Madrid,     federations: [ESP,ITA,POR,AND,GIB]}  # disabled
    - {id: dc_mx, slot: 105, pool_file: orchestrator/webshare_proxies_americas.txt,  timezone: America/Mexico_City, federations: [FRA,BEL,NED,LUX]}
    - {id: dc_ae, slot: 106, pool_file: orchestrator/webshare_proxies_mena_asia.txt, timezone: Asia/Dubai,        federations: [SRB,CRO,BIH,MKD,MNE,SLO,KOS,ALB,GRE,TUR]}
    - {id: dc_dach,     slot: 107, pool_file: orchestrator/webshare_proxies_europe.txt, timezone: Europe/Berlin, federations: [GER,SUI,AUT]}  # Vollbackfill
    - {id: dc_update_1, slot: 108, pool_file: orchestrator/webshare_proxies_europe.txt, timezone: Europe/Berlin, federations: []}     # P1/P2/P3-Monatsrefresh
```

**Proxy-Anbieter (seit 2026-07-03):** Webshare, statische 100-IP-Liste (`orchestrator/webshare_proxies.txt`, git-ignored) statt eines einzelnen Rotating-Gateways wie beim vorherigen Anbieter ProxyJet (Domain-Ausfall 2026-07-03). **Nach Region aufgeteilt** (GeoIP-Klassifizierung, `docs/scraping_orchestrator.md`): Europa (53 IPs, 5 Threads inkl. `dc_update_1`), Naher-Osten+Afrika+Asien-Ozeanien (19 IPs, 3 Threads), Amerikas (28 IPs, 2 Threads) — stellt die ursprüngliche Geo-Plausibilität (Zeitfenster passt zur IP-Region) wieder her, die beim reinen Einzel-Pool verloren gegangen war. `proxy_manager.py::ProxyManager` wählt pro Request zufällig eine `IP:PORT`-Kombination aus der jeweils zugewiesenen Datei. Details siehe `docs/scraping_orchestrator.md` Abschnitt "Proxy-Integration".

**P1/P2/P3 — monatlicher Refresh aller bereits gescrapten Spieler (seit 2026-07-02, ersetzt das frühere `dc_update`/4-UP-Jobs-System):**
Drei geschlechtsunabhängige Prioritätsstufen statt vormals 4 Kategorien (ELO2300/FEMALE/GER/DACH) + separatem `dc_update`-Rest:
- **P1** = ELO ≥ 2300 (alle Föderationen), **P2** = DACH (GER/SUI/AUT) < ELO 2300, **P3** = alle übrigen bereits gescrapten, aktiven Spieler (~118.000)
- `orchestrator/monthly_refresh_tiers.py`: Single Source of Truth für die drei Filter, von Batch-Generator und Worker importiert
- `orchestrator/generate_monthly_refresh_batches.py`: erzeugt ELO-Band-Batches gepoolt über alle Föderationen (Zielgröße ~2.000–3.000 Spieler/Batch), `thread_affinity='dc_update_1'`, `update_only=1` — Prioritätsreihenfolge P1→P2→P3, erst dann Batch-Größe
- `orchestrator/reset_monthly_refresh.py`: monatlicher Requeue nur für P1/P2/P3-Gruppen (federation-Sentinel), rührt den separaten Welt-Backfill nicht an — behebt den Vorgänger-Bug (`reset_current_year.py` setzte pauschal alle Gruppen des Jahres zurück)
- `update_only`-Spalte (unverändert seit 2026-06-07): filtert `get_fide_ids()` auf `EXISTS(scrape_periods WHERE status='ok')` — nie ein Vollbackfill neuer Spieler

**thread_affinity:** Jede SQLite-Gruppe ist einem DC-Thread zugewiesen (`dc_de`, `dc_in`, ...) oder
residential (`NULL`). DC-Threads claimen nur ihre eigenen Gruppen; Residential-Threads claimen
nur `thread_affinity IS NULL`.

**profiles.yaml — seit 2026-07-04 rein statisch (Review #4):** Die git-Version wird ins Image gebacken und ist die alleinige Quelle für Profile, fuzzy_weights und Thread-Topologie — Änderungen greifen per `git pull` + Rebuild, kein manuelles Nachziehen mehr. Alles zur Laufzeit Veränderliche (enabled/active_hours/max_hours pro Thread/Slot, active_profile) liegt in **`/data/runtime_settings.json`** (gemeinsames Volume, atomar geschrieben); UI-Toggles schreiben nur noch dorthin und überleben Rebuilds. Fehlende Einträge fallen auf die YAML-Defaults zurück. Das frühere `cp -n`-Seeding nach `/data/profiles.yaml` ist abgeschafft (Alt-Datei liegt als `/data/profiles.yaml.pre-review4.bak`).

### 7.3 DC-Modi

| Modus | Verhalten |
|---|---|
| **🤖 Automatisch** | ALLE DC-Threads mit Credentials starten; Timezone-Check entscheidet ob aktiv (07–23 Uhr Ortszeit) |
| **🖐 Individuell** | Nur `enabled=true` Threads starten; kein Timezone-Check; laufen 24/7 |

### 7.4 Scraping-Profile

| Profil | Wartezeit | Timeout | Einsatz |
|---|---|---|---|
| `semi_aggressive` | 2s (±35%) | 15s | T1 |
| `normal` | 3s (±40%) | 20s | T2 |
| `semi_conservative` | 5,5s (±45%) | 25s | alle DC-Threads |

> **VPS-IP geblockt:** Alle VPS-Requests laufen über den Proxy-Pool (Webshare). Mac Mini scrapt direkt.

### 7.5 Dashboard

```
https://scelo.chesspit.net   (BasicAuth: peter / persönliches PW)
```

| Tab | Inhalt |
|---|---|
| **🌍 Übersicht** | Föderations-Completion-Heatmap (ELO < 2300, alle Föderationen inkl. DC-ES/MX/AE) |
| **🗺️ Übersicht Land** | Federation×Jahr-Heatmap mit Click-to-Detail-Modal |
| **⚙️ Steuerung** | Metric-Cards; Residential-Karten (T1–T4 mit Toggle+Profil); DC-Karten (8× Toggle+Modus+Zeiten); Start/Stop/Neustart |
| **📋 Queue** | Pending-Gruppen; Thread-Spalte; Kategorie-Filter; DC-Sub-Dropdown |
| **✅ Abgeschlossen** | Erledigte Gruppen mit Statistiken + Thread (T1–T4/DC-XX) |
| **📊 Bericht Scraper** | Tägliches Datenvolumen pro Thread (T1–T4 + 8 DC-Slots); Zwischensummen Residential/DC als `% | MB`; Total-MB |
| **🗺 Bericht Länder** | Hierarchische Ländertabelle: Welt → Kontinent → „In Arbeit"/„Ohne Daten" → Land; Gruppen-%, Zeitraum, Spieler (gescraped/aktiv); aufklappbar per [+]/[−] |

### 7.6 Features

| Feature | Details |
|---|---|
| 24.588 Gruppen | Föd. × Jahr (2009–2026) × ELO-Band in SQLite |
| thread_affinity | DC-Thread-Zuweisung pro Gruppe (dc_de/in/uk/us/hk/es/mx/ae oder NULL) |
| 8 DC-Threads | Eigener Host, Credentials, Timezone, Föderationen je Thread |
| DC Auto-Modus | Alle Threads mit Credentials starten, Timezone entscheidet Aktivität |
| DC Individuell-Modus | Nur enabled=true Threads, 24/7, kein Timezone-Check |
| DC enabled-Flag live | `run_dc_slot()` prüft `enabled` zwischen Gruppen — Toggle wirkt ohne Neustart |
| Residential-Toggles | T1–T4 unabhängig ein/ausschaltbar (wie DC-Threads) |
| 🔄 Neustart-Alert | Zeigt aktive Konfiguration (N× Residential, DC-Threads, Modus+Zeiten) |
| DACH-Priorisierung | T1/T2 nur DACH 2020–2026; andere Föderationen auf P500000+ |
| Pre-Filter | ~55% skip-Rate via TXT-Snapshot (num_games=0) |
| Thread-Spalte | T1–T4 / DC-DE/IN/UK/US/HK/ES/MX/AE in Queue + Abgeschlossen |
| Atomische State-Writes | `worker_state.json` via `.tmp`-Rename (kein Truncation-Window bei Neustarts) |
| Startup-Grace | DC-Threads warten 1 s bei leerem State-File — verhindert Sofort-Stopp nach Neustart |

### 7.7 Vorfall 2026-07-14: FIDE-Endpoint-Umbenennung

FIDE hat am 14.07.2026 ~08:12 UTC die Ratings-Seite umgebaut: `a_indv_calculations.php`
→ **`a_indv_calculation.php`** (Singular). Die alte URL lieferte HTTP 200 mit leerem Body
(kein 404!), der Worker wertete das als `no_data` und lief ~33 h „erfolgreich" weiter:
~64k falsche `no_data`-Zeilen in `scrape_periods`, ~70 Gruppen fälschlich `done`
(„Fertig — 0 Partien").

Behoben 2026-07-15: URL-Fix in `scraper/fetcher.py` (Parameter/Format unverändert,
Parser kompatibel — neues Fixture `calc_1503014_2026-06-01.html`); falsche `no_data`
gelöscht (Abgrenzung zu legitimen Pre-Filter-Skips via `rating_history.num_games`),
Gruppen requeued. Neuer Guard in `worker.py::_fetch`: leere 200er zählen als
Fetch-Fehler (Circuit-Breaker), echte leere Perioden liefern seit dem Umbau den Text
„No records found". Zusätzlich Format-Frühwarnung, wenn ein Body weder `calc_table`
noch „No records found" enthält.

---

## 8. Spieler-Steckbrief (neu, 2026-05-11)

Interaktive Analyse-Seite pro Spieler in der bestehenden Frontend-App.

**Datei:** `frontend/pages/player_profile.py`
**URL:** `http://localhost:8050/player-profile`

### 8.1 Features

| Sektion | Inhalt |
|---|---|
| **Suche** | Name (min. 2 Zeichen, ILIKE) oder FIDE-ID — inline mit Spielerinfo |
| **Header** | Name, Elo, Alter, Titel, Föderation, Partien |
| **Rating-Verlauf** | Forward-fill auf Monatsraster (horizontal halten bis neuer Wert), alle Jahre auf X-Achse |
| **Partien-Chart** | Pro Quartal (Q1/Q2/Q3/Q4) + nach Q4 ein dunklerer Jahresgesamt-Balken mit Anzahl |
| **Filter** | 3 Gruppen: Jahrbereich+Alter / Farbe+Geschlecht / Gegner-ELO (1600–2700)+ELO-Abw. (±400) |
| **3×3 Matrix** | Zeile=Dimension (Spielstärke/Altersklasse/Farbe), Spalte=Metrik (Anzahl/Score%/Σ Δ Elo) |
| **M/F-Split** | Jeder Balken nach Männer/Frauen aufgeteilt (gestapelt bei Anzahl, gruppiert bei Score/Δ) |

### 8.2 Notebook 12 (Spieler-Steckbrief)

`notebooks/12_player_profile.ipynb` — ausgeführtes Beispiel für Peter Klings (FIDE 4631234).
Enthält zusätzlich QC-Zellen: Vergleich `Σ rating_change_weighted` mit tatsächlicher Rating-Änderung.

---

## 9. Offene Punkte / Nächste Schritte

*(aktualisiert 2026-07-03 — ältere Punkte aus Juni, die inzwischen erledigt sind, z.B. alle female_XX-Gruppen (siehe scraping_status.md), entfernt)*

| Aufgabe | Priorität | Status |
|---|---|---|
| P3-Monatsrefresh fertig laufen lassen | Hoch | 🔄 13/40 Batches (Stand 2026-07-03, ~17:20 UTC), läuft automatisch weiter |
| Webshare-Support: `166.88.110.0/24`-Subnetz melden | Mittel | ⬜ 4 von 5 ersetzten IPs landeten wieder im selben kaputten Block — gezielt das Subnetz melden statt Einzel-IPs |
| Restliche tote Pool-IPs ersetzen (~11 von 100) | Mittel | ⬜ `scripts/check_proxy_pool.py` zur erneuten Prüfung, danach Region-Datei + Worker-Neustart nicht vergessen |
| USA/2019/1599–1623-Gruppe (failed, unabhängig) | Niedrig | ⬜ `retries=0`, keine Fehlermeldung, `last_run_at` 2026-06-28 — Ursache noch nicht untersucht |
| Orchestrator Auto-Retry | Mittel | ⬜ failed-Gruppen nach X h automatisch → pending (max. 3×) — wurde am 2026-07-03 manuell nachgeholt (22 Gruppen geprüft, 21 zurückgesetzt) |
| PostgreSQL-Backup einrichten | Mittel | ⬜ aktuell kein externes Backup |
| Region-Pools bei Bedarf neu balancieren | Niedrig | ⬜ nur falls IP-Verteilung durch künftige Webshare-Käufe/Ersatz sehr ungleich wird |
| resolve_opponents nach Backfills | Mittel | ⬜ lokal, nach weltweitem Backfill-Fortschritt |
| Notebooks 01–09 ausführen | Mittel | ⬜ Daten bereit |
| Parquet-Export aktualisieren | Niedrig | ⬜ nach grösserem Backfill |
| female_top/male_control Update | Niedrig | ⬜ (inaktive Spieler, wenig Mehrwert) |

**Neu aus Session 2026-09-16** (die Tabelle darüber ist vom 03.07. und teilweise überholt —
bewusst nicht ungeprüft aufgeräumt):

| Aufgabe | Priorität | Status |
|---|---|---|
| Notebooks 10/11/13/14 regenerieren **und ausführen** | Mittel | ⬜ Generatoren geändert (gemeinsamer `elo_band`-Helper, NULL-Gruppe benannt), die committeten `.ipynb` haben aber noch die alten Zellen. Achtung: Regenerieren allein löscht die gespeicherten Ergebnisse |
| `quality_check.py --rebuild` — Folgen bedenken | Mittel | ⬜ Danach enthält `qc_rating_check` statt 2.150 die ~243.541 gescrapten Spieler. QC-Seiten haben jetzt einen Grundgesamtheit-Filter (Default „kuratiert"), Notebooks 10/11 zählen die NULL-Gruppe — beides vorbereitet, aber noch nie gegen die große Population gelaufen |
| Worker-Speicherwachstum beobachten | Niedrig | 👀 Seit dem P0-Fix (16.09.) stabil: Worker 70 → 84 MB in 7 Tagen ohne Neustart (`~/logs/memory_watch.log` auf dem VPS), Host 3,4 GB frei. Kein Leck erkennbar; der Spike vom 12.09. bleibt unerklärt, 4-GB-Limit + Monitoring bleiben. Folgeschaden vom 14.09. behoben: DC-Threads prüfen die DB-Verbindung jetzt vor jeder Gruppe (`run_dc_slot`), nicht erst nach der ersten Exception |
| Gruppen ohne Thread | Niedrig | ✅ 24.09.: 17 NON-Gruppen (1 Spieler) hatten keinen Thread, weil `set_backfill_targets.py` für den Kontinent „Other" keine Kandidaten kannte → 2021–2026 an `dc_newplayers_1`, 2009–2019 per Jahresziel skipped (2020 lag schon bei `dc_dach`); „Other" im Skript ergänzt |
| Coverage-Nenner: `players.active` vs. FIDE-Standardliste | Niedrig | ⬜ `store.py:397-414` begründet, warum die Standardliste der sauberere Nenner wäre; `players.active` driftet, weil der Monatsimport es für Bestandsspieler nie auffrischt. Bewusst nicht mitgeändert (sonst Zahlen vor/nach unvergleichbar) |
| Lücke unterhalb `ELO_FLOOR = 1400` | Niedrig | ⬜ Bänder 1000–1300 zeigen 0 gescrapte Spieler (271 aktive betroffen). Im Coverage-Tab jetzt sichtbar; Entscheidung, ob das Grid nach unten erweitert wird, steht aus |
| Migration 017 auf Test-/Zweitumgebungen anwenden | Niedrig | ⬜ Auf der Produktiv-DB angewendet. Die DB-gestützten Tests laufen nicht gegen den VPS (`permission denied to create database` für den `fide`-User) — lokal mit Wegwerf-PG und `ORCH_TEST_DATABASE_URL` laufen sie (2026-09-16: 240 grün, nur `test_retry_on_429` rot, schon vorher) |
| Liste 2024-06 neu importieren | Hoch | ✅ 17.09.2026 (6.4a). GAP-Gruppen fertig 19.09., Audit 2024-05…08 am 24.09. wiederholt: Juni 82,6 % gescrapt (besser als Mai–Aug), 2024-07 unauffällig (weniger Partien 3,6 → 0,8 %, Kette unerklärt 2,0 → 0,4 %) |
| `rating_corrections` 2024-03 für gescrapte Spieler neu berechnen | Mittel | ⬜ Formel mit Rating nach den Partien (6.4a); betrifft `quality_check.py`-Ergebnisse für das März-2024-Fenster |
| Inaktive Spieler seit 2020 in die Population aufnehmen? | Entscheidung | ✅ Nein (17.09.2026) |
| Reaktivierte Spieler erfassen | Mittel | ⬜ `players.active` beim Monatsimport aus dem Listen-Flag nachziehen (analog `sync_players_std_rating`); sonst werden wieder aktive Spieler nie gescrapt (2.293 Fälle in 2026-09). Ändert die Coverage-Zahlen |
| März-2026-Anomalie klären | Niedrig | ⬜ 16.538 Spieler nur in der FIDE-Liste 2026-03; Stichprobe auf ratings.fide.com (z. B. 525002783) |
| Stichproben 2020-01/2020-05/2022-01 (no_data) | Niedrig | ⬜ Periodenweise über der Schwelle, Ursache offen (2024-07 hat sich mit dem Juni-Neuimport erledigt) |

---

## 8. Bekannte Limitationen

### 8.1 Gegner-Auflösung

`resolve_opponents.py` arbeitet per Closest-Rating **ohne harte Toleranz**.
Fälle mit diff >200 (z.B. `Petrov, Nikita (RUS)`, 6 Kandidaten, bester Abstand 1017)
sind aufgelöst aber inhaltlich zweifelhaft. Identifizierbar per:
```sql
SELECT gr.opponent_name, gr.opponent_federation,
       gr.opponent_rating, p.std_rating,
       ABS(gr.opponent_rating - p.std_rating) AS diff
FROM game_results gr
JOIN players p ON p.fide_id = gr.opponent_fide_id
WHERE ABS(gr.opponent_rating - p.std_rating) > 200
ORDER BY diff DESC;
```
Überwiegend indische Spieler mit abweichender Schreibweise bleiben unresolved (~2,5 %).

### 8.2 Inaktive Spieler im Seed

Initialer Seed (2026-04-17) hat `active`-Flag nicht ausgelesen. Seitdem korrekt via
`--refresh-metadata`. Datensatz enthält daher:

| Gruppe | seeded | aktiv | inaktiv |
|--------|-------:|------:|--------:|
| female_top | 66 | 43 | 21 |
| male_control | 649 | 435 | 44 |
| elite_2600 | 202 | 153 | 49 |
| swiss_2026 | 349 | 338 | 11 |

**Konsequenz:** Alle Analysen nach `p.active = TRUE` filtern.
