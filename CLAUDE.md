# FIDE Calculations Scraper

Scraper für FIDE-Calculations-Partien → PostgreSQL/TimescaleDB (VPS Hostinger).

**Ziel:** vollständige Partien-Datenbasis **aller bei der FIDE aktiven Spieler ab ca. 2020**.
Stand 16.09.2026: 243.555 aktive Spieler mit Rating > 0, davon 243.541 mindestens einmal
angefasst; Perioden-Abdeckung seit 2020 **76 %** (Dashboard-Tab „Abdeckung", oder
`scripts/coverage_report.py`).

*Historisch* begann das Projekt als enge Forschungsfrage (Top-Spielerinnen ELO 2400–2600 vs.
gleichstarke Männer). Die dafür kuratierten Gruppen (`female_top`/`male_control`) sind
eingefroren und nur teilweise befüllt — für den Frauen-vs-Männer-Vergleich gilt Notebook 14
mit seiner dynamisch aus `rating_history` abgeleiteten Kohorte (siehe Notebooks-Abschnitt
unten und `docs/project_status.md` 6.7/6.8).

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
├── migrations/                ← 001_initial.sql … 017_elo_band_function.sql
├── notebooks/                 ← 01–17 Analysen + notebooks/_generate_*.py (Generatoren)
├── scripts/
│   ├── seed_players.py        ← ⚠️ System A, eingefroren (s. „Gruppen: zwei Systeme")
│   ├── monthly_update.sh      ← FIDE-Liste laden + importieren + VPS-Requeue (täglich per launchd)
│   ├── coverage_report.py     ← Abdeckung je Föderation/Band/Jahr (CLI zum Dashboard-Tab)
│   ├── backfill.py            ← Historische Perioden nachladen
│   ├── run_local_backfill.sh  ← caffeinate + Auto-Restart + Tunnel-Check
│   ├── resolve_opponents.py   ← Gegner-FIDE-IDs per Lookup befüllen
│   ├── quality_check.py       ← QC gegen TXT-Snapshots → qc_rating_check
│   └── tunnel.sh              ← SSH-Tunnel localhost:5434 → VPS:5432
└── data/                      ← FIDE-Listen (gitignored); monthly_update.sh lädt hier ab
```

---

## Verbindung

| | |
|---|---|
| VPS | `pit@187.124.181.116`, `/opt/fide-scraper/` |
| DB lokal | `postgresql://fide:nimzo194.@localhost:5434/fidedb` |
| Tunnel starten | `bash scripts/tunnel.sh` |
| Orchestrator-Dashboard | https://scelo.chesspit.net (BasicAuth) — Steuerung, Queue, Tab „Abdeckung" |

**Achtung bei DB-Diagnosen:** Die produktiv genutzte DB läuft im Container
`fide-tunnelbliq-shared-db` (Port 5432, mit einem anderen Projekt geteilt), **nicht** in
`fide-scraper-db-1` (Port 5433). Der Tunnel auf lokal 5434 zeigt auf VPS-Port 5432, die
Angabe oben stimmt also — nur beim `docker logs` den richtigen Container erwischen.

---

## FIDE-Datenquelle

**AJAX-Endpoint** (nicht die `.phtml`-Seite!):
```
https://ratings.fide.com/a_indv_calculation.php?id_number={fide_id}&rating_period={YYYY-MM-01}&t=0
```
Seit FIDE-Umbau 2026-07-14 Singular (`calculation`); die alte Plural-URL liefert HTTP 200 mit **leerem Body** (kein 404). Pflicht-Header: `X-Requested-With: XMLHttpRequest`. Echte leere Perioden liefern den Text „No records found …".

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
| `players` | ~1,8 Mio FIDE-Spieler; `active` = FIDE-Status; `sex`; `analysis_group` = ⚠️ eingefroren, s. unten |
| `game_results` | Einzelpartien; UNIQUE `(fide_id, period, game_index)` |
| `scrape_periods` | Scraping-Status (ok/no_data/error) + k_factor pro (fide_id, period) |
| `rating_history` | Monatliches Rating: `std_rating` (Scraper) + `published_rating` (TXT) |
| `groups` | 175 kuratierte Scraping-Gruppen — ⚠️ eingefroren (System A, s. unten) |
| `rating_corrections` | FIDE-Einmalkorrekturen (März 2024: +0,4×(2000−rating) für <2000er) |
| `qc_rating_check` | QC-Fenster-Ergebnisse |
| `orchestrator.scrape_groups` / `.scrape_runs` | Orchestrator-Queue (seit Review #5 in PG statt SQLite scraper.db; Migration 013) |

**SQL-Funktionen** (Migration 017): `fn_elo_band(rating)` → Untergrenze des 50er-Bands,
`fn_elo_group(rating, sex)` → `f_2400_2449` / `m_1850_1899` / `x_…` bei unbekanntem Geschlecht.
Eine Quelle für Coverage, QC-Frontend und Notebooks; der pandas-Zwilling liegt in
`notebooks/_setup.py::elo_band()`, `tests/test_elo_bands.py` prüft beide gegeneinander.

Schlüsselentscheide: `game_index` löst Duplikate bei Doppelrunden; `opponent_fide_id` per nachträglichem Lookup (kein ID in AJAX-Response); beide `rating_change`-Felder gespeichert (ungewichtet + K×Δ).

---

## Gruppen: zwei Systeme, nur eines davon lebt

Leicht zu verwechseln — die beiden hängen **nicht** zusammen und wissen nichts voneinander.

| | System A (eingefroren) | System B (aktiv) |
|---|---|---|
| Wo | `groups`-Tabelle + `players.analysis_group` | `orchestrator.scrape_groups` |
| Befüllt durch | `scripts/seed_players.py`, **manuell pro Gruppe** | `orchestrator/generate_groups.py`, automatisch |
| Umfang | 175 kuratierte Gruppen, ELO-Stand April 2026 | alle Föderationen × 2009–2026 × ELO-Bänder ab 1400 |
| Wächst mit neuen Spielern | **nein** | ja (P0-Tier, monatlich via `monthly_update.sh`) |

System B leistet die eigentliche Arbeit. `worker.py::get_fide_ids()` fragt `players` live nach
Föderation + `std_rating` ab und fasst `analysis_group` nie an.

**System A nicht fortführen.** Die kuratierten Gruppen sind laut `docs/project_status.md` 6.7
nur teilweise befüllt (`backfill_status='partial'`): `female_top` 23 von 66, `male_control` 48
von 649 gelabelt — mit `active = TRUE` bleiben davon 2 bzw. 4. Sie bleiben als
Methoden-Dokumentation und Vergleichsachse erhalten (`coverage_by_analysis_group`), taugen
aber nicht als Analysegrundlage. Wer eine Kohorte braucht, leitet sie dynamisch aus
`rating_history` ab — Vorbild: Notebook 14.

<details>
<summary>Historischer Workflow „neue Gruppe starten" (System A, nur noch Referenz)</summary>

```bash
python scripts/seed_players.py --group GRUPPENNAME
bash scripts/run_local_backfill.sh GRUPPENNAME 2012-08-01 2026-03-01
```
```sql
UPDATE groups SET backfill_status='complete', scraped_from='2012-08-01', scraped_to='2026-03-01'
WHERE group_name='GRUPPENNAME';
```
</details>

---

## Monatslauf (automatisch)

`scripts/monthly_update.sh` läuft **täglich** per launchd auf dem Mac
(`scripts/net.chesspit.fide-monthly-update.plist`), nicht monatlich: FIDE veröffentlicht die
neue Liste nicht an einem festen Kalendertag.

1. Fehlende Perioden der letzten 3 Monate ermitteln (`FIDE_LOOKBACK_MONTHS`, älteste zuerst)
2. Liste von `ratings.fide.com/download/standard_<mmm><yy>frl.zip` laden (404 = noch nicht da → Exit 0)
3. Importieren
4. **Nur wenn wirklich importiert wurde:** P1/P2/P3-Refresh + P0-Neuzugänge auf dem VPS requeuen

Ohne Schritt 4 werden neue Spieler nie nachgezogen. Log: `~/backups/fide-scraper/monthly.log`.

⚠️ **Der launchd-Job gehört auf genau EIN Gerät** (aktuell: Mac Mini). Der Lock liegt unter
`/tmp` und wirkt nur lokal — zwei Rechner mit demselben Job würden sich gegenseitig nicht
sehen und beide den VPS-Requeue auslösen. Auf einem zweiten Gerät das Skript nur manuell
aufrufen, die `.plist` dort **nicht** installieren.

---

## Zweites Gerät einrichten (z. B. MacBook Pro)

Repo klonen bzw. `git pull` reicht nicht — vier Dinge liegen bewusst nicht im Git:

| Was | Wie |
|---|---|
| `.env` | aus `.env.example` erzeugen (DB_PASSWORD, DATABASE_URL, Proxy-Credentials) |
| `.env.notebook` | eine Zeile: `DATABASE_URL=postgresql://fide:…@localhost:5434/fidedb` (nur für Notebooks) |
| `.venv` | neu anlegen: `python3 -m venv .venv && .venv/bin/pip install -r scraper/requirements.txt` |
| SSH-Key für den VPS | sonst scheitern Tunnel und die VPS-Schritte in `monthly_update.sh` |

Danach `bash scripts/tunnel.sh` — ohne den Tunnel geht weder Notebook noch Import.

**Nicht mit übernehmen:** die launchd-Jobs (`net.chesspit.fide-monthly-update`,
`net.chesspit.fide-backup-pull`). Beide enthalten absolute Pfade unter
`/Users/macminipit/…` und sind als Single-Instance gedacht — siehe Warnung oben.

---

## ARPAD (Chatbot)

Chat-Q&A-Seite unter `/arpad` — Claude (Sonnet 5, Tool Runner) beantwortet Fragen zu den
gescrapten Rating-/Partiedaten über 4 feste, sichere Query-Tools (kein Text-to-SQL).

- Tool-Definitionen + Query-Logik: `frontend/data_chat_queries.py` (reine DB-Layer) +
  `frontend/data_arpad.py` (Anthropic-Client, System-Prompt, `answer_question()`).
- Braucht `ANTHROPIC_API_KEY` in `.env` (lädt selbst via `python-dotenv`, siehe
  `scraper/config.py`-Pattern — frontend lädt sonst kein `.env`).
- Kennt nur den gescrapten Kern-Datensatz (~14.000+ Analysegruppen-/Top-ELO-/Swiss-2026-
  Spieler mit `game_results`) — keine Scraping-Status-/Fortschritts-Fragen (out of scope).

---

## Gotchas

| Problem | Lösung |
|---|---|
| Docker auf VPS im Hintergrund | `docker compose run -T` (ohne TTY → sonst SIGTTOU-Absturz) |
| Tunnel-Drop beim lokalen Backfill | `db.py::ensure_connection()` 10× Retry bis 5 Min |
| Opponent-Match falsch (diff >200) | Query: `ABS(opponent_rating - std_rating) > 200` |
| VPS-IP von FIDE geblockt | Primär lokal scrapen via `run_local_backfill.sh` |
| `_generate_NN.py` ausführen löscht Notebook-Ergebnisse | Der Generator schreibt die `.ipynb` ohne Outputs neu (NB14 verlor so 1952 Zeilen Tabellen/Grafiken). Nur regenerieren, wenn danach auch ausgeführt wird |
| `std_rating = 0` heißt „unbewertet", nicht „schwach" | Betrifft 1,26 von 1,5 Mio aktiven Spielern. In Filtern immer `std_rating > 0` statt `IS NOT NULL` — sonst Phantom-Band 0 bzw. Millionen Phantom-Soll-Perioden |
| `COALESCE(...) AS x` + `SELECT DISTINCT` | Dann muss `ORDER BY` den **Alias** nutzen, nicht die Ursprungsspalte („ORDER BY expressions must appear in select list") |

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
| 13 | ⚠️ veraltet — s. Notebook 14 |
| 14 | Top-40-Frauen (Jahresende 2016–2025, dynamisch aus `rating_history`) vs. Männer im Elo-Band 2400–2600 × Gegner-Geschlecht × Turniertyp + Permutationstest |
| 15 | Pro Spielerin der Top-40-Kohorte: Partien/Ergebnis/Rating-Δ gegen gleich starke (±50) bzw. stärkere (≥50) Gegner, ganze Karriere, nach Gegner-Geschlecht |

Notebooks 01–04 und 07 basieren auf den Gruppen `female_top`/`male_control`
(`players.analysis_group`), die aktuell nur unvollständig befüllt sind — siehe
`docs/project_status.md` Abschnitt 6.7/6.8. Für den Frauen-vs-Männer-Vergleich Notebook 14
verwenden (dynamische Kohorte aus `rating_history.published_rating`, kein Scraping-Backfill
nötig).

Verbindung: `DATABASE_URL` aus `.env.notebook` → `localhost:5434`.
Generatoren (Quelle der Wahrheit): `notebooks/_generate_*.py`.

---

## Abhängigkeiten

Versionen in `scraper/requirements.txt`.
Kern: `requests`, `beautifulsoup4`, `psycopg2-binary`, `python-dotenv`, `pyyaml`,
`pandas`, `matplotlib`, `seaborn`, `jupyter`, `pytest`, `responses`.
