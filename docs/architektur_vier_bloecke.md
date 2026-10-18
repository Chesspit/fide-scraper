# Architektur: vier Blöcke in einem Repo

Stand: 2026-10-18 · Kurzfassung und Entscheidungen: `docs/ideen_verbesserungen.md` Abschnitt G

Das Projekt wird logisch in vier Blöcke auf einer gemeinsamen DB gegliedert. Es bleibt **ein
Repo** (eine DB, ein Schema, ein Compose-Stack, ein Entwickler). Die Ordner `scraper/`,
`orchestrator/`, `frontend/` und `notebooks/` behalten ihre Namen, weil launchd-Jobs,
Docker-Build-Kontexte, `/opt/fide-scraper` und CLAUDE.md darauf verweisen. Neu kommen nur
`core/` und `qc/` hinzu.

---

## 1. Zuordnungs-Matrix (Ist-Stand)

Befund der Code-Analyse: Das Frontend ist im Code schon entkoppelt (importiert nichts aus
`scraper`/`orchestrator`, nur die DB). QC dagegen liegt im Orchestrator: `audit.py`,
`integrity.py`, `coverage.py` brauchen von dort nur den FIDE-Periodenkalender
(`valid_periods_for_year` in `sync_done_groups.py`, `is_valid_fide_period` in `scraper/db.py`).

| | **1 Scraper & Orchestrator** | **2 ELO-Einsichten** | **3 Analysen** | **4 Qualitätskontrolle** |
|---|---|---|---|---|
| **Code** | `scraper/*`; `orchestrator/` außer audit/coverage/integrity (worker, queue/proxy/profile_manager, store, app, generate_*, reset_*, reassign_*, sync_done_groups, monthly_refresh_tiers) | `frontend/app.py`, `data.py`, `data_percentiles/player_history/population/titles/top100.py`, Seiten `elo_c`, `elo_dist`, `game_detail`, `player_profile`, `title_evolution`, `arpad` (+ `data_arpad.py`, `data_chat_queries.py`) | `notebooks/` 01–07, 09, 12–17, `_setup.py`, `_generate_*.py` | `orchestrator/audit.py`, `integrity.py`, `coverage.py`; `frontend/data_qc.py` |
| **Skripte** | `monthly_update.sh`, `backfill.py`, `import_rating_snapshots.py`, `resolve_opponents.py`, `check_proxy_pool.py`, `tunnel.sh`, Backup-Skripte + plists. Eingefroren (System A): `seed_players.py`, `extend_male_control.py`, `run_*`/`chain_*`-Shells, `setup_raspi.sh` | – | `elo_distribution.py`, `export_female_roster.py`, `generate_*_export.py`, `generate_strength_methodology.py`, `generate_player_strength_cards.py`, `export_snapshot.py` (Root), `exports/` | `quality_check.py`, `reconcile_ratings.py`, `audit_data.py`, `verify_scrape_integrity.py`, `coverage_report.py` |
| **DB** | schreibt `players`, `game_results`, `scrape_periods`, `rating_history`, `rating_corrections`, `orchestrator.*`, `groups` (eingefroren) | nur lesen | nur lesen (Views aus 002/009) | liest Rohdaten, besitzt `qc_rating_check` (004/006/016); Funktionen aus 018 |
| **Deploy** | `db`, `traefik`, `dashboard`, `worker`; `orchestrator/Dockerfile` | `frontend`; `frontend/Dockerfile` | lokal (Tunnel + `.env.notebook`) | keine eigene Laufzeit: CLI im Monatslauf, Bibliothek in den Dashboards |
| **UI** | scelo.chesspit.net | elo.chesspit.net | Jupyter | QC-Seiten `qc_overview`/`qc_cases`/`qc_corrections` im Frontend; Tab „Abdeckung" im Orchestrator |
| **Tests** | fetcher, parser, db, queue_manager, proxy_manager, worker_fetch, runtime_settings, store, coverage_warmer, period_repair, sampling | – | – | audit, integrity, coverage, reconcile_ratings |
| **Notebooks** | – | – | siehe Code | 08, 10, 11 |

**Fundament `core/`** (kein fünfter Block, sondern das, was alle teilen):
- Konfiguration und DB-Verbindung (heute `scraper/config.py::get_database_url`)
- FIDE-Periodenkalender (`is_valid_fide_period`, `valid_periods_for_year`)
- Python-Zwillinge der SQL-Funktionen: `elo_band` (heute `notebooks/_setup.py`), `fide_expected` (heute `orchestrator/audit.py`)
- `migrations/`

### Abgrenzung der Qualitätskontrolle

- **QC ist Bibliothek plus CLI, keine eigene Oberfläche.** Prüflogik und QC-Abfragen liegen in
  `qc/`. QC-Seiten im Frontend, der Abdeckungs-Tab im Orchestrator und die Notebooks 08/10/11
  rufen `qc/` auf und enthalten kein eigenes QC-SQL. `frontend/data_qc.py` (732 Zeilen) wandert
  großteils nach `qc/`.
- **QC findet, Block 1 repariert.** QC schreibt nie in Rohdaten-Tabellen, nur in eigene
  Ergebnistabellen und Berichte. Reparaturen sind Aufträge an Block 1, z. B.
  `generate_period_repair_batches.py` (GAP-Gruppen) — das Skript bleibt im Orchestrator.
- **`rating_corrections` sind Referenzdaten**, keine QC-Ergebnisse: FIDE-Fakten, geschrieben von
  `scraper/db.py`. Gehört zu Block 1, QC liest.

---

## 2. Schnittstellen & Abhängigkeiten

```
 FIDE ──► [1 Scraper/Orchestrator] ──schreibt──► DB: Rohdaten + orchestrator.*
                ▲                                   │
                │ Reparaturauftrag (GAP-Gruppen)    ├──liest──► [4 QC] ──schreibt──► qc_* + Audit-Berichte
                └──────────── Befund ───────────────┤
                                                    ├──liest──► [2 ELO-Einsichten]  (zeigt QC-Ergebnisse über qc/)
                                                    └──liest──► [3 Analysen]        (QC-Status als Filter)
 alle Blöcke ──► core/ (Config, DB, Perioden, Elo-Funktionen, Migrationen)
```

**Erlaubte Import-Richtungen:** jeder Block → `core`; Frontend, Analysen und
Orchestrator-Dashboard → `qc`. `core` importiert keinen Block, niemand importiert `frontend`.

| Ressource | Heute | Ziel |
|---|---|---|
| DB-URL | vier Wege: `scraper.config` (dotenv), `os.environ` im Frontend, `.env.notebook`-Loader, eigenes `load_dotenv` in `data_arpad.py` | eine Stelle: `core.config.get_database_url()` |
| DB-Rechte | alle Blöcke als `fide` mit Schreibrechten, auch das Frontend mit ARPAD | `fide_ingest` (Rohdaten schreiben), `fide_qc` (lesen + `qc_*` schreiben), `fide_reader` (Frontend, Notebooks: nur lesen) — **beschlossen 18.10.** |
| `.env` | eine Datei für alles | bleibt eine Datei, Variablen nach Block gruppiert; Proxy-Zugangsdaten nur für den Worker, `ANTHROPIC_API_KEY` nur fürs Frontend |
| Importpfade | rund 45 Dateien mit `sys.path.insert` | `pyproject.toml` + `pip install -e .` (✅ Phase 1), danach Hacks schrittweise entfernen |
| Abhängigkeiten | `requirements.txt` je scraper/orchestrator/frontend; Notebooks nutzen die von scraper | je Block eine Datei bzw. Extras in `pyproject.toml` |
| Altlast | `orchestrator/generate_groups.py` importiert `scripts.seed_players` (System B hängt an System A), deshalb kopiert das Orchestrator-Image ganz `scripts/` | `load_players_from_file` nach `core/` bzw. `orchestrator/` |
| Migrationen | zentral | bleiben zentral, Kopfkommentar `-- owner: ingest/qc/analysis` |

---

## 3. Roadmap

Jede Phase ist ein eigener Commit, Tests bleiben grün, `git revert` ist der Rückweg.
Testbasis (18.10.2026, Wegwerf-PG via `pgserver`, siehe CLAUDE.md-Gotchas): **256 grün,
1 rot** (`test_retry_on_429`, schon länger bekannt).

**Reihenfolge:** Phasen mit Worker-/Dashboard-Rebuild (2, 3) erst nach Ende des Welt-Backfills
und nach dem November-Import, gebündelt in einem Deploy-Fenster. Der P0-Fix (neue/reaktivierte
Spieler) ist kein Teil dieses Umbaus und wird separat besprochen.

| Phase | Inhalt | Betriebsrisiko | Status |
|---|---|---|---|
| **0 Konsens + Altlast** | Abschnitt G in `ideen_verbesserungen.md`; tote Deploy-Dateien geprüft und entfernt (Root-`docker-compose.yml`, `docker-compose.vps.yml`, `scraper/Dockerfile`). Bleiben: `orchestrator/docker-compose.yml` (Hostinger-Rückfall), `profiles_pi.yaml` (Pi) | keins | ✅ 18.10. |
| **1 Packaging** | `pyproject.toml`, `pip install -e . --no-deps` in der lokalen `.venv`; Abhängigkeiten bleiben in den `requirements.txt`. Docker-Teil (`pip install -e .` in den Images) kommt mit Phase 2 | keins | ✅ 18.10. (lokal) |
| **2 `core/`** | `core/config.py`, `core/db.py`, `core/periods.py`, `core/elo.py`; Importe in einem Commit umstellen; Dockerfiles: `COPY core/` + `pyproject.toml`, `pip install -e .`; `test_elo_bands` gegen `core.elo` | mittel: Worker-Rebuild, danach Worker-Log und Saves prüfen | ⬜ |
| **2a DB-Rollen** | `fide_ingest` / `fide_qc` / `fide_reader` anlegen, Rechte vergeben, `.env` lokal + VPS, Dienste nacheinander umstellen | mittel: Rechte auf der Produktiv-DB | ⬜ |
| **3 `qc/`** | `audit.py`, `integrity.py`, `coverage.py` aus `orchestrator/` sowie Logik aus `quality_check.py`/`reconcile_ratings.py` nach `qc/`; Skripte in `scripts/` werden dünne CLIs; Abdeckungs-Tab importiert `qc.coverage`; Pfad in `monthly_update.sh` | mittel: Dashboard-Rebuild, Abdeckungs-Tab + nächster Monatslauf als Test | ⬜ |
| **4 Block 1 aufräumen** | `generate_groups` von `scripts.seed_players` lösen; Orchestrator-Image ohne `scripts/`; System-A-Skripte nach `scripts/legacy/` | gering | ⬜ |
| **5 Frontend** | `data_qc.py` auf `qc/`; QC-Seiten als eigene Navbar-Gruppe; Rolle `fide_reader` | gering | ⬜ |
| **6 Analysen** | `notebooks/_setup.py` nutzt `core`; Export-Skripte nach `scripts/analysis/`; Notebooks 08/10/11 über `qc/`. ⚠️ Generatoren nur neu laufen lassen, wenn danach auch ausgeführt wird | keins (lokal) | ⬜ |
| **7 Grenzen absichern** | pytest, der verbotene Import-Richtungen meldet; CLAUDE.md-Dateistruktur aktualisieren | keins | ⬜ |
| **8 optional** | getrennte Repos/Images, nur falls ein Block eigene Releases oder Mitarbeiter bekommt | – | – |

Shell-Einstiegspunkte, auf die launchd oder die Doku verweisen (`monthly_update.sh`,
`tunnel.sh`, Backup-Skripte), bleiben an ihrem Ort.
