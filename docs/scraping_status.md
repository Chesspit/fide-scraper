# Scraping-Status

Stand: 2026-07-04 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite, Live-Abfrage)
Raspberry-Pi-Stand aktualisiert: 2026-06-28, ~18:30 Uhr (Tailscale seitdem nicht erneut abgefragt)

---

## Gesamtstand DB (Live 2026-07-03, ~17:20 UTC)

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **9.506.714** |
| DB-Größe | **~9,49 GB** |
| Gruppen complete | **107 / 253** (140 pending, 5 partial, 1 skipped) — bezieht sich auf die manuell gepflegten Mac-Mini-Analysegruppen, unabhängig vom neuen P1/P2/P3-System (siehe unten) |
| Global-Gruppen complete | **51 / 51** — ELO ≥ 2300 weltweit vollständig ✅ (Vorbehalt: siehe Top-Spieler-Lückenanalyse unten) |
| Spieler mit ≥ 1 gescrapter Periode | **141.845** |
| Neueste published_rating-Periode | **2026-07-01** (importiert 2026-07-02, `standard_jul26frl.zip`) |
| Neueste gescrapte Spiel-Periode | **2026-06-01** (läuft über den P1/P2/P3-Prozess nach, siehe Session-Eintrag 2026-07-03) |
| P1/P2/P3-Fortschritt | P1 ✅ / P2 ✅ komplett; P3: 13/40 Batches, 48.045 neue Partien bisher |
| VPS-Orchestrator-Queue gesamt | 4.142 done, 20.564 pending, 7 running, **1 failed** (21 vom ProxyJet-Ausfall betroffene Gruppen am 2026-07-03 zurückgesetzt + priorisiert, siehe Session-Eintrag; 1 unabhängige Alt-Gruppe (USA/2019) bewusst nicht angefasst) |

---

## Top-Spieler-Lückenanalyse (ELO ≥ 2300, aktiv) — Stand 2026-07-01

Trotz "51/51 Global-Gruppen complete" sind **nicht 100 % aller Top-Spieler gescrapt**. Live-Abfrage gegen `players` (aktiv, `std_rating >= 2300`) vs. `game_results`:

| ELO-Bucket | Gesamt (aktiv) | Gescraped | Anteil |
|---|---:|---:|---:|
| 2800–2899 | 2 | 2 | 100 % |
| 2700–2799 | 30 | 29 | 96,7 % |
| 2600–2699 | 127 | 126 | 99,2 % |
| 2500–2599 | 455 | 442 | 97,1 % |
| 2400–2499 | 1.175 | 1.135 | 96,6 % |
| 2300–2399 | 2.571 | 2.455 | 95,5 % |
| **Gesamt** | **4.360** | **4.189** | **96,1 %** |

**171 Top-Spieler fehlen**, alle mit `analysis_group IS NULL` (nie regulär in einer `global_*`-Gruppe geseeded):

- **57 Spieler nie angefasst** — kein Seed, kein einziger Scrape-Versuch
- **114 Spieler** haben `scrape_periods`-Einträge (z. B. via Opponent-Resolution berührt), aber **kein Ergebnis** (`no_data`/Fehler)

Föderationsverteilung der fehlenden 171 (Top 5): **RUS (16), UKR (14), SRB (14), HUN (9), CRO (9)**. Teils historische/verstorbene Top-Spieler, die im `active`-Flag noch als aktiv geführt werden (z. B. Vugar Gashimov, 2737, gest. 2014).

**TODO:** Gruppe für die 171 (mind. die 57 nie angefassten) fehlenden Top-Spieler anlegen und seeden, um die 2300er-Range wirklich lückenlos zu machen.

---

## Kern-Gruppen (Priorität 1–2)

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| female_top | 23 | 2400–2600 (F, inaktiv) | 2008-04 – 2026-04 | ✅ complete |
| male_control | 48 | 2400–2600 (M, age-matched) | 2008-04 – 2026-04 | ✅ complete |
| elite_2600 | 190 | ≥ 2600 | 2008-04 – 2026-05 | ✅ complete |
| swiss_2026 | 349 | — (SMM 2026) | 2009-01 – 2026-04 | ✅ partial (2008 fehlt) |
| female_2200 | 207 | 2200–2399 (F) | 2008-04 – 2026-05 | ✅ complete |
| male_2200 | 112 | 2200–2399 (M) | 2008-04 – 2026-04 | ✅ complete |

---

## Global-Gruppen (Mac Mini Backfill) — alle complete ✅

**51 Gruppen (global_02 – global_28b), ELO 2300–2603, alle Föderationen**

Zeitraum gescrapt: **2008-04-01 – 2026-04-01** (inkl. Pre-2012-Quartalsperioden)

| Bereich | Gruppen | ELO-Range | Abgeschlossen |
|---------|--------:|-----------|---------------|
| global_02 – global_11b | 12 | 2412 – 2603 | bis 2026-05-09 |
| global_12a – global_19b | 16 | 2351 – 2411 | bis 2026-05-17 |
| global_20a – global_23b | 8 | 2328 – 2350 | 2026-05-18 – 2026-05-21 |
| global_24a – global_25b | 4 | 2317 – 2327 | 2026-05-22 |
| global_26a – global_28b | 6 | 2300 – 2316 | **2026-05-22** (Chain, ~19:17 Uhr) |

→ **ELO ≥ 2300 weltweit vollständig und lückenlos gescrapt.**

---

## Female-Gruppen (Mac Mini Backfill) — laufend 🔄

**55 Gruppen (female_2100_01 – female_1800_24), ELO 1800–2199, nur Spielerinnen (F)**

Zeitraum: **2010-01-01 – 2026-04-01** (inkl. Pre-2012-Quartalsperioden)
Reihenfolge: jüngste Periode zuerst → älteste; **vollautomatische Chain** via `run_female_chain.sh`.

| Bereich | Gruppen | Spielerinnen | ELO-Range | Status |
|---------|--------:|-------------:|-----------|--------|
| female_2100_01 – female_2100_06 | 6 | 395 | 2104–2199 | ✅ complete |
| female_2000_01 – female_2000_09 | 9 | 626 | 2004–2103 | ✅ complete (seit 2026-06-03) |
| female_1900_01 – female_1900_16 | 16 | ~925 | 1903–2003 | ✅ complete (seit 2026-06-08, 11:42 Uhr) |
| female_1800_01 – female_1800_19 | 19 | ~1.400 | 1800–1902 | ✅ complete (seit 2026-06-17) |
| female_1800_20 – female_1800_24 | 5 | ~382 | 1800–1902 | ✅ complete (seit ~2026-06-28) |
| **Gesamt** | **55** | **3.952** | **1800–2199** | ✅ **alle complete** |

**Nächster Schritt:** female_1600-Gruppen anlegen (ELO 1600–1799, ~5.941 Spielerinnen — noch keine Gruppen in DB).

---

## Orchestrator (VPS) — föderationsbasiertes Scraping + P1/P2/P3-Monatsrefresh

| | |
|---|---|
| Dashboard | **https://scelo.chesspit.net** (BasicAuth) |
| Modus | **bis zu 10 Threads (2 Residential + 9 DC); aktuell 6 DC aktiv (DC-DE + DC-US + DC-ES disabled)** |
| DC-Modus | Individuelle Von/Bis-Zeit pro Karte (Ortszeit, Timezone-basiert) |

### Alle Threads

| Thread | Typ | Profil | Föderationen | Timezone | Status |
|--------|-----|--------|--------------|----------|--------|
| T1 | Residential | semi_aggressive | DACH (Priority) | — | ✅ aktiv |
| T2 | Residential | normal | DACH (Priority) | — | ✅ aktiv |
| DC-DE (Slot 99) | Datacenter | semi_conservative | POL, UKR, LAT, LIT, EST, CZE, SVK, FID | Europe/Berlin | ⏸ disabled |
| DC-IN (Slot 100) | Datacenter | semi_conservative | IND, IRI | Asia/Kolkata | ✅ aktiv |
| DC-UK (Slot 101) | Datacenter | semi_conservative | ENG, SCO, WLS, IRL, NIR, DEN, NOR, SWE, FIN, ISL | Europe/London | ✅ aktiv |
| DC-US (Slot 102) | Datacenter | semi_conservative | USA, CAN | America/New_York | ⏸ disabled |
| DC-HK (Slot 103) | Datacenter | semi_conservative | CHN, VIE + Ozeanien | Asia/Hong_Kong | ✅ aktiv |
| DC-ES (Slot 104) | Datacenter | semi_conservative | ESP, ITA, POR, AND, GIB | Europe/Madrid | ⏸ disabled |
| DC-MX (Slot 105) | Datacenter | semi_conservative | FRA, BEL, NED, LUX | America/Mexico_City | ✅ aktiv |
| DC-AE (Slot 106) | Datacenter | semi_conservative | SRB, CRO, BIH, MKD, MNE, SLO, KOS, ALB, GRE, TUR | Asia/Dubai | ✅ aktiv |
| DC-DACH (Slot 107) | Datacenter | semi_conservative | GER, SUI, AUT (Vollbackfill) | Europe/Berlin | ✅ aktiv |
| DC-UPDATE-1 (Slot 108) | Datacenter | semi_conservative | alle (P1/P2/P3-Monatsrefresh, `update_only=1`) | Europe/Berlin | ✅ aktiv |

**DC-UPDATE-1 ersetzt seit 2026-07-02 den alten `dc_update`-Thread** — siehe Session-Änderungen unten. Läuft aktuell die 40 P3-Batches ab (P1+P2 bereits fertig).

### P1/P2/P3-Monatsrefresh — Fortschritt (Stand 2026-07-02, ~19:25 UTC)

Ersetzt die alten 4 UP-Jobs (lokal, Mac Mini) + föderationsbasierte `dc_update`-Rest-Batches durch drei geschlechtsunabhängige Prioritätsstufen, komplett auf dem VPS (siehe `orchestrator/monthly_refresh_tiers.py`):

| Tier | Filter | Spieler | Batches | Status |
|---|---|---:|---:|---|
| P1 | ELO ≥ 2300, alle Föderationen | 4.189 | 2 | ✅ komplett fertig |
| P2 | GER/SUI/AUT, ELO < 2300 | 19.481 | 7 | ✅ komplett fertig |
| P3 | Rest (alle übrigen ≥1×gescrapten Spieler) | 118.066 | 40 | 🔄 7/40 fertig, 1 läuft (27.806 neue Partien bisher) |

P1 und P2 waren größtenteils bereits durch den Mac-Mini-Ad-hoc-Lauf bzw. den laufenden DC-DACH-Vollbackfill abgedeckt und liefen daher sehr schnell durch. P3 läuft seit ~11:30 Uhr deutlich schneller als ursprünglich geschätzt (~66 Min/Batch statt ~4h) — vermutlich Überschneidung mit den parallel laufenden Welt-Backfill-Threads (DC-AE/IN/HK/MX/UK) in den höheren ELO-Bändern. Hochrechnung: alle 40 Batches evtl. in ~1–1,5 Tagen statt der befürchteten fast einer Woche fertig; muss sich bei den unteren ELO-Bändern noch bestätigen. Bei Bedarf per zweitem `dc_update_2`-Thread weiter beschleunigbar (siehe `monthly_refresh_tiers.DC_UPDATE_POOL`).

---

## Raspberry Pi (Slot 50 "Pi") — aktiv seit 2026-06-10

Raspberry Pi 500 als drittes Scraping-Gerät beim Bruder (Remote-Zugang via Tailscale).

| | |
|---|---|
| Gerät | Raspberry Pi 500 (Pi 5, ARM64, 8 GB), Benutzer `pit1` |
| Tailscale-IP | `100.125.193.29` |
| Profil | `normal` (1 Thread, kein Proxy — residential IP) |
| Queue | 1247 Gruppen (`device='raspi'`), Jahr 2020, ELO 1400–2840, alle Föderationen |
| Sync | `sync_pi_to_vps.sh` alle 5 Min → `merge_pi_status.py` → thread_slot 50 im Dashboard |
| **Fortschritt (2026-06-28)** | **362 done / 1 running (SWE) / 884 pending — ~29 %** |

**Fortschritt:** 17/1246 am 12.06. → 362/1247 am 28.06. (~21,5 Gruppen/Tag) → Rest ~6 Wochen (Anfang August). Worker + Sync aktiv (Periode 2020-03-01).

**Status abfragen** (Tailscale `up`, NordVPN aus; Pi-SQLite unter `orchestrator/pi_data/scraper.db`, kein `sqlite3`-CLI → Python):
```bash
ssh pit1@100.125.193.29 "python3 -c \"import sqlite3; c=sqlite3.connect('/home/pit1/fide-scraper/orchestrator/pi_data/scraper.db'); print(list(c.execute('SELECT status,COUNT(*) FROM scrape_groups GROUP BY status')))\""
```

```bash
# Worker-Log:
ssh pit1@100.125.193.29 "tail -20 /tmp/worker_pi.log"
# Sync-Log:
ssh pit1@100.125.193.29 "tail -10 /tmp/sync_pi.log"
# Worker neu starten (falls nötig):
ssh pit1@100.125.193.29 "cd ~/fide-scraper && source .venv/bin/activate && kill \$(pgrep -f worker.py); sleep 2; nohup python3 orchestrator/worker.py > /tmp/worker_pi.log 2>&1 &"
```

**Hinweis SSH:** Tailscale-Tunnel muss aktiv sein. Falls Verbindung hängt:
```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale ping 100.125.193.29
```

### Residential Queue-Strategie (T0/T1)

**Nur DACH-Region**, Ziel: GER 1800+ bis 2022, AUT+SUI bis 2020–2021

| Priorität | Inhalt |
|-----------|--------|
| P1–P12 | GER 2026 ELO 1904–2002 |
| P13–P33 | Historische Gruppen 2010–2012, ELO ≥ 2300 (USA, POL, GER, ITA, ESP, RUS) |
| P1001–P3017 | DACH 2025–2023 (GER 1900+, AUT+SUI alle ELO) |
| P2001–P2234 | GER 1800–1899 2026 (234 Gruppen, interleaved) |
| P3013–P3246 | GER 1800–1899 2023 |
| P4001–P6069 | DACH 2022–2020 |
| P4053–P4286 | GER 1800–1899 2022 |
| P5059–P5292 | GER 1800–1899 2021 |
| P6057–P6290 | GER 1800–1899 2020 |
| P100000+ | DACH vor 2020 (deprioritisiert) |
| P500000+ | alle anderen Föderationen |

### DC Queue-Strategie

Jeder DC-Thread hat eigenen Pool (`thread_affinity`), Prio: 2026→2009, Jahr DESC / ELO DESC

| DC-Thread | Gruppen done | Gruppen pending | Jahresbereich |
|-----------|-------------:|----------------:|---------------|
| DC-DE *(disabled)* | 327 | ~1.608 | 2009–2026 |
| DC-IN | 408 | ~1.937 | 2009–2026 |
| DC-UK | 308 | ~1.051 | 2009–2026 |
| DC-US *(disabled)* | 280 | ~410 | 2009–2026 |
| DC-HK | 213 | ~329 | 2009–2026 |
| DC-ES *(disabled)* | 329 | ~2.458 | 2009–2026 |
| DC-MX | 294 | ~2.662 | 2009–2026 |
| DC-AE | 340 | ~1.121 | 2009–2026 |
| DC-DACH | 313 | ~1.084 | 2009–2026 |

---

## Analytics-Frontend (Port 8055)

| | |
|---|---|
| URL | **https://scelo.chesspit.net/analytics** *(oder lokal Port 8055)* |
| Framework | Dash (Python), Multi-Page |
| Default-Spieler | Gukesh D (FIDE-ID 46616543, Weltmeister 2024) |

### Seiten

| Seite | Gruppe | Pfad | Beschreibung |
|-------|--------|------|--------------|
| ELO-Top100 | Aktiv | `/c` | Live-Top-100 Rangliste mit ELO-Verlauf |
| ELO-Verteilung | Aktiv | `/dist` | ELO-Verteilungshistogramm nach Kategorie |
| Spieler-Steckbrief | Aktiv | `/player-profile` | Profil + Rating-History + Spielstatistiken |
| Partien-Detail | Test | `/games` | Alle Partien eines Spielers, filterbar |
| GM/IM Entwicklung | Test | `/titles` | Zeitreihe der Titelträger |

---

## Änderungen Session 2026-07-04 — Review #5: Queue-Migration SQLite → PostgreSQL

Letzter offener Review-Punkt umgesetzt: die Orchestrator-Queue (`scrape_groups`/`scrape_runs`) zieht aus der SQLite `/data/scraper.db` ins **Schema `orchestrator` der fidedb** (Migration `013_orchestrator_queue.sql`).

| Was | Details |
|-----|---------|
| **setup_db.py** | Jetzt PG-Verbindungsmodul: `connect()` mit `search_path=orchestrator,public`, Autocommit, Reconnect-Retry (10×, Backoff bis 60 s — Tunnel-Drops/PG-Neustarts töten den Worker nicht); Schema-DDL idempotent selbstprovisionierend |
| **queue_manager.py** | `?`→`%s`, `datetime('now','localtime')`→`localtimestamp`; Claim bleibt optimistisch (atomares UPDATE + rowcount); Interface unverändert → worker.py brauchte keine Änderung. **Caveat dokumentiert:** `reset_stale_running()` ist global — sobald ein zweites Gerät die geteilte Queue nutzt, braucht es claimed_by + Geräte-Scope |
| **store.py** | `julianday()`→`EXTRACT(EPOCH …)`, `int || text`-Casts, `ROUND(x::numeric,n)::float` (kein Decimal in Dash-JSON), datetime→ISO-String-Normalisierung; Verbindung pro Aufruf mit retries=1 (Dashboard darf nicht hängen) |
| **Generatoren** | `generate_groups.py` (ohne `--db`, `ON CONFLICT DO NOTHING`), `generate_monthly_refresh_batches.py`, `reset_monthly_refresh.py`, `sync_done_groups.py` (jetzt eine einzige PG-Verbindung), `reassign_dach.py` |
| **Datenübernahme** | `scripts/migrate_queue_to_pg.py`: SQLite → PG mit ID-Erhalt, eine Transaktion, Sequenz-`setval`, automatische Verifikation (Zeilenzahlen + Status-Verteilung); Generalprobe mit lokaler scraper.db-Kopie (24.588 Gruppen) erfolgreich |
| **Gelöscht** | `export_pi_groups.py`, `merge_pi_status.py`, `setup_pi_worker.sh`, `sync_pi_to_vps.sh` — Geräte sprechen künftig direkt mit PG, kein SQLite-Export/Merge mehr |
| **Backup** | `backup_fide_vps.sh`: SQLite-Teil entfernt — pg_dump fidedb enthält die Queue automatisch; Queue-only-Restore: `pg_restore --schema=orchestrator` |
| **Tests** | `tests/conftest.py` neu: PG-Test-Fixture (`ORCH_TEST_DATABASE_URL` oder abgeleitete `fide_orch_test`-DB; skippt ohne erreichbare PG); test_queue_manager + test_store portiert, +3 neue Tests (DC-Affinity-Claim, reset_stale_running, duration/rate) — 35 passed |

**Deploy-Schritte (VPS):** Worker stoppen → Image bauen → `migrate_queue_to_pg.py --sqlite /data/scraper.db` → `up -d --no-deps` beide Container → Dashboard + Claim verifizieren → scraper.db im Volume als `.migrated`-Archiv belassen.

---

## Änderungen Session 2026-07-03/04 (Abend) — Architektur-Review-Umsetzung

Architektur-Review mit Fable-Modell durchgeführt (`review-elo-dashboard-2026-07-03.md` im Repo-Root, 11 priorisierte Punkte) und direkt **9 von 10 umsetzbaren Punkten** abgearbeitet — alle deployed und live verifiziert:

| # | Was | Details |
|---|-----|---------|
| 1 ✅ | **Backup-Regime** | VPS-Cron 03:45: `scripts/backup_fide_vps.sh` (pg_dump fidedb ~854 MB + SQLite-Online-Backup scraper.db ~4 MB, Rotation 7/30 Tage, TimescaleDB-Restore-Weg im Header). Offsite: Mac-Mini-Pull 07:30 via launchd (`pull_backup_macmini.sh`, Retention 5 Tage). Beide Wege getestet, Integrität verifiziert. |
| 2 ✅ | **Auto-Retry failed-Gruppen** | `requeue_failed()`: retries < 3 + letzter Versuch > 2 h → automatisch pending (Worker-Start + Leerlauf); stündliche WARNING für Gruppen ohne Retry-Budget. USA/2019-Anomalie per `retries=3` + Notiz bewusst ausgenommen (manual hold). |
| 3 ✅ | **429-Fallback** | `DIRECT_FALLBACK_ON_429=false` auf VPS: nie mehr direkt (ohne Proxy) von der FIDE-geblockten VPS-IP fetchen — weder als 429-Fallback noch implizit im Pool-Cooldown. |
| 4 ✅ | **profiles.yaml statisch** | Laufzeit-State (enabled/active_hours/max_hours/active_profile) in `/data/runtime_settings.json` (`orchestrator/runtime_settings.py`, atomar); `cp -n`-Volume-Seeding abgeschafft, Git/Image = Wahrheit, YAML jetzt kommentierbar. Alt-Datei: `/data/profiles.yaml.pre-review4.bak`. |
| 6 ✅ | **store.py + state_io.py** | Kompletter DB-Zugriff aus app.py extrahiert (app.py 2626→2191 Zeilen); eine atomare worker_state-Implementierung für beide Container (app.pys Truncation-Race-Kopien entfernt); 9 Tests inkl. DC-UPDATE-1-Regressionstest. |
| 7 ✅ | **Heatmap dynamisch** | Übersichts-Spalten live aus Thread-Config (`_overview_columns()`); ELO-Floor/Ceiling in neuer `[dashboard]`-Sektion der profiles.yaml. |
| 8 ✅ | **Fuzzy-Queue** | `TIER_WIDTH=1` als offizielle Design-Entscheidung dokumentiert (deterministisch nach Priorität, von P1→P2→P3 verlangt); Doku angepasst. |
| 9 ✅ | **Aufräumen** | Caddy-Verzeichnis + alte UP-Job-Pipeline gelöscht (−621 Zeilen: `reset_current_year.py`, `generate_update_batches.py`, `update_jobs.yaml`, `run_update_job*.sh`); Doku Aufgabe 6 auf Traefik/Coolify. **Lektion:** Dockerfile kopierte gelöschte Datei → Build brach still, Deploy lief mit altem Image (Fix `a86a89f`) — nach Löschungen Dockerfile-COPYs prüfen, nach Deploys Code-im-Container verifizieren. |
| 10 ✅ | **Pool-Hot-Reload** | `ProxyManager` lädt Pool-Dateien bei mtime-Änderung selbst nach (30s-Drossel, leerer Parse ersetzt nie) — IP-Tausch ohne Worker-Neustart; live bewiesen. **Wichtig:** Pool-Dateien in-place syncen (scp/cat >), nicht rsync/mv (Bind-Mount-Inode). |
| 5 ⏳ | **Queue → PostgreSQL** | Einziger offener Punkt (1–2 Tage); dank #6 nur noch `store.py` + `queue_manager.py` + Generatoren betroffen. *(→ umgesetzt 2026-07-04, siehe Session-Eintrag oben)* |

Tests: 111 passed (+1 bekannter Alt-Fehler `test_retry_on_429`, unabhängig). Commits: `1804820` … `65026cc`.

---

## Änderungen Session 2026-07-03

### ProxyJet-Ausfall → Wechsel auf Webshare → Region-Split
| Was | Details |
|-----|---------|
| **Problem entdeckt** | Alle DC-Threads standen still, keine erfolgreichen Saves mehr. Diagnose: `proxy-jet.io` und Subdomains vom VPS aus unerreichbar (TCP-Timeout bzw. Read-Timeout selbst bei einfachsten Anfragen), vermutlich Domain-Beschlagnahmung. VPS-eigene Internetverbindung (Google direkt: HTTP 200) und Mac-Mini-Scraping (unproxied) unbetroffen. |
| **Providerwahl** | Recherche + Kostenvergleich (DataImpulse, IPRoyal, Webshare, Oxylabs) für ~60 GB/6 Monate Nutzungsprofil. User entschied sich für **Webshare** (8 Jahre Marktpräsenz, 1.223+ Trustpilot-Reviews) trotz etwas höherer Kosten als DataImpulse — Priorität: Verlässlichkeit statt kleinstem Preis, nach dem ProxyJet-Vorfall. |
| **`proxy_manager.py` providerneutral umgebaut** ✅ | `ProxyJetManager` → `ProxyManager`, neuer Pool-Modus (viele `IP:PORT` + 1 Credential-Paar, zufällige Auswahl pro Request) zusätzlich zum bisherigen Single-Host-Modus — nötig weil Webshare eine statische 100-IP-Liste liefert, keinen Rotating-Gateway wie ProxyJet |
| **Alle Configs umgestellt** ✅ | `profiles.yaml`, `docker-compose.yml`, `.env` (lokal + VPS) — `PROXYJET_*` (25 Vars) → `PROXY_*` (5 Vars), da Webshare nur ein gemeinsames Credential-Paar für alle 100 IPs braucht statt 9 separater DC-Thread-Paare |
| **Deploy-Stolperstein: `/data/profiles.yaml`-Volume** | Lebt in einem persistenten Docker-Volume, wird nur per `cp -n` (no-clobber) aus der git-Version geseedet — reiner `git pull` + Rebuild reicht bei strukturellen Änderungen nicht. Live-Datei musste manuell nachgezogen werden (unter Beibehaltung der `enabled`-Flags für DC-DE/US/ES) |
| **Bug gefunden: Proxy-Wiederverwendung über Retries** ✅ gefixt | Nach erstem Deploy: 0 erfolgreiche Saves über mehrere Minuten. Ursache: `_fetch()` zog den Proxy einmal pro Combo und behielt ihn über alle `max_retries`-Versuche bei — bei ~10-12% toten IPs im 100er-Pool verbrannte das den kompletten Retry-Budget an einer einzigen toten Verbindung statt auszuweichen. Fix: frischer Proxy pro Retry-Versuch. Verifiziert: 0 → 26-36 Saves/5min, 0 anhaltende Fehlschläge |
| **`scripts/check_proxy_pool.py`** ✅ neu | Testet alle Pool-IPs direkt gegen FIDE, listet tote auf. Erster Lauf: 88/100 erreichbar, 12 tot (4 davon im selben `166.88.110.0/24`-Subnetz — Hinweis für Webshare-Support) |
| **worker.py: Proxy-IP jetzt in Fehler-Logs** ✅ | Vorher ließ sich aus den Logs nicht ablesen, welche IP fehlschlug — jetzt `proxy=host:port` in jeder Warn-/Error-Zeile, für organisches Monitoring toter IPs über die Zeit |
| **1. Webshare-Ersatzrunde** | User ersetzte 5 der 12 toten IPs. Nachgetestet: nur 1 (Ägypten) funktioniert, die 4 Türkei-Ersatz-IPs landeten wieder im selben kaputten Subnetz |
| **Geo-Regression erkannt + behoben** ✅ | ProxyJet hatte pro DC-Thread einen regionsspezifischen Host + passende `timezone`/`active_hours` (Anfragen zur lokalen Wachzeit aus plausibler Region). Webshares gemeinsamer Pool hatte das verloren. GeoIP-Klassifizierung (ip-api.com) der 100 IPs → 3 Regions-Pools (User-Entscheidung: Naher Osten + Afrika + Asien-Ozeanien zusammengelegt, da Ägypten/Südafrika UTC+2 nahe an Türkei UTC+3 liegen): Europa (53 IPs → `dc_de`/`dc_uk`/`dc_es`/`dc_dach`/`dc_update_1`, 5 Threads), Naher-Osten+Afrika+Asien-Ozeanien (19 IPs → `dc_in`/`dc_hk`/`dc_ae`, 3 Threads), Amerikas (28 IPs → `dc_us`/`dc_mx`, 2 Threads) |
| **Alles deployed + verifiziert** ✅ | Mehrere Neustarts, durchgehend gesunde Save-Raten (26-36/5min), 0 anhaltende Fehlschläge nach jedem Schritt |
| **22 failed-Gruppen geprüft + 21 zurückgesetzt** ✅ | Nach Abschluss der Migration im Dashboard aufgefallen: 22 Gruppen auf `failed`. 21 zeigten Circuit-Breaker-/Verbindungsabbruch-Signaturen vom 30.6.–3.7. (zeitlich zur ProxyJet-Degradierung passend) → auf `pending` + `priority=0` gesetzt (kommen vor der gesamten Queue dran). 1 Ausreißer (USA/2019/1599–1623, `retries=0`, keine Fehlermeldung, `last_run_at` 28.6.) bewusst unangetastet gelassen — andere Ursache, nicht netzwerkbedingt |
| Commits | `d238a81` (Provider-Wechsel), `c035898` (Retry-Fix), `f966cd9` (Logging + Health-Check-Skript), `1385f65`/`7d4497f`/`96fe185` (Doku), `5e49fec` (Region-Split), `29ee081` (DC-UPDATE-1 → Europa), `1705230` (Session-Doku) |

---

## Änderungen Session 2026-07-02

### Monatlicher Update-Prozess komplett neu gebaut: P1/P2/P3-ELO-Band-System
| Was | Details |
|-----|---------|
| **Auslöser** | `reset_current_year.py` (Vormonats-Mechanismus) setzte pauschal ALLE done-Gruppen des Jahres zurück — inkl. der ~700+ Zeilen des separaten Welt-Backfills, nicht nur die Update-Batches. Zusätzlich: keine Geschlechtertrennung mehr gewünscht, Batches sollen rein nach ELO-Band statt Föderation definiert werden |
| **Neue Priorität** | P1 = ELO ≥ 2300 (alle Föderationen/Geschlechter), P2 = DACH (GER/SUI/AUT) < 2300, P3 = Rest — ersetzt die alten 4 UP-Jobs (ELO2300/FEMALE/GER/DACH) + föderationsbasierte `dc_update`-Rest-Batches |
| **`orchestrator/monthly_refresh_tiers.py`** ✅ | Neues Modul, Single Source of Truth für Tier-Filter/-Grenzen, von Batch-Generator UND Worker importiert |
| **`orchestrator/generate_monthly_refresh_batches.py`** ✅ | Ersetzt `generate_update_batches.py`; ELO-Band-Batches gepoolt über alle Föderationen (statt pro Föderation), Zielgröße ~2.000–3.000 Spieler; sortiert nach Tier zuerst (P1→P2→P3), dann Größe — wichtig bei sequenzieller Abarbeitung auf nur einem Thread |
| **`orchestrator/reset_monthly_refresh.py`** ✅ | Ersetzt `reset_current_year.py`; trifft ausschließlich P1/P2/P3-Gruppen (federation-Sentinel), lässt den Welt-Backfill unangetastet — behebt den Kern-Bug |
| **`worker.py::get_fide_ids()`** ✅ | Neuer föderationsübergreifender Tier-Zweig, bestehender Föderations-Pfad für den Welt-Backfill unverändert |
| **Thread-Pool** | Bewusst mit nur 1 Thread gestartet (`dc_update_1`, Slot 108, ersetzt alten `dc_update`) — sequenzielle Abarbeitung nach Priorität; zweiter Thread (`dc_update_2`) bei Bedarf trivial ergänzbar |
| **`app.py`** ✅ | 9 hartkodierte DC-Thread-Label-Stellen auf dynamischen `_dc_thread_maps()`-Helper umgestellt (liest `profiles.yaml` live statt hartkodierter Dicts) |
| **`monthly_update.sh`** ✅ | Mac-Mini-Schritte entfernt (config.yaml-Rewrite, UP-Jobs-Schleife) — läuft jetzt vollständig ohne Mac Mini/MacBook Pro; Raspberry Pi bleibt wie bisher nur für historischen Backfill zuständig |
| **Deploy-Stolperstein** | `/data/profiles.yaml` lebt in einem persistenten Docker-Volume, wird nur per `cp -n` (no-clobber) aus der git-Version geseedet — ein reiner `git pull` + Rebuild reicht bei strukturellen Änderungen NICHT. Live hatte DC-DE/US/ES `enabled:false` (git-Version war veraltet) — git-Datei an Live-Stand angeglichen, dann `/data/profiles.yaml` gelöscht und Container neu gestartet, damit der Seed-Mechanismus sauber greift |
| **Alte Dateien als superseded markiert** | `update_jobs.yaml`, `scripts/run_update_job.sh`, `orchestrator/generate_update_batches.py`, `orchestrator/reset_current_year.py` — bleiben auf Platte, nicht mehr aufrufen |
| **Ad-hoc Mac-Mini-Lauf (Ausnahme)** | Vor dem Deploy: `UP-ELO2300` lokal für Juni nachgeholt (1274/1274, 0 Fehler) + gezielter Re-Scrape der 246 bereits vor dem Monatswechsel gescrapten Top-Spieler zur QC-Absicherung (246/246, 0 Fehler) |
| **Ergebnis (Stand ~19:25 UTC)** | P1 ✅ fertig, P2 ✅ fertig, P3 🔄 7/40 Batches (27.806 neue Partien) — läuft weiter im Hintergrund, deutlich schneller als geschätzt |
| **Bug gefunden + gefixt: Bericht Scraper zeigte DC-UPDATE-1 nicht** | `_ordered_dc` in `app.py` war hartkodiert und kannte noch das alte Label `"DC-UPDATE"` — nach dem Rename auf `DC-UPDATE-1` fiel die Spalte komplett aus Tabelle UND Tagessummen (`_dc_mb`, `_total`) raus. Liste jetzt live aus `profiles.yaml` abgeleitet |
| Commits | `6c19bbb` (Hauptumbau), `547b7f2` (profiles.yaml an Live-Stand angeglichen), `97aa3ba` (Bericht-Scraper-Fix) |

---

## Änderungen Session 2026-06-28

### Mac Mini — female_1800_20–24 abgeschlossen, alle female_XX (1800–2199) complete
| Was | Details |
|-----|---------|
| **female_1800_20–24** ✅ | 5 Gruppen, ~382 Spielerinnen, `scraped_to='2026-05-01'` |
| **Alle 55 female-Gruppen** ✅ | ELO 1800–2199 komplett (female_2100–female_1800-Serie) |
| **DB-Stand** | 9.268.017 Partien, ~9,4 GB, 141.699 Spieler mit ≥ 1 Periode, 107/253 Gruppen complete |
| **Kein Backfill aktiv** | Mac Mini idle — nächste Gruppe noch nicht gestartet |
| **Nächster Schritt** | female_1600-Gruppen anlegen + seeden (~5.941 Spielerinnen, ELO 1600–1799, ~80 Gruppen) |

---

## Änderungen Session 2026-06-17

### Mac Mini — female_1800_11–18 abgeschlossen
| Was | Details |
|-----|---------|
| **female_1800_11–15** ✅ | Waren bereits vollständig in DB (inkl. pre-2012 Quartale) — `backfill_status` auf `complete` korrigiert |
| **female_1800_16** ✅ | Fehlende pre-2012 Quartalsperioden nachgeladen (16/16 Perioden), fertig 09:30 Uhr |
| **female_1800_17** ✅ | 7.877 Perioden, fertig 13:22 Uhr |
| **female_1800_18** ✅ | 7.172 Perioden, fertig 16:42 Uhr |
| **female_1800_19** ✅ | 8617/8617 Player-Period-Kombinationen, 0 Fehler, fertig 20:35 Uhr |
| **female_1800_20–24** ⏳ | 5 Gruppen offen — in neuer Session starten |
| **Spieler mit ≥ 1 Periode** | **140.168** (vs. 95.585 beim Start dc_update am 2026-06-07, +44.583) |

---

## Änderungen Session 2026-06-10

### Raspberry Pi 500 — Setup abgeschlossen, Scraping aktiv
| Was | Details |
|-----|---------|
| **Setup Phase 1–3** ✅ | Pi 500 eingerichtet: OS, SSH, Repo, venv, SSH-Key auf VPS, Tailscale (Fernzugriff via `100.125.193.29`) |
| **Worker aktiv** ✅ | 1.247 Gruppen (Jahr 2020, alle Föderationen außer DACH), 1 Thread (normal-Profil, kein Proxy), `profiles_pi.yaml` |
| **Pi-Sync** ✅ | `sync_pi_to_vps.sh` + `merge_pi_status.py`: alle 5 Min SCP → Merge → thread_slot 50 "Pi" im Dashboard |
| **VPS DB** ✅ | 1.247 Gruppen Jahr 2020 → `device='raspi'`, `thread_affinity=NULL`; 1 AUT-Gruppe → dc_dach |
| **Bugs gefixt** | `profile_manager.py`: dotenv vor PROFILES_PATH laden; `setup_pi_worker.sh`: fehlende .env-Einträge ergänzen |
| Commits | `efd21ec`, `95fe051`, `c85976c` |

### Mac Mini — female_1800_08–10 abgeschlossen
| Was | Details |
|-----|---------|
| **female_1800_04–07** ✅ | Abgeschlossen (seit 2026-06-09) |
| **female_1800_08–10** ✅ | Chain via `chain_female_1800_08_10.sh` abgeschlossen — _10 fertig 16:54 Uhr (5809/5810) |
| **female_1800_11–24** ⏳ | 14 Gruppen, ~1.037 Spielerinnen — Chain-Skripte ausstehend |
| **female_2000 Master** ✅ | `backfill_status = 'complete'` gesetzt (war vergessen worden) |

---

## Änderungen Session 2026-06-08

### Mac Mini — female_1900 abgeschlossen, female_1800 läuft
| Was | Details |
|-----|---------|
| **female_1900_01 – _16** ✅ | Komplette Reihe (16 Gruppen, ~925 Spielerinnen) abgeschlossen — _16 zuletzt um 11:42 Uhr (5191/5191, 0 Fehler) |
| **female_1800_01 – _02** ✅ | Abgeschlossen (5083/5084 bzw. 5824/5824 Perioden) |
| **female_1800_03** 🔄 | Läuft (Stand ~21:42 Uhr: 3665/5192, ETA ~22:45 Uhr) — letzte Gruppe der aktuellen Chain |
| **`scripts/chain_female_1900_16_1800_03.sh`** ✅ | Chain-Skript für female_1900_16 + female_1800_01–03, lief automatisch durch |
| **`scripts/chain_female_1800_04_07.sh`** ✅ | Folgekette vorbereitet (female_1800_04→05→06→07), morgen früh manuell starten |

### Orchestrator-Dashboard — Übersicht-Heatmap-Fix
| Was | Details |
|-----|---------|
| **Problem** | "Übersicht gesamt" zeigte ELO-Buckets ab 0 — `dc_update`-Batches tragen `elo_min=0` als Drift-Puffer (`REST_ELO_FLOOR` in `generate_update_batches.py`), wodurch leere Buckets weit unterhalb der realen Population (≥1400) sichtbar waren |
| **Fix** ✅ | Neue Konstante `OVERVIEW_ELO_FLOOR = 1400`; `query_overview()` klemmt `lo_bucket` jetzt auf `max(elo_min-Bucket, 1400)` — Worker-Auswahllogik (`get_fide_ids`, Drift-Schutz) bleibt unverändert |
| Deploy | Build + `up -d --no-deps dashboard` auf VPS ausgeführt |
| Commit | `6f53e4d` |

### Dokumentation
| Was | Details |
|-----|---------|
| **`docs/setup_raspi.pdf`** ✅ | PDF-Export von `docs/setup_raspi.md` erstellt (Pandoc → HTML → Chrome-Headless-Druck) |

---

## Änderungen Session 2026-06-07

### VPS Orchestrator — dc_update: monatliches Update für die "Rest"-Population
| Was | Details |
|-----|---------|
| **Problem** | Die 4 Update-Jobs `UP-ELO2300`/`UP-FEMALE`/`UP-GER`/`UP-DACH` (`update_jobs.yaml`) decken nur ~30.000 der 125.500 bereits gescrapten Spieler monatlich ab. Die übrigen **~95.585 "Rest"-Spieler** (alle gescrapten, aktiven Spieler außerhalb ELO≥2300/Female/GER/SUI/AUT) hatten keinen automatischen Refresh-Mechanismus |
| **`update_only`-Spalte** ✅ | Neue `scrape_groups.update_only`-Migration (`setup_db.py`); wenn `1`, filtert `get_fide_ids()` (`worker.py`) zusätzlich auf `EXISTS(scrape_periods WHERE status='ok')` — Update-Batches wählen garantiert nur bereits gescrapte Spieler aus, kein Risiko eines Vollbackfills durch Rating-Drift in dynamischen ELO-Bändern |
| **Auto-Einsortierung neuer Spieler** ✅ | Sobald ein Spieler erstmals vollständig gescraped wurde (Eintrag in `scrape_periods`), erscheint er automatisch im nächsten Update-Zyklus seines Föderations-Batches — ohne Re-Balancing |
| **`generate_update_batches.py`** ✅ | Neues Skript: 77 Batches generiert (1 pro Föderation, ELO 0–2299; große Föderationen ESP/IND in je 3 ELO-Unterbänder à 3.000–6.000 Spieler gesplittet); alle mit `thread_affinity='dc_update'`, `update_only=1`, Jahr 2026 — Initial-Lauf auf VPS ausgeführt: 95.585 Spieler erfasst |
| **`dc_update`-Thread aktiviert** ✅ | Slot 108, "Update", `enabled: true` in `profiles.yaml`; erster Lauf ITA/2026/0–2299 bestätigt korrekt 5.182 von 5.658 Spielern (nur bereits gescrapte) × 1 Periode |
| **`monthly_update.sh`** ✅ | Neuer Schritt 5/5: `reset_current_year.py` wird automatisch per SSH im VPS-Dashboard-Container ausgeführt (vorher nur als manueller Hinweis) — requeued die `dc_update`-Batches monatlich |
| **Bug-Fix `reset_current_year.py`** ✅ | Skript ignorierte `ORCHESTRATOR_DATA_DIR` und suchte die DB unter `/app/orchestrator/scraper.db` statt `/data/scraper.db` — hätte im Container nie funktioniert; jetzt nutzt es `setup_db.DB_PATH` |
| **Dashboard: Bericht Scraper** ✅ | DC-UPDATE-Spalte jetzt dauerhaft sichtbar (analog zu DC-DACH), auch ohne bisherige Run-Daten |
| Commits | `e59abe4` (dc_update Update-Batches + Migration + Worker-Filter), `757a0ff` (Bericht Scraper DC-UPDATE-Spalte) |

---

## Änderungen Session 2026-06-05

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2000_02 – _09** ✅ | 8 Gruppen, 561 Spielerinnen abgeschlossen (letzte: _09, 2026-06-03) |
| **female_1900_01 – _04** ✅ | 4 Gruppen, 269 Spielerinnen abgeschlossen |
| **female_1900_05 – _07** 🔄 | Chain gestartet: _05 läuft aktiv, _06 + _07 starten automatisch nach |

---

## Änderungen Session 2026-05-26

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **Bericht Länder-Tab** ✅ | Neuer Tab „🗺 Bericht Länder": hierarchische DataTable Welt → Kontinent → „In Arbeit"/„Ohne Daten" → Land; Gruppen-% + Spieler (gescraped/aktiv aus PostgreSQL); aufklappbar [+]/[−]; Auto-Refresh 300 s |
| **Bericht Scraper-Tab** ✅ | T1–T4 immer anzeigen (auch ohne Daten); Gesamt-Spalten neu: `%_Res | %_DC | MB_Res | MB_DC | Total` |
| **DC enabled-Flag Fix** ✅ | `run_dc_slot()` prüft `enabled` zwischen Gruppen; Toggle wirkt ohne Worker-Neustart (Commit `aec6f39`) |
| **Worker Restart Bug Fix** ✅ | Atomische `worker_state.json`-Writes + 1 s Startup-Grace; verhindert Sofort-Stopp von DC-Threads nach Neustart (Commit `92dd9a0`) |
| **profiles.yaml in git** ✅ | VPS-Version mit allen 8 DC-Threads nach git committed (Commit `68c0778`); war vorher nicht versioniert |
| **DC-DE + DC-UK** | `enabled: false` (manuell deaktiviert via Dashboard) |

---

## Änderungen Session 2026-05-25 (aktuell)

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2000_01** ✅ | 66 Spielerinnen, ELO 2090–2103, abgeschlossen |
| **female_2000_02** 🔄 | 65 Spielerinnen, ~50% fertig, läuft weiter |
| **run_female_chain.sh** | Chain läuft vollautomatisch durch alle female_XX-Gruppen |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **Worker-Neustart 17:14** | Nach HK-Abschluss (VIE 2025) automatisch neu gestartet, alle Threads aktiv |
| **Bericht-Tab** | Neuer Tab „📊 Bericht": tägliches Datenvolumen pro Thread (MB), Zwischensummen Residential/DC mit % |
| **3 neue DC-Scrapers** | DC-ES (ESP/ITA/POR/AND/GIB), DC-MX (FRA/BEL/NED/LUX), DC-AE (SRB/CRO/BIH/MKD/MNE/SLO/KOS/ALB/GRE/TUR) |
| **Übersicht-Heatmap** | DC-ES/MX/AE als neue Spalten ergänzt |
| **docker-compose.yml** | DC-ES/MX/AE Credentials als Env-Variablen in dashboard + worker |

### Analytics-Frontend
| Was | Details |
|-----|---------|
| **Navbar restrukturiert** | Gruppen „Aktiv" (ELO-Top100, ELO-Verteilung, Spieler-Steckbrief) und „Test" (Partien-Detail, GM/IM) |
| **Version A/B gelöscht** | `elo_a.py`, `elo_b.py`, `elo_dist_b.py` entfernt |
| **ELO-Top100** | Umbenannt von „Version C" |
| **Default-Spieler** | Gukesh D (46616543) auf allen Seiten als Vorauswahl |
| **Partien-Detail** | 2-Karten-Filter, Zeitraum-Slider (Ab Jahr), 2-zeilige Spaltenköpfe, keine Sortierung/Filter-Zeile |

---

## Änderungen Session 2026-05-23

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2100_01** 🔄→✅ | 65 Spielerinnen, ELO 2183–2199, abgeschlossen |
| **55 female_XX-Gruppen angelegt** | ELO 1800–2199, 3.952 Spielerinnen, 2010-01–2026-04, pending |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **GER 1800–1899 aktiviert** | 234 skipped → pending, interleaved in DACH-Jahresbänder |
| **Dashboard DC-Spalten** | Übersicht-Heatmap: DC-DE/IN/UK/US/HK als Aggregat-Spalten |
| **Dropdown-Favoriten** | ★ GER/SUI/AUT immer oben in Föderations-Dropdown |

---

## Änderungen Session 2026-05-22

### Mac Mini
| Was | Details |
|-----|---------|
| global_24b – global_28b ✅ | 9 Gruppen, ELO 2300–2324, alle fertig |
| **ELO ≥ 2300 complete** | **Alle 51 Gruppen fertig** — Chain-Script lief durch bis 19:17 Uhr |
| hist_-Gruppen | 5 historische Gruppen (2010–2012) in PostgreSQL angelegt |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **5 DC-Threads** | DC-DE/IN/UK/US/HK — alle mit eigenem Host/Credentials/Timezone |
| **DC Auto-Modus** | Timezone-basiert 07–23 Uhr Ortszeit, Toggle im Dashboard |
| **thread_affinity** | Jede Gruppe in SQLite-Queue hat DC-Thread-Zuweisung |

---

## Ältere Änderungen

→ Sessions 2026-05-13 bis 2026-05-21: global_14b – global_24a abgeschlossen,
   Dashboard-Verbesserungen, VPS-Profil-Umstellung, Retry-After-Fix.
