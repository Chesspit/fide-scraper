# FIDE Calculations Scraper

Scraper für FIDE-Calculations-Partien → PostgreSQL/TimescaleDB (VPS Infomaniak, seit 02.10.2026).

**Ziel:** vollständige Partien-Datenbasis **aller bei der FIDE aktiven Spieler ab ca. 2020**.
Stand 30.10.2026: **Backfill abgeschlossen** (Orchestrator-Queue leer, 20,6 Mio Partien).
Abdeckung gegen die offizielle Liste seit 2020 **≥ 99 % je Jahr** (`scripts/audit_data.py`);
2026 zeigt 97,7 % nur wegen ~12.900 von FIDE zurückgezogener März-Erstratings (ohne sie 99,8 %).
Neue Perioden kommen über den Monatslauf. Der Dashboard-Tab „Abdeckung" bzw.
`scripts/coverage_report.py` misst etwas anderes: alle Perioden der *heute aktiven* Spieler.

*Historisch* begann das Projekt als enge Forschungsfrage (Top-Spielerinnen ELO 2400–2600 vs.
gleichstarke Männer). Die dafür kuratierten Gruppen (`female_top`/`male_control`) sind
eingefroren und nur teilweise befüllt — für den Frauen-vs-Männer-Vergleich gilt Notebook 14
mit seiner dynamisch aus `rating_history` abgeleiteten Kohorte (siehe Notebooks-Abschnitt
unten und `docs/project_status.md` 6.7/6.8).

→ **Projektdokumentation:** [docs/project_status.md](docs/project_status.md)
→ **Scraping-Status:** [docs/scraping_status.md](docs/scraping_status.md)
→ **Umbau in vier Blöcke (Plan + Phasenstand):** [docs/architektur_vier_bloecke.md](docs/architektur_vier_bloecke.md)

---

## Dateistruktur

Vier Blöcke auf einer DB plus gemeinsames Fundament (`docs/architektur_vier_bloecke.md`).
Erlaubte Importe: jeder Block → `core`; Frontend, Analysen, Orchestrator-Dashboard → `qc`;
`core` importiert keinen Block, niemand importiert `frontend`. `tests/test_import_boundaries.py`
prüft das bei jedem Testlauf — neue Dateien dort einem Block zuordnen (`BLOCK_RULES`).

```
fide-scraper/
├── core/                      ← Fundament (importiert keinen Block)
│   ├── config.py              ← .env laden; get_database_url(role) — DATABASE_URL_<ROLLE> → DATABASE_URL
│   ├── db.py                  ← connect(), connect_analysis() (Notebooks, .env.notebook)
│   ├── periods.py             ← is_valid_fide_period, valid_periods_for_year, latest_list_period, Regel „neueste Liste"
│   ├── elo.py                 ← elo_band, fide_expected, diff_cap (Zwillinge der SQL-Funktionen 017/018)
│   └── fide_list.py           ← FIDE-Spielerliste (TXT/ZIP) parsen
├── config.yaml                ← Scraper-Settings (rate limits, retry, Beta-Verteilung)
├── scraper/                   ← Block 1: fetcher, parser, db (UPSERT, ensure_connection), main (CLI)
├── orchestrator/              ← Block 1: worker, queue/proxy/profile_manager, generate_*, reset_*
│   ├── app.py, store.py       ← Orchestrator-Dashboard (scelo), darf qc/ lesen
│   ├── qc_ui.py, qc_pages/    ← Dashboard-Tab „QC" (Audit-Läufe, Einzelabfrage, Rating-Fenster, Fälle, 2024)
│   └── check_proxy_pool.py    ← Gateway-Smoke-Test (läuft im Worker-Container)
├── qc/                        ← Block 4: audit, integrity, coverage, rating_check, reconcile,
│                                 rating_views, live (Spieler × Periode), audit_store (qc_audit_runs), db
├── frontend/                  ← Block 2: ELO-Einsichten, EDITION=public|intern (app.py → PAGES)
├── notebooks/                 ← Block 3: marimo (NN_*.py, Quelle der Wahrheit) + .ipynb-Archiv, _setup.py
├── deploy/infomaniak/         ← Produktions-Stack (Compose, bootstrap.sh, memory_watch.sh); Runbook docs/umzug_infomaniak.md
├── migrations/                ← 001_initial.sql … 022_db_roles.sql (022 vorbereitet, s. docs/db_rollen.md)
├── scripts/
│   ├── monthly_update.sh      ← FIDE-Liste laden + importieren + VPS-Requeue + Audit (täglich per launchd)
│   ├── import_rating_snapshots.py, backfill.py, resolve_opponents.py   ← Block 1
│   ├── audit_data.py          ← CLI zu qc/audit.py: Datenprüfung ab Stichtag (--store → Dashboard)
│   ├── coverage_report.py, quality_check.py, reconcile_ratings.py, verify_scrape_integrity.py ← CLIs zu qc/
│   ├── tunnel.sh              ← SSH-Tunnel localhost:5434 → VPS:5432
│   ├── analysis/              ← Block 3: Export-Skripte (Roster, Stärke-Karten, export_snapshot …)
│   └── legacy/                ← ⚠️ System A, eingefroren (seed_players, extend_male_control, run_*/chain_*)
└── data/                      ← FIDE-Listen (gitignored); monthly_update.sh lädt hier ab
```

---

## Verbindung

| | |
|---|---|
| VPS | Infomaniak „ELO“ `pit@179.237.106.106`, `/opt/fide-scraper/` (Host steht nur in `scripts/vps.env`) |
| Stack | `deploy/infomaniak/docker-compose.yml`: `fide-db`, `traefik`, `dashboard`, `worker`, `frontend` |
| DB lokal | `postgresql://fide:<DB_PASSWORD>@localhost:5434/fidedb` (Passwort nur in `.env`, Server-Wert in `/opt/fide-scraper/.env`) |
| Tunnel starten | `bash scripts/tunnel.sh` |
| Orchestrator-Dashboard | https://scelo.chesspit.net (BasicAuth) — Steuerung, Queue, Tabs „Abdeckung" und „QC" |
| Analyse-Frontend | https://elo.chesspit.net (gleiche BasicAuth) — ELO-Einsichten, Top 100, ARPAD. Nach dem Umbau-Deploy: `intern-elo.chesspit.net` (BasicAuth, alle Seiten) und `elo.chesspit.net` öffentlich (`frontend-public`, Compose-Profil `public`, erst nach OK des Users) |

DB-Container heißt `fide-db` (Port 5432 nur auf `127.0.0.1`), Diagnosen also
`docker compose -f /opt/fide-scraper/deploy/infomaniak/docker-compose.yml logs …` bzw. `docker exec fide-db psql -U fide -d fidedb`.

**Hostinger (`187.124.181.116`) gehört nicht mehr zum Projekt:** Am 20.10.2026 wurden alle ELO- und
tunnelbliq-Reste entfernt (Container, DB-Volumes, `/opt/fide-scraper`, Cron, Dumps). Dort laufen nur noch
Coolify, coolify-proxy, n8n und andere Projekte — nichts davon anfassen. Umzug + Rückbau: `docs/umzug_infomaniak.md`.

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
Eine Quelle für Coverage, QC und Notebooks; der Python-Zwilling liegt in
`core/elo.py::elo_band()` (re-exportiert von `notebooks/_setup.py`), `tests/test_elo_bands.py` prüft beide gegeneinander.

Migration 018: `fn_fide_expected(diff)` → FIDE-Erwartungswert (Tabelle, nicht gerundete
Normalverteilung), `fn_fide_diff_cap(period)` → 400 bzw. NULL (keine Kappung 2022-02 bis
2024-03, aus den Daten abgeleitet). Python-Zwilling in `core/elo.py`.

Schlüsselentscheide: `game_index` löst Duplikate bei Doppelrunden; `opponent_fide_id` per nachträglichem Lookup (kein ID in AJAX-Response); beide `rating_change`-Felder gespeichert (ungewichtet + K×Δ).

---

## Gruppen: zwei Systeme, nur eines davon lebt

Leicht zu verwechseln — die beiden hängen **nicht** zusammen und wissen nichts voneinander.

| | System A (eingefroren) | System B (aktiv) |
|---|---|---|
| Wo | `groups`-Tabelle + `players.analysis_group` | `orchestrator.scrape_groups` |
| Befüllt durch | `scripts/legacy/seed_players.py`, **manuell pro Gruppe** | `orchestrator/generate_groups.py`, automatisch |
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
python scripts/legacy/seed_players.py --group GRUPPENNAME
bash scripts/legacy/run_local_backfill.sh GRUPPENNAME 2012-08-01 2026-03-01
```
```sql
UPDATE groups SET backfill_status='complete', scraped_from='2012-08-01', scraped_to='2026-03-01'
WHERE group_name='GRUPPENNAME';
```
</details>

---

## Datenprüfung (`scripts/audit_data.py`)

Frage: *Sind ab Stichtag alle Spieler erfasst, und ergibt die Elo-Entwicklung Sinn?* Maßstab ist
die **offizielle Liste**, nicht die Queue: Soll = jede Kombination (Spieler, Periode) mit
`rating_history.num_games > 0` — auch heute inaktive Spieler. Logik in `qc/audit.py`.
Mit `--store` landet ein Lauf in `qc_audit_runs` (Migration 021) und erscheint im Dashboard-Tab „QC"
(Trend, Drill-down, Stichproben); der Monatslauf speichert so jeden Lauf.
Ausnahme: ungescrapte Kombos von Spielern, die in der neuesten Liste nicht mehr mit Rating
stehen (von FIDE zurückgezogen), zählen nicht zum Soll, sondern als INFO `periods_withdrawn` —
dieselbe Regel wie bei den Listen-Gruppen (`orchestrator/list_population.py`).

```bash
python scripts/audit_data.py --since 2020-01 --report audit.md      # alles
python scripts/audit_data.py --since 2026-01 --federation LIE       # schnell, ein Land
```

| Ebene | Prüft | hart wenn |
|---|---|---|
| 0 | Liste je Periode vorhanden (sonst wird die Periode übersprungen) | Liste fehlt |
| 1 | Soll-Kombos gescrapt? Spieler im Zeitraum nie gescrapt? no_data trotz Liste? | > 0 |
| 2 | Partienzahl = `num_games`; Integritätschecks im Zeitraum | weniger Partien (außer Erstbewertung) |
| 3 | Kette `Liste[P] − Liste[vorher] = Σ K×Δ`, Elo-Formel je Partie, K×Δ, Wertebereiche | unerklärte Kette > 2 % je Periode, Formel > 0,1 % |

Die **Kette** schließt fachlich nicht immer (K=40 und Ro ≠ Vorliste bei nachgewerteten
Turnieren → „erklärt"), deshalb eine Schwelle statt Einzelbefunden. Die **Formel** ist
exakt (509.691 Partien, 0 Abweichungen), aber nur für Perioden mit genau einem Turnier
prüfbar, weil die Summary-Zeile ein einziges Ro liefert. Exit 1 bei harten Befunden;
solange der Backfill läuft, ist das der Normalzustand.

---

## Monatslauf (automatisch)

`scripts/monthly_update.sh` läuft **täglich** per launchd auf dem Mac
(`scripts/net.chesspit.fide-monthly-update.plist`), nicht monatlich: FIDE veröffentlicht die
neue Liste nicht an einem festen Kalendertag.

1. Fehlende Perioden der letzten 3 Monate ermitteln (`FIDE_LOOKBACK_MONTHS`, älteste zuerst)
2. Liste von `ratings.fide.com/download/standard_<mmm><yy>frl.zip` laden (404 = noch nicht da → Exit 0)
3. Importieren
4. **Nur wenn wirklich importiert wurde:** P1/P2/P3-Refresh requeuen + Listen-Gruppen für die neue
   Periode anlegen (`generate_list_batches.py --period`, im `worker`-Container; ersetzt seit 30.10.2026 P0)
5. **Nur nach Import:** Datenprüfung ab 2020 (`FIDE_AUDIT_SINCE`), Einzeiler ins Log, Bericht nach
   `~/backups/fide-scraper/audit/audit_<datum>.md`

Ohne Schritt 4 werden neue Spieler nie nachgezogen. Log: `~/backups/fide-scraper/monthly.log`.

**Anhalten:** Existiert auf dem VPS `~/HOLD_FIDE_MONTHLY_UPDATE`, endet jeder Lauf sofort (kein
Download/Import/Requeue); VPS nicht erreichbar ⇒ Lauf ebenfalls übersprungen. ⚠️ **Seit 30.10.2026
gesetzt:** November-Update erst nach explizitem OK des Users (Vier-Blöcke-Umbau geht vor).
Aufheben: `ssh pit@179.237.106.106 'rm ~/HOLD_FIDE_MONTHLY_UPDATE'`.

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
| `.venv` | neu anlegen: `python3 -m venv .venv && .venv/bin/pip install -r scraper/requirements.txt && .venv/bin/pip install -e . --no-deps` |
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
  `scraper/config.py`-Pattern — frontend lädt sonst kein `.env`). Auf dem Server setzt
  `ssh -t pit@179.237.106.106 ./set_anthropic_key.sh` ihn nach Prüfung gegen die API. Der Key braucht
  Workspace-Scope „Default“ — Organisations-Keys liefern HTTP 400 (fehlender `anthropic-workspace-id`).
- Kennt nur den gescrapten Kern-Datensatz (~14.000+ Analysegruppen-/Top-ELO-/Swiss-2026-
  Spieler mit `game_results`) — keine Scraping-Status-/Fortschritts-Fragen (out of scope).

---

## Gotchas

| Problem | Lösung |
|---|---|
| Docker auf VPS im Hintergrund | `docker compose run -T` (ohne TTY → sonst SIGTTOU-Absturz) |
| Tunnel-Drop beim lokalen Backfill | `db.py::ensure_connection()` 10× Retry bis 5 Min |
| Opponent-Match falsch (diff >200) | Query: `ABS(opponent_rating - std_rating) > 200` |
| VPS-IP von FIDE geblockt | Primär lokal scrapen via `scripts/legacy/run_local_backfill.sh` |
| `_generate_NN.py` ausführen löscht Notebook-Ergebnisse | Der Generator schreibt die `.ipynb` ohne Outputs neu (NB14 verlor so 1952 Zeilen Tabellen/Grafiken). Für 14/15 gilt die marimo-Fassung, die Generatoren brechen dort ohne `--force` ab |
| `ModuleNotFoundError: core`/`qc` lokal | Neue Top-Level-Pakete: `.venv/bin/pip install -e . --no-deps` erneut ausführen (editable Install kennt nur die Pakete vom Installationszeitpunkt) |
| Frontend zeigt `/games`, `/arpad` nicht | `EDITION` fehlt → öffentliche Edition. Lokal `EDITION=intern python frontend/app.py` |
| `std_rating = 0` heißt „unbewertet", nicht „schwach" | Betrifft 1,26 von 1,5 Mio aktiven Spielern. In Filtern immer `std_rating > 0` statt `IS NOT NULL` — sonst Phantom-Band 0 bzw. Millionen Phantom-Soll-Perioden |
| DB-Tests: `permission denied to create database` | Der `fide`-User darf `fide_orch_test` nicht anlegen. Lokale Wegwerf-PG nutzen und `ORCH_TEST_DATABASE_URL` setzen (z. B. `pip install pgserver` in einem Python-3.12-venv) |
| Fehlerhafte Liste → stiller Datenverlust | Der Pre-Filter in `worker.py::scrape_group()` markiert Kombos mit `num_games = 0` **ohne Abruf** als `no_data`. Nach einem Listen-Neuimport die falschen Zeilen löschen und mit `orchestrator/generate_period_repair_batches.py --period YYYY-MM-01` GAP-Gruppen (nur diese Periode) anlegen — Vorfall 2024-06, `docs/project_status.md` 6.4a |
| Dashboard zeigt „no available server“ | Das ist der alte Hostinger-Traefik — der Rechner löst `scelo` noch auf die alte IP auf. `/etc/hosts` prüfen (Mac Mini hatte dort einen festen Eintrag), sonst DNS-Cache leeren |
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

Verbindung: `DATABASE_URL` aus `.env.notebook` → `localhost:5434` (über `core.db.connect_analysis()`).

**marimo (seit Phase 6):** Notebooks als reine `.py`-Dateien, git-diffbar und reaktiv.
14 und 15 liegen als `notebooks/14_top40_female_vs_band_men.py` / `15_top40_equal_strength_summary.py`
vor, die `.ipynb` bleiben als Archiv, bis die marimo-Fassung lokal geprüft ist.

```bash
.venv/bin/marimo edit notebooks/14_top40_female_vs_band_men.py   # interaktiv
.venv/bin/marimo run  notebooks/15_top40_equal_strength_summary.py # als App
.venv/bin/marimo convert notebooks/NN_xyz.ipynb -o notebooks/NN_xyz.py   # weitere überführen
.venv/bin/marimo check notebooks/NN_xyz.py                       # danach; tests/test_marimo_notebooks.py prüft alle
```
Beim Überführen: `sys.path`-Hack und `display()` entfernen (`from _setup import …`, `mo.vstack`),
gemeinsame Helfer aus `_setup`/`core` statt Inline-Kopien. Übrige Notebooks: Generatoren
`notebooks/_generate_*.py` bleiben bis zur Überführung Quelle der Wahrheit.

---

## Abhängigkeiten

Versionen in `scraper/requirements.txt` (lokal: Scraper + Analysen inkl. `marimo`),
`orchestrator/requirements.txt` (Worker/Dashboard-Image), `frontend/requirements.txt`.
Kern: `requests`, `beautifulsoup4`, `psycopg2-binary`, `python-dotenv`, `pyyaml`,
`pandas`, `matplotlib`, `seaborn`, `jupyter`, `marimo`, `pytest`, `responses`.
Paketstruktur (`core`, `qc`, `scraper`, `orchestrator`, `scripts`) über `pyproject.toml`
+ `pip install -e . --no-deps`; keine `sys.path`-Hacks mehr.
