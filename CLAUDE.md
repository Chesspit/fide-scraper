# FIDE Calculations Scraper

Scraper für FIDE-Calculations-Partien → PostgreSQL/TimescaleDB (VPS Hostinger).
Analyseprojekt: Top-Spielerinnen (ELO 2400–2600) vs. gleichstarke Männer — Gegnerstruktur, Rating-Volatilität, Turnierfrequenz, Rating-Progression.

→ **Projektdokumentation:** [docs/project_status.md](docs/project_status.md)
→ **Scraping-Status:** [docs/scraping_status.md](docs/scraping_status.md)

---

## Dateistruktur

```
fide-scraper/
├── config.yaml                ← Scraper-Settings (rate limits, retry, Beta-Verteilung)
├── scraper/
│   ├── main.py                ← CLI: run / status
│   ├── fetcher.py             ← HTTP GET + Retry + Rate-Limiting
│   ├── parser.py              ← BeautifulSoup HTML-Parser
│   ├── db.py                  ← PostgreSQL UPSERT; ensure_connection(); is_valid_fide_period()
│   └── config.py              ← config.yaml + .env kombiniert
├── migrations/                ← 001_initial.sql … 012_dynamic_membership.sql
├── notebooks/                 ← 01–11 Analysen + notebooks/_generate_*.py (Generatoren)
├── scripts/
│   ├── seed_players.py        ← Spieler aus FIDE-TXT seeden (groups-Tabelle als Quelle)
│   ├── backfill.py            ← Historische Perioden nachladen
│   ├── run_local_backfill.sh  ← caffeinate + Auto-Restart + Tunnel-Check
│   ├── resolve_opponents.py   ← Gegner-FIDE-IDs per Lookup befüllen
│   ├── quality_check.py       ← QC gegen TXT-Snapshots → qc_rating_check
│   └── tunnel.sh              ← SSH-Tunnel localhost:5434 → VPS:5432
└── data/
    └── players_list_foa_2026-04.txt   ← FIDE-Download April 2026
```

---

## Verbindung

| | |
|---|---|
| VPS | `pit@187.124.181.116`, `/opt/fide-scraper/` |
| DB lokal | `postgresql://fide:nimzo194.@localhost:5434/fidedb` |
| Tunnel starten | `bash scripts/tunnel.sh` |

---

## FIDE-Datenquelle

**AJAX-Endpoint** (nicht die `.phtml`-Seite!):
```
https://ratings.fide.com/a_indv_calculations.php?id_number={fide_id}&rating_period={YYYY-MM-01}&t=0
```

Gültige Perioden: ab **2008-04-01**; monatlich erst ab **2012-08-01** (davor quartalsweise).
`db.py::is_valid_fide_period()` filtert strukturell leere Perioden automatisch.

---

## Parser: Spalten pro Partiezeile

`<table class="calc_table">`, Partiezeilen `<tr bgcolor=#efefef>`, Zellen `<td class=list4>`:

| Index | Inhalt | Hinweis |
|-------|--------|---------|
| 0 | Gegner-Name + Farbe-Span | CSS-Klasse `black_note`/`white_note` → color B/W |
| 1 | Titel (f/m/g/c) | Kleinbuchstaben: f=FM, m=IM, g=GM, c=CM |
| 2 | Frauen-Titel (wf/wm/wg) | |
| 3 | Gegner-Rating | `<font>`-Tags + `*` entfernen |
| 4 | Gegner-Föderation | 3-stellig |
| 5 | Ergebnis (1.00/0.50/0.00) | normalisieren auf 1/0.5/0 |
| 7 | rating_change ungewichtet | |
| 8 | K-Faktor (10/20/40) | |
| 9 | K × Change | |

Summary-Zeile `<tr bgcolor=#e6e6e6>`: Spalte 1 = **Ro** → `rating_history.std_rating`.

---

## DB-Tabellen

| Tabelle | Inhalt |
|---------|--------|
| `players` | ~1,8 Mio FIDE-Spieler; `analysis_group` für Analysegruppen; `active` = FIDE-Status |
| `game_results` | Einzelpartien; UNIQUE `(fide_id, period, game_index)` |
| `scrape_periods` | Scraping-Status (ok/no_data/error) + k_factor pro (fide_id, period) |
| `rating_history` | Monatliches Rating: `std_rating` (Scraper) + `published_rating` (TXT) |
| `groups` | 175 Scraping-Gruppen — **einzige Quelle der Wahrheit** für ELO-Range + Federation |
| `rating_corrections` | FIDE-Einmalkorrekturen (März 2024: +0,4×(2000−rating) für <2000er) |
| `qc_rating_check` | QC-Fenster-Ergebnisse |

**Wichtig:** Analysen immer nach `p.active = TRUE` filtern (21 inaktive female_top, 44 male_control).

Schlüsselentscheide: `game_index` löst Duplikate bei Doppelrunden; `opponent_fide_id` per nachträglichem Lookup (kein ID in AJAX-Response); beide `rating_change`-Felder gespeichert (ungewichtet + K×Δ).

---

## Workflow: neue Gruppe starten

```bash
# 1. Spieler seeden (liest ELO-Range + Federation aus groups-Tabelle):
python scripts/seed_players.py --group GRUPPENNAME

# 2. Backfill lokal (caffeinate, Auto-Restart, Tunnel-Check):
bash scripts/run_local_backfill.sh GRUPPENNAME 2012-08-01 2026-03-01
```

Danach groups-Tabelle aktualisieren:
```sql
UPDATE groups SET backfill_status='complete', scraped_from='2012-08-01', scraped_to='2026-03-01'
WHERE group_name='GRUPPENNAME';
```

---

## Gotchas

| Problem | Lösung |
|---|---|
| Docker auf VPS im Hintergrund | `docker compose run -T` (ohne TTY → sonst SIGTTOU-Absturz) |
| Tunnel-Drop beim lokalen Backfill | `db.py::ensure_connection()` 10× Retry bis 5 Min |
| Opponent-Match falsch (diff >200) | Query: `ABS(opponent_rating - std_rating) > 200` |
| VPS-IP von FIDE geblockt | Primär lokal scrapen via `run_local_backfill.sh` |

---

## Notebooks

| Notebook | Analyse |
|----------|---------|
| 01–04 | Gegnerstruktur, Rating-Volatilität, Turnierfrequenz, Rating-Progression |
| 05 | Σ rating_change_weighted pro Jahr; Splits nach Geschlecht/Farbe/Stärke-Bucket |
| 06 | Alters-Kohorten (Anker 2015): <20 / 20–30 / 30–40 / 40–50 / >50 |
| 07 | female_top Peer-Performance (Kohorte × Stärke-Bucket × Gegner-Geschlecht) |
| 08 | QC Elo-Analyse |
| 10–11 | QC-Detail 2024 / 2008 |

Verbindung: `DATABASE_URL` aus `.env.notebook` → `localhost:5434`.
Generatoren (Quelle der Wahrheit): `notebooks/_generate_*.py`.

---

## Abhängigkeiten

Versionen in `scraper/requirements.txt`.
Kern: `requests`, `beautifulsoup4`, `psycopg2-binary`, `python-dotenv`, `pyyaml`,
`pandas`, `matplotlib`, `seaborn`, `jupyter`, `pytest`, `responses`.
