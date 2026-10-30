# Übergabe: Vier-Blöcke-Umbau in einer Cloud-Session

Stand 30.10.2026. Plan und Zielbild: `docs/architektur_vier_bloecke.md` (Abschnitt 0 = Entscheide,
Abschnitt 3 = Phasen). Projektkontext: `CLAUDE.md`.

## Start-Prompt (in der Cloud-Session einfügen, Repo `Chesspit/fide-scraper`)

```
Setze den Vier-Blöcke-Umbau aus docs/architektur_vier_bloecke.md um.
Lies zuerst CLAUDE.md, docs/cloud_session_umbau.md (Spielregeln) und
docs/architektur_vier_bloecke.md (Abschnitt 0 Zielbild, Abschnitt 3 Phasen).

Arbeite auf dem Branch umbau/vier-bloecke, eine Phase = ein Commit (oder wenige),
in dieser Reihenfolge: 2 (core/), 3 (qc/), 3b (QC-Oberfläche im Orchestrator-
Dashboard), 4 (Block 1 aufräumen), 5 (zwei Frontend-Editionen), 6 (marimo),
7 (Import-Grenzen per pytest). Phase 2a (DB-Rollen) nur als Migration +
Doku vorbereiten, nicht anwenden.

Nach jeder Phase: komplette Testsuite gegen eine Wegwerf-Postgres (pgserver,
siehe Spielregeln) — Ergebnis darf nicht schlechter sein als die Baseline.
Pushe den Branch regelmäßig. Kein Merge nach master, kein Deploy.
Am Ende: Pull Request mit Phasenübersicht, Testergebnis je Phase und einer
Checkliste, was lokal noch zu tun ist (Deploy, Migrationen, DNS, Prüfungen).
Bei Unklarheiten im Zielbild: Annahme treffen, im PR unter "Annahmen" notieren.
```

## Spielregeln für die Cloud-Session

**Kein Zugang zur Produktion — und das ist gewollt.** Kein SSH-Key für den VPS, kein Tunnel,
keine `.env`. Daraus folgt:

- Nicht versuchen, den VPS, die Produktiv-DB, `scelo`/`elo`-Seiten oder ratings.fide.com zu
  erreichen. Keine Scrape-Requests (Heim-/Server-IPs dürfen nicht verbrannt werden).
- **November-Update gesperrt:** Liste 2026-11 nicht importieren, `monthly_update.sh` nicht
  ausführen, die Sperre (`~/HOLD_FIDE_MONTHLY_UPDATE` auf dem VPS) nicht anfassen. Nur der User gibt frei.
- Kein Merge nach `master`: Der VPS deployt per `git pull` von `master` (CLAUDE.md, Orchestrator-Deploy).
- Migrationen nur als Datei schreiben (nächste Nummer nach `migrations/020_*`), nicht anwenden.
- Notebooks/marimo: ohne DB nicht ausführbar. Überführen und mit `marimo check` bzw. Import-
  Test prüfen; Ausführung und Ergebnisvergleich passiert lokal. Bestehende `.ipynb` **nicht
  löschen und keine `_generate_*.py` laufen lassen** (löscht gespeicherte Ergebnisse).

**Tests.** Der `fide`-User fehlt, also Wegwerf-Postgres:

```bash
python3.12 -m venv .venv && .venv/bin/pip install -r scraper/requirements.txt pgserver \
  && .venv/bin/pip install -e . --no-deps
export ORCH_TEST_DATABASE_URL=$(.venv/bin/python -c \
  "import pgserver; print(pgserver.get_server('/tmp/pgdata', cleanup_mode=None).get_uri())")
.venv/bin/python -m pytest tests -q
```

Baseline vom 30.10.2026 (Python 3.12, pgserver): **263 passed, 1 failed** — der eine ist
`test_fetcher.py::test_retry_on_429`, schon länger bekannt, nicht Teil des Umbaus.
`tests/test_db.py` legt sich eine eigene DB mit **allen** Migrationen an (001…020 laufen auf
einer frischen PG fehlerfrei durch) — neue Migrationen müssen das weiterhin tun.
Frontend-/Dashboard-Requirements für Import-Tests zusätzlich aus `frontend/requirements.txt`
bzw. `orchestrator/requirements.txt` installieren.

**Grenzen, die nicht verhandelbar sind** (`architektur_vier_bloecke.md` Abschnitt 2):
`core` importiert keinen Block; jeder Block darf `core`; Frontend, Analysen und
Orchestrator-Dashboard dürfen `qc`; niemand importiert `frontend`. QC schreibt nie in
Rohdaten-Tabellen.

**Pfade, die stehen bleiben müssen:** `scripts/monthly_update.sh`, `scripts/tunnel.sh`,
Backup-Skripte, `scripts/audit_data.py` als CLI (launchd und Doku verweisen darauf);
Ordnernamen `scraper/`, `orchestrator/`, `frontend/`, `notebooks/`. Der Monatslauf ruft
`orchestrator/generate_list_batches.py` und `orchestrator/reset_monthly_refresh.py` im
Container auf — wenn sich Pfade ändern, `monthly_update.sh` mitziehen.

## Hinweise je Phase

- **2 `core/`:** DB-URL heute auf vier Wegen (`scraper/config.py`, `os.environ` im Frontend,
  `.env.notebook`-Loader, `data_arpad.py`) → `core.config.get_database_url()`. Periodenkalender
  (`is_valid_fide_period`, `valid_periods_for_year`), `elo_band` (heute `notebooks/_setup.py`),
  `fide_expected` (heute `orchestrator/audit.py`), **`latest_list_period` + Regel „steht in der
  neuesten Liste"** (heute `orchestrator/list_population.py`, seit 30.10. auch von `audit.py`
  genutzt). Dockerfiles: `COPY core/ pyproject.toml`, `pip install -e .`. Die rund 45
  `sys.path.insert`-Hacks dabei schrittweise entfernen.
- **3/3b QC:** `audit.py`, `integrity.py`, `coverage.py` nach `qc/`; Logik aus
  `quality_check.py`/`reconcile_ratings.py` dazu; Skripte in `scripts/` werden dünne CLIs.
  Neue Tabelle für Audit-Läufe (Kopfzahlen, Checks, by_year/by_federation, Stichproben als
  JSONB), `audit_data.py --store` o. ä. schreibt hinein, der Monatslauf nutzt das. QC-Bereich
  im Orchestrator-Dashboard (`orchestrator/app.py`, Dash) liest nur über `qc/`.
- **4:** `orchestrator/generate_groups.py` importiert `scripts.seed_players` — lösen, dann
  Orchestrator-Image ohne `scripts/`. System-A-Skripte nach `scripts/legacy/`.
- **5:** `frontend/app.py` hat heute Navbar-Gruppen Aktiv/Test/QC. Edition per Umgebungs-
  variable; Seiten nur bei passender Edition registrieren (Callbacks!). Compose
  (`deploy/infomaniak/docker-compose.yml`): zwei Services, öffentlicher Router ohne
  `fide-auth`, dafür `ratelimit`-Middleware; interner Router `intern-elo.chesspit.net` mit
  `fide-auth`. Ein Test, der für `EDITION=public` prüft, dass `/games`, `/arpad`, `/qc*`
  nicht in `dash.page_registry` stehen.
- **6 marimo:** `marimo` in die Analyse-Abhängigkeiten. Notebook 14 und 15 zuerst
  (`marimo convert notebooks/14_*.ipynb`), dann die übrigen nach Bedarf; gemeinsamer
  Zugang über `core`. Kurze Anleitung in CLAUDE.md (Notebooks-Abschnitt) nachziehen.
- **7:** pytest, der verbotene Import-Richtungen meldet (z. B. per `ast` über alle Module);
  CLAUDE.md-Dateistruktur aktualisieren.

## Was danach lokal passiert (nicht Cloud)

1. PR durchsehen, Branch lokal auschecken, Tests gegen lokale Wegwerf-PG.
2. Deploy-Fenster: Migrationen anwenden, Worker + Dashboard + Frontends neu bauen,
   Worker-Log und Saves prüfen (`docs/project_status.md`, Orchestrator-Deploy).
3. DNS `intern-elo.chesspit.net` anlegen; öffentliches `elo.chesspit.net` erst nach
   ausdrücklichem OK ohne Passwort schalten.
4. DB-Rollen (2a) anwenden und Dienste nacheinander umstellen.
5. marimo-Notebooks lokal ausführen, Ergebnisse mit den `.ipynb` vergleichen.
6. Erst danach: November-Update freigeben (Sperre entfernen) — nur auf Ansage des Users.
