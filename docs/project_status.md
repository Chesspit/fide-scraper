# FIDE Scraper — Projektdokumentation

Stand: 21. Mai 2026 (Abend)

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
| Dashboard | `https://scelo.chesspit.net` (Caddy, BasicAuth) — siehe `orchestrator/caddy/Caddyfile` |
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
| 3 | 35 | ~3.900 | global_02–28b (weltweit ≥2300) | **41 complete** (02–23b), 24a läuft, 24b–28b pending |
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

## 5. Aktueller Datensatz-Stand (2026-05-21)

| Kennzahl | Wert |
|---|---|
| **Gesamt-Partien** | **2.829.049** |
| **Gegner aufgelöst** | **~97,5 %** |
| **Spieler mit Daten** | **15.208** |
| **Früheste Periode mit Daten** | **2008-04** |
| **Neueste Periode** | **2026-04** |

### 5.1 Scraping-Status Kern-Gruppen

| Gruppe | Spieler | Gescrapt | Status |
|---|---|---|---|
| female_top | 66 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_control | 649 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| elite_2600 | 202 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| female_2200 | 321 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| swiss_2026 | 349 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_2200 | 170 | — | ⛔ skipped (gestrichen 2026-05-18) |
| global_02–23b | 56–147 | ELO 2328–2603 | ✅ complete (41 Gruppen, 2012-08 – 2026-04) |
| global_24a | 90 | ELO 2325–2327 | 🔄 läuft (seit 2026-05-21) |
| global_24b–28b | — | ELO 2300–2324 | ⬜ pending (9 Gruppen) |

> **FIDE-Perioden:** `scraper/main.py` überspringt automatisch strukturell leere Monate
> (is_valid_fide_period): 2008 Apr/Jul/Okt, 2009 Jan/Apr/Jul/Sep/Nov,
> 2010–2012-07 zweimonatlich, ab 2012-08 monatlich.

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

### 6.4 CLI

```bash
# Lokal via Tunnel:
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 -m scripts.quality_check [--rebuild] [--from-year YYYY] [--to-year YYYY]

# Jahres-Report ohne Neuberechnung:
python3 -m scripts.quality_check --report-only
```

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

---

## 7. Scraping Orchestrator (deployed 2026-05-09, Parallel-Modus + DC-Thread 2026-05-21)

Ein eigenständiges Tool zur Verwaltung des globalen Scrapings via ProxyJet-Proxy.

### 7.1 Architektur

```
orchestrator/
├── app.py              ← Dash-Dashboard (3 Tabs: Heatmap / Queue / Abgeschlossen)
├── worker.py           ← Worker: sequentiell (max_workers=1) oder parallel (2–4 Threads)
├── queue_manager.py    ← SQLite-Queue, Fuzzy-Scheduling, Optimistic Locking
├── proxy_manager.py    ← ProxyJet Rotating Residential Proxy (thread-safe)
├── profile_manager.py  ← Scrape-Profile + Fuzzy-Auswahl
├── generate_groups.py  ← 24.588 Gruppen (Föd. × Jahr × ELO-Band) generieren
├── setup_db.py         ← SQLite-Schema
├── profiles.yaml       ← Profile + fuzzy_weights + [concurrency] (schreibbar via Dashboard)
├── assets/custom.css   ← Dash CSS-Fixes
├── Dockerfile          ← Python 3.12 slim
├── docker-compose.yml  ← dashboard + worker (restart: unless-stopped)
└── requirements.txt
```

### 7.2 Parallel-Modus (ab 2026-05-21)

Jeder Thread ist ein unabhängiger Mini-Worker mit eigener PG- und SQLite-Verbindung.
Konfiguration in `profiles.yaml [concurrency]`:

```yaml
concurrency:
  max_workers: 2          # 1=sequentiell, 2–4=parallel (max. 4)
  worker_profiles:
    - semi_aggressive     # T0
    - normal              # T1
    - semi_conservative   # T2
    - semi_aggressive     # T3
  datacenter:
    enabled: false        # DC-Thread ein/aus (Toggle im Dashboard)
    profile: semi_conservative
```

**Umschalten ohne SSH:** Dashboard → Threads-Dropdown (1×–4×) → 🔄 Neustart
- Worker schließt aktuelle Gruppen fertig, exitiert dann (sys.exit 0)
- Docker startet ihn automatisch neu mit neuer Konfiguration

**ProxyJet:** Eine einzige rotating-residential Konfiguration reicht für alle Threads.
Jeder parallele Request bekommt automatisch eine andere IP aus dem Pool.

### 7.2b Datacenter-Thread (ab 2026-05-21)

Optionaler zusätzlicher Thread via ProxyJet **Datacenter**-Proxy (günstiger als Residential).
Läuft als **Slot 99** unabhängig von den 1×–4× Residential-Threads.

| | Residential | Datacenter |
|---|---|---|
| Endpoint | eu.proxy-jet.io:1010 | eu.proxy-jet.io:1010 |
| Username | `…-resi-DE` | `…-dc-DE` |
| Kosten | höher | günstiger |
| Env-Variable | `PROXYJET_USERNAME` | `PROXYJET_DC_USERNAME` |

**Steuerung:** Dashboard Heatmap-Tab → **DC-Thread**-Toggle → 🔄 Neustart

Im Dashboard erscheint der DC-Thread als grauer **DC**-Badge in der Status-Anzeige (Queue-Tab) neben den farbigen T0/T1-Badges. Die Abgeschlossen-Tabelle zeigt ebenfalls `DC` in der Thread-Spalte.

### 7.3 Scraping-Profile

| Profil | Proxy | Wartezeit | Timeout | Einsatz |
|---|---|---|---|---|
| `semi_aggressive` | aktiv | 2s (±35%) | 15s | T0, T3 |
| `normal` | aktiv | 3s (±40%) | 20s | T1 |
| `semi_conservative` | aktiv | 5,5s (±45%) | 25s | T2, DC-Thread |
| `conservative` | aktiv | 8s (±50%) | 30s | Referenz (inaktiv) |

Fuzzy-Auswahl für Gruppen ohne explizites Profil: 60% semi_aggressive / 40% normal.

> **VPS-IP geblockt:** FIDE sperrt die VPS-IP dauerhaft. Alle VPS-Requests laufen
> über ProxyJet Residential-Proxy. Mac Mini scrapt direkt (kein Proxy nötig).

### 7.4 Dashboard

```
https://scelo.chesspit.net   (BasicAuth: peter / persönliches PW)
```

| Tab | Inhalt |
|---|---|
| **Heatmap** | Föderations-Grid; Worker-Steuerung (Start/Pause/Stop/🔄 Neustart); Threads-Dropdown (1×–4×); DC-Thread-Toggle |
| **Queue** | Pending/Running/Failed-Gruppen; Priorität + Gerät editierbar; Thread-Spalte; Worker-Status-Anzeige in Kopfzeile |
| **Abgeschlossen** | Erledigte Gruppen mit Statistiken + Thread-Spalte (T0/T1/DC) |

### 7.5 Features

| Feature | Details |
|---|---|
| 24.588 Gruppen | Föd. × Jahr (2009–2026) × ELO-Band in SQLite |
| Parallel-Modus | 2–4 Residential-Threads, jeder mit eigenem Profil und eigener Queue-Verbindung |
| DC-Thread | Optionaler Slot-99-Thread via Datacenter-Proxy (günstiger, semi_conservative) |
| Threads-Dropdown | 1×/2×/3×/4× — schreibt sofort in profiles.yaml, wirksam nach Neustart |
| DC-Toggle | Ein/Aus-Schalter im Dashboard — wirksam nach Neustart |
| 🔄 Neustart-Button | Sauberer Prozess-Exit → Docker-Neustart mit neuer Konfiguration |
| Pre-Filter | ~55% skip-Rate via TXT-Snapshot (num_games=0 → kein FIDE-Request) |
| Circuit Breaker | Nach 15 Doppel-Timeouts → Gruppe `failed`, Worker läuft weiter |
| Thread-Spalte Queue/Abgeschlossen | T0/T1/DC für laufende und abgeschlossene Gruppen |
| Status-Anzeige | Pro Thread nebeneinander: Badge + Profil + Gruppe + Fortschritt + Speed + ETA |

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

## 9. Offene Punkte

| Aufgabe | Priorität | Status |
|---|---|---|
| global_24a–28b via Mac Mini (ELO 2300–2327) | Hoch | 🔄 24a fertig ~20:13h, 24b morgen früh |
| DC-Thread Erstlauf beobachten | Hoch | ⏰ heute Abend ~20:45h (VPS-Neustart) |
| VPS auf 3 Threads hochschalten | Mittel | ⏰ Sonntag 2026-05-24 prüfen (Routine gesetzt) |
| April-Nachscrape (38 Spieler, global_09b/10a/17b) | Mittel | ⬜ nach global-Gruppen |
| dach_01–08 seeden + starten | Mittel | ⬜ nach global-Gruppen |
| Kern-Gruppen 2008 nachholen | Mittel | ⬜ 3 Quartalsperioden fehlen |
| Notebooks 01–09 ausführen | Mittel | ⬜ Daten bereit |
| resolve_opponents nach Backfills | Niedrig | ⬜ lokal oder VPS |
| Parquet-Export aktualisieren | Niedrig | ⬜ nach grösserem Backfill |

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
