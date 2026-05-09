# FIDE Scraper — Projektdokumentation

Stand: 30. April 2026

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
| 1 | 5 | 1.587 | Kern-Analysegruppen | partial/complete |
| 2 | 1 | 170 | male_2200 | partial (2013–2026) |
| 3 | 19 | ~2.800 | global_02–20 (weltweit ≥2345) | global_02 ✅, global_03 ⏳ |
| 4 | 8 | 1.163 | dach_01–08 (SUI+AUT+GER ≥2200) | pending |
| 5 | 40 | 5.988 | sui_01–20 + aut_01–20 (1400–2199) | pending |
| 6 | 86 | 12.948 | ger_01–86 (1400–2199) | pending |

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

## 5. Aktueller Datensatz-Stand (2026-05-09)

| Kennzahl | Wert |
|---|---|
| **Gesamt-Partien** | **1.793.298** |
| **Gegner aufgelöst** | **~97,5 %** |
| **Spieler mit Daten** | **2.508** |
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
| male_2200 | 170 | 2013-01 – 2026-04 | ✅ complete |
| global_02–11a | 720 | ELO 2407–2603 | ✅ complete (2012-08 – 2026-04) |
| global_11b–20b | — | ELO 2345–2411 | ⬜ pending |

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

## 7. Scraping Orchestrator (neu, 2026-05-09)

Ein eigenständiges Tool zur Verwaltung des globalen Scrapings via ProxyJet-Proxy.
Vollständige Doku: [`docs/scraping_orchestrator.md`](scraping_orchestrator.md)

### 7.1 Architektur

```
orchestrator/
├── app.py              ← Dash-Dashboard (3 Tabs: Heatmap / Queue / Abgeschlossen)
├── worker.py           ← Worker-Schleife (Queue → ProxyJet → PostgreSQL)
├── queue_manager.py    ← SQLite-Queue, Fuzzy-Scheduling, Optimistic Locking
├── proxy_manager.py    ← ProxyJet Rotating Residential Proxy
├── profile_manager.py  ← Scrape-Profile + Fuzzy-Auswahl (20/60/20)
├── generate_groups.py  ← 24.588 Gruppen (Föd. × Jahr × ELO-Band) generieren
├── setup_db.py         ← SQLite-Schema
├── profiles.yaml       ← conservative / normal / aggressive + fuzzy_weights
├── Dockerfile          ← Python 3.12 slim
├── docker-compose.yml  ← dashboard + worker Services
├── requirements.txt
└── caddy/Caddyfile     ← Reverse Proxy (aktuell nicht aktiv — SSH-Tunnel reicht)
```

### 7.2 Features

| Feature | Details |
|---|---|
| 24.588 Gruppen | Föd. × Jahr (2009–2026) × ELO-Band; Größen 50–250 (gewichtet 10/20/40/30%) |
| Fuzzy-Scheduling | Zufällig aus Top-50-Prioritäten; kein erkennbares Muster |
| Fuzzy-Profil | Zufällig gewichtet: 20% conservative, 60% normal, 20% aggressive |
| Gerät-Zuweisung | `device`-Spalte: mac_mini / raspi / vps — Worker filtert eigene Gruppen |
| Race Condition | Optimistic Locking: atomares SELECT+UPDATE, bis zu 5 Retries |
| Laufzeit-Limits | Max Gruppen + Max Stunden — einstellbar im Dashboard (Tab 1) |
| Dashboard | Auto-Refresh 10/15/30s; Priorität + Gerät + Profil per Klick editierbar |
| Deployment | VPS via Docker; SSH-Tunnel für Dashboard-Zugang |

### 7.3 Nächste Schritte Orchestrator

| Schritt | Priorität | Details |
|---|---|---|
| ProxyJet-Bandbreite kaufen | **Hoch** | 1 GB für ~1.000 Spieler ab 2009; Zugangsdaten bereits in `.env` |
| VPS-Deployment | **Hoch** | `git push && ssh VPS "cd /opt/fide-scraper && git pull && cd orchestrator && docker compose up -d --build"` |
| Netzwerkname prüfen | **Hoch** | `docker network ls \| grep fide` auf VPS → ggf. `DOCKER_NETWORK` in `.env` anpassen |
| DB auf VPS initialisieren | **Hoch** | `docker compose run --rm worker python orchestrator/generate_groups.py --db /data/scraper.db --txt data/players_list_foa_2026-04.txt` |
| Erster Test-Lauf | **Hoch** | Dashboard öffnen (SSH-Tunnel), Max Gruppen = 3, Start klicken |

---

## 8. Offene Punkte (bestehender Scraper)

| Aufgabe | Priorität | Status |
|---|---|---|
| global_11b–20b via Orchestrator | Hoch | ⬜ nach VPS-Deployment |
| male_2200 2008-04–2012-12 nachholen | Mittel | ⬜ Zeitraum noch nicht gescrapt |
| dach_01–08 seeden + starten | Mittel | ⬜ nach global-Gruppen |
| Kern-Gruppen 2008 nachholen | Mittel | ⬜ 3 Quartalsperioden fehlen |
| Notebooks 01–09 ausführen | Mittel | ⬜ Daten bereit |
| resolve_opponents nach jedem Backfill | Niedrig | ⬜ lokal oder VPS |
| Parquet-Export aktualisieren | Niedrig | ⬜ nach jedem grösseren Backfill |

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
