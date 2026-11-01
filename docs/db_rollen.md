# DB-Rollen je Block (Phase 2a)

Stand 30.10.2026 · **vorbereitet, nicht angewendet.** Beschluss 18.10.2026, Plan:
`docs/architektur_vier_bloecke.md` Abschnitt 2.

Heute arbeiten alle Dienste als `fide` (Eigentümer von `fidedb`, volle Schreibrechte) —
auch das Frontend mit ARPAD. Ziel: jeder Block nur mit den Rechten, die er braucht.

| Rolle | Block | liest | schreibt | Dienste |
|---|---|---|---|---|
| `fide` | – | alles | alles + DDL | Migrationen, Backups (bleibt Eigentümer) |
| `fide_ingest` | 1 Scraper & Orchestrator | alles in `public` + `orchestrator` | Rohdaten (`players`, `game_results`, `scrape_periods`, `rating_history`, `rating_corrections`, `groups`), `orchestrator.*` — **nicht** `qc_*` | `worker`, `dashboard`, Monatslauf-Import, `resolve_opponents.py`, `backfill.py` |
| `fide_qc` | 4 Qualitätskontrolle | alles in `public` + `orchestrator` | nur `qc_rating_check`, `qc_audit_runs` (inkl. TRUNCATE) | `audit_data.py --store`, `quality_check.py`, `coverage_report.py`, `verify_scrape_integrity.py` |
| `fide_reader` | 2 ELO-Einsichten, 3 Analysen | `public` (Tabellen, Views, Funktionen) | nichts | `frontend` (intern), `frontend-public`, Notebooks/marimo, `scripts/analysis/` |

Migration: `migrations/022_db_roles.sql` (idempotent, Rollen entstehen `NOLOGIN`).
Die Rechte-Matrix ist in `tests/test_db_roles.py` festgeschrieben.

## Wie der Code die Rolle wählt

`core.config.get_database_url(role)` liest `DATABASE_URL_<ROLLE>` und fällt sonst auf
`DATABASE_URL` zurück. Solange keine Rollen-URL gesetzt ist, ändert sich also nichts.

| Aufruf | Rolle |
|---|---|
| `scraper/db.py::get_connection()` | `ingest` |
| `orchestrator/setup_db.py::connect()` | `DATABASE_URL` (Container setzt sie je Dienst) |
| `qc/db.py::connect()`, `qc/rating_check.py::connect()`, `qc/rating_views.py` | `qc` |
| `frontend/*` (`core.db.connect(role="reader")`) | `reader` |
| `core.db.connect_analysis()` (Notebooks) | `.env.notebook`, sonst `reader` |

`orchestrator/setup_db.py::ensure_schema()` überspringt seit Phase 2a das DDL, wenn
Tabellen und nachgerüstete Spalten schon stehen — sonst scheiterte `fide_ingest` (kein
Eigentümer) am `ALTER TABLE … ADD COLUMN IF NOT EXISTS`.

## Anwenden (Deploy-Fenster, lokal/VPS — nicht aus der Cloud-Session)

1. Migration als Superuser einspielen:
   ```bash
   docker exec -i fide-db psql -U postgres -d fidedb < /opt/fide-scraper/migrations/022_db_roles.sql
   ```
2. Passwörter setzen und Login freischalten (Werte nur in `/opt/fide-scraper/.env`):
   ```sql
   ALTER ROLE fide_reader LOGIN PASSWORD '…';
   ALTER ROLE fide_qc     LOGIN PASSWORD '…';
   ALTER ROLE fide_ingest LOGIN PASSWORD '…';
   ```
3. Dienste **einzeln** umstellen, jeweils Log prüfen, bevor der nächste folgt:
   1. Frontends: in `.env` `READER_DB_USER=fide_reader`, `READER_DB_PASSWORD=…`, dann
      `docker compose up -d frontend` (und ggf. `frontend-public`). Seiten durchklicken,
      ARPAD eine Frage stellen.
   2. Lokal (Mac): `.env.notebook` auf `fide_reader`; in `.env`
      `DATABASE_URL_QC=postgresql://fide_qc:…@localhost:5434/fidedb` → nächster
      `audit_data.py --store` schreibt als `fide_qc`.
   3. Block 1 zuletzt: `DATABASE_URL` von `dashboard`/`worker` in
      `deploy/infomaniak/docker-compose.yml` auf `fide_ingest` umstellen (eigene
      Variablen `INGEST_DB_USER`/`INGEST_DB_PASSWORD` anlegen), Worker-Log und Saves
      prüfen. Lokal (Mac) nutzen `import_rating_snapshots.py` und `resolve_opponents.py`
      `DATABASE_URL_INGEST`, sobald gesetzt; `monthly_update.sh` selbst reicht für
      `period_is_imported` und das Audit weiter `DATABASE_URL` durch.
4. Rückweg: Dienst wieder auf `fide` stellen; die Rollen schaden nicht.

**Neue Tabellen:** Lesen kommt über `ALTER DEFAULT PRIVILEGES` automatisch (für Tabellen,
die `fide` anlegt). Schreibrechte nicht — eine Migration mit neuer `qc_*`-Tabelle braucht
ein eigenes `GRANT … TO fide_qc`, eine neue Rohdaten-Tabelle ein `GRANT … TO fide_ingest`.
