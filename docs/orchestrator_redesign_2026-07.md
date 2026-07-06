# Orchestrator: Ist-Soll-Analyse und Redesign-Vorschlag

**Datum:** 2026-07-06 · **Branch:** `fable-test/orchestrator-redesign` · **Scope:** Fuzzy-Queue-Scheduler, Coverage, Vollständigkeitsprüfung

---

## 0. Einordnung: Der Aufgabenkontext ist teilweise überholt

Die Analyse-Anfrage beschreibt ein System mit „SQLite-Tracking, Caddy-Deployment, nie grundlegend überarbeitet“. Das war der Stand bis Anfang Juli 2026. Tatsächlich lief am 03./04.07. ein 11-Punkte-Architektur-Review (`review-elo-dashboard-2026-07-03.md`), dessen 10 umsetzbare Punkte **alle implementiert und deployed** sind:

| Damals bemängelt | Heute |
|---|---|
| Queue in SQLite (Docker-Volume, kein Backup) | PostgreSQL-Schema `orchestrator.*` in fidedb (Migration 013), im täglichen pg_dump enthalten |
| Failed-Gruppen bleiben stumm liegen | `requeue_failed()` mit retries<3-Deckel + 2h-Abstand + WARNING-Log |
| 429-Fallback von der FIDE-geblockten VPS-IP | `DIRECT_FALLBACK_ON_429=false` auf dem VPS |
| profiles.yaml-Split-Brain (Runtime-State in Config) | `runtime_settings.json` (atomar), profiles.yaml statisch/kommentierbar |
| app.py-Monolith, Datenzugriff untestbar | `store.py`/`state_io.py` extrahiert, Bucket-Logik getestet |
| Caddy-Erbe | gelöscht; real: Traefik/Coolify |

Diese Ist-Soll-Analyse setzt **danach** an: Was ist nach dem Review noch strukturell offen — gemessen an den vier Anforderungen Langlauf-Robustheit, Priorisierung, Rate-Limiting, Bulk vs. Monatsupdate?

## 1. Ist-Zustand (Kurzfassung)

- **Queue:** `orchestrator.scrape_groups` (~25.000 Zeilen: Federation × Jahr × ELO-Band) + `scrape_runs` (Historie). Claim per optimistischem `UPDATE … WHERE status='pending'` mit 5 Retries (`queue_manager.py:102-186`).
- **Scheduling:** `TIER_WIDTH=1` — die „Fuzzy-Queue“ ist seit 03.07. **offiziell deterministisch** (dokumentierte Entscheidung, `queue_manager.py:27-40`): strikte Prioritätsreihenfolge; Verschleierung leisten Timing-Jitter (`get_wait_time`, ±35–50 %) und `active_hours`-Fenster pro DC-Thread.
- **Threads:** bis zu 11 (2 Residential + 9 DC) in einem Worker-Prozess; DC-Threads föderationsgebunden über `thread_affinity`, mit eigenem Proxy-Pool, Timezone und Tageszeitfenster.
- **Betriebsmodi:** sauber koexistent in einer Tabelle — Welt-Backfill trägt echte Federation-Codes, der Monatsrefresh die Sentinels `P1`/`P2`/`P3` in der `federation`-Spalte (+ `update_only=1`); `reset_monthly_refresh.py` re-armiert monatlich nur die Sentinels.
- **Rate-Limiting:** profilbasiert (base_wait/jitter/min_wait/cooldown_on_429/max_retries), 429-Cooldown pausiert den Thread-Pool, Circuit-Breaker (15 Doppel-Fehlschläge) bricht Gruppen ab.
- **Priorisierung:** dichte Integer-Prioritäten, vergeben von den Generatoren; einzeln editierbar im Dashboard (`store.update_group_priority`), Bulk-Änderung nur per SQL.

## 2. Soll-Abgleich: die drei wichtigsten Schwachstellen

### (1) Completion ist behauptungs-, nicht evidenzbasiert — der False-Positive-Kanal

`mark_done()` feuert, sobald `scrape_group()` ohne Exception zurückkehrt (`worker.py:594/944/1099`) — auch bei `records_found=0`. Niemand prüft, ob die erwarteten `scrape_periods`-/`game_results`-Zeilen tatsächlich existieren. `sync_done_groups.py` gleicht nur **pending**-Gruppen gegen die Ground Truth ab, auditiert aber nie bereits-done-Zeilen.

Auf Perioden-Ebene ist es subtiler:
- `status='ok'` garantiert **keine** Partien-Zeile (leere-aber-geparste Perioden sind möglich).
- `status='error'`-Zeilen sowie `no_data` mit HTTP-Fehlerstatus (429/403) **blockieren dauerhaft jeden Retry**, weil `get_pending_periods()` (scraper/db.py) nur Kombos zurückgibt, die noch *gar nicht* in `scrape_periods` stehen. Ein einmaliger 429 im falschen Moment = stilles, permanentes Loch, das überall als „versucht“ zählt.

**→ Umgesetzt in diesem Branch:** `orchestrator/integrity.py` + `scripts/verify_scrape_integrity.py` — fünf benannte, unabhängige Checks (Registry-Muster, erweiterbar ohne Kern-Änderung): `ok_without_games`, `no_data_with_games`, `blocked_error_rows`, `done_groups_missing_combos` (auditiert done-Gruppen serverseitig, eine Query pro Jahr), `orphan_games`. Report-only, Severity hart/weich, Fix-Empfehlung pro Check, Exit-Code für Cron. Ergebnisse des ersten Live-Laufs: siehe §5.

### (2) Coverage-Blindheit entlang der Ground Truth

Sämtliches Dashboard-Reporting ist queue-getrieben; die einzige Berührung der echten Scrape-Daten ist `store.query_pg_players` (Federation-Aggregat). Es gab keine Möglichkeit zu beantworten: *„Wie vollständig ist female_top pro Jahr wirklich gescrapt?“* — `analysis_group` kam in keinem Report vor, `game_results` wurde von store.py nie abgefragt.

**→ Umgesetzt in diesem Branch:** `orchestrator/coverage.py` + `scripts/coverage_report.py` — ground-truth-basiert (players ⨯ scrape_periods ⨯ game_results), drei Dimensionen (Federation, analysis_group, ELO-Band) × Jahr, mit Soll-Perioden aus der kanonischen FIDE-Kalenderlogik (`is_valid_fide_period`). Kennzahlen: aktive Spieler, Spieler mit Daten, versuchte vs. erwartete Perioden, Partien.

### (3) Multi-Device-Queue ist nur für genau einen Worker sicher

`reset_stale_running()` setzt beim Worker-Start **global** alle running→pending (`queue_manager.py:319`, dokumentierter ACHTUNG-Kommentar). Sobald ein zweites Gerät (Mac Mini, Raspberry Pi) an die geteilte PG-Queue andockt, würde dessen Start die laufenden Gruppen des VPS-Workers zurücksetzen. Genau deshalb ist der Pi seit dem PG-Umzug abgehängt (Status-Sync gebrochen, Scraping läuft blind weiter).

**→ Vorschlag (Phase B, nicht implementiert):** `claimed_by`-Patch, drei kleine Änderungen:
1. `setup_db.ensure_schema`: `ALTER TABLE scrape_groups ADD COLUMN IF NOT EXISTS claimed_by TEXT` (idempotent, kein Migrationsskript nötig).
2. `_try_claim_next()`: Claim-UPDATE setzt `claimed_by = %s` (Gerätename aus Env, z. B. `WORKER_DEVICE_ID`, Default Hostname).
3. `reset_stale_running(claimed_by)`: `WHERE status='running' AND claimed_by = %s` — jedes Gerät räumt nur seine eigenen Leichen weg.

Danach kann der Pi direkt gegen die PG-Queue claimen (SSH-Tunnel existiert bereits) — die gesamte Export/Merge-Krücke bleibt Geschichte. Aufwand: ~2–3 h inkl. Tests (PG-Fixture existiert).

## 3. Nebenbefunde

| Befund | Einordnung |
|---|---|
| **Zwei Rate-Limiter-Implementierungen:** Orchestrator nutzt uniformen Jitter (`get_wait_time`); die Beta-Verteilungs-Pacing in `scraper/fetcher.py:103` (config.yaml „Beta-Verteilung“) nutzt nur der Standalone-Scraper (Mac-Mini-Backfills) | Kein Fehler, aber ein Doku-/Erwartungs-Gap: config.yaml suggeriert Beta-Pacing für „den Scraper“, der VPS-Orchestrator hat es nie benutzt. Vereinheitlichen oder dokumentieren (Phase D) |
| **Prioritäts-Änderung = SQL-Handarbeit** bei Bulk-Umordnung (dichte Ränge, Generatoren vergeben lückenlos) | Ein kleines CLI `scripts/requeue.py --federation X --year Y --to-front` (setzt `priority = MIN(priority)-1` bzw. Block-Shift) würde 90 % der Fälle abdecken (Phase D). Die Frage „wie sollte der Orchestrator idealerweise auf die Prioritäts-DB zugreifen?“ ist seit Review #5 beantwortet: Queue und Prioritäten liegen in PG, das Dashboard editiert einzeln — nur Bulk fehlt |
| **P1/P2/P3-Sentinels in der `federation`-Spalte** | Funktioniert kollisionfrei, ist aber implizit; bei nächster Schema-Änderung explizite `mode`-Spalte erwägen (rein kosmetisch, kein Handlungsdruck) |
| **Zukunftsmonate wurden historisch gescrapt** (bekanntes Memory-TODO) | Sichtbar im Coverage-Report als >100 % Perioden-Abdeckung (z. B. elite_2600/2026: 877/870). Der Report macht das Artefakt erstmals messbar |
| **generate_groups.py:94-98** verwirrender 2012-Perioden-Kommentar | Bei nächster Wartung glätten; Code-Verhalten ist korrekt (9 Perioden 2012) |

## 4. Redesign: Phasenplan

| Phase | Inhalt | Status |
|---|---|---|
| **A** | Integritätsprüfung (Ziel 3) + Coverage-Reporting (Ziel 2) als testbare Module + CLIs | ✅ dieser Branch |
| **B** | `claimed_by`-Patch (§2.3) → danach Pi direkt an PG-Queue | Vorschlag, ~2–3 h |
| **C** | Perioden-Retry-Semantik: `blocked_error_rows`-Findings altersbasiert löschen/requeuen — entweder Cleanup-Lauf auf Basis der Integritätsprüfung (sofort möglich, manuell) oder `get_pending_periods()` um „error älter als N Tage gilt als unversucht“ erweitern | Vorschlag |
| **D** | Optional: evidenzbasiertes `mark_done` (Soll-Kombo-Zählung vor done, = Check 4 inline), Dashboard-Tab auf Basis von coverage.py, Bulk-Prioritäts-CLI, Rate-Limiter-Vereinheitlichung | Vorschlag, nach Bedarf |

**Non-Goals (bewusst):** kein Quellen-Plugin-System für hypothetische zweite Datenquellen (Review-Punkt 11: spekulative Generalisierung); kein TIER_WIDTH-Revert (Determinismus ist seit 03.07. dokumentierte Design-Entscheidung, P1→P2→P3 hängt daran).

## 5. Erster Live-Lauf (2026-07-06, read-only via Tunnel)

**Coverage (Auszug analysis-group, 2020–2026):** elite_2600 durchgängig 100 % Perioden-Abdeckung, 93–100 % Spieler mit Daten pro Jahr; female_1800_XX-Gruppen 100 % Perioden-Abdeckung bei erwartbar niedrigerer Daten-Quote (52–98 % — niedrigeres ELO-Band = mehr partienlose Monate). Plausibel gegen die als komplett bekannten Gruppen.

**Integrität (vollständiger Lauf, ~17 Min):**

| Check | Findings | Interpretation |
|---|---|---|
| `ok_without_games` | **0** | Jede ok-Periode hat Partien-Zeilen — Transaktions-Design von `save_period` hält |
| `no_data_with_games` | **0** | Keine no_data-Behauptung widerspricht vorhandenen Partien |
| `blocked_error_rows` | **0** | Keine error-/429-Leichen in scrape_periods (aktuell!) — der Kanal existiert aber strukturell weiter, Phase C bleibt sinnvoll |
| `done_groups_missing_combos` | **294 weiche, 0 harte** | Alle weich (2–4 % fehlend); Stichprobe AUS/1653-1710 bestätigt Rating-Drift als Ursache: exakt 6 Spieler sind heute im Band, waren es zum Scrape-Zeitpunkt aber nicht (6 × 12 = 72 fehlende Kombos, deckungsgleich). Kein Scraping-Fehler, sondern erwartete Populations-Drift — und zugleich eine konkrete Nachscrape-Liste, falls 100 % gewünscht |
| `orphan_games` | **0** | Keine Partien ohne Tracking-Zeile |

**Lehre aus dem ersten Lauf (Check-Design selbst):** Der Erstlauf meldete 73 „harte“ Findings — allesamt die 73 alten `dc_update`-Batches (`XXX/2026/0-2299`, `update_only=1`), deren Soll-Population per Design nur bereits gescrapte Spieler umfasst. Der Check schließt `update_only`-Gruppen jetzt aus (rückwirkend nicht reproduzierbare Soll-Menge); Test `test_update_only_groups_excluded` dokumentiert den Fall.

**Gesamtbild:** Die Perioden-Buchführung ist vollständig konsistent — das strukturelle False-Positive-Risiko liegt heute ausschließlich auf Gruppen-Ebene und dort erklärbar durch Drift. Der kritischste theoretische Kanal (429/403-Zeilen, die Retries blockieren) ist aktuell leer, bleibt aber ohne Phase C offen für künftige Vorfälle.

---

*Analyse-Grundlage: 2 Explorations-Agenten über `orchestrator/` + `store.py`/`app.py`/Tests, Architektur-Review vom 03.07., Live-Läufe der neuen Tools. Code-Referenzen beziehen sich auf den Branch-Stand.*
