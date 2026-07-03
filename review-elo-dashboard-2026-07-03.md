# Architektur-Review: Scraping-Orchestrator (fide-scraper)

**Datum:** 2026-07-03 · **Reviewer:** Claude (Fable 5) · **Scope:** `orchestrator/` + Deployment, rein analytisch (kein Code verändert)

**Gelesene Quellen:** `docs/scraping_orchestrator.md` (Original-Briefing), `worker.py` (1189 Z.), `app.py` (2699 Z.), `queue_manager.py`, `proxy_manager.py`, `profile_manager.py`, `profiles.yaml`, `setup_db.py`, `monthly_refresh_tiers.py`, `generate_monthly_refresh_batches.py`, `docker-compose.yml`, `caddy/Caddyfile`, Tests-Verzeichnis.

**Hinweis zur Terminologie:** Das Projekt heißt im Repo `fide-scraper` (Dashboard deployed als `scelo.chesspit.net`); "elo-dashboard" wird hier synonym verstanden. Das Deployment läuft real über **Traefik/Coolify** (Labels in `docker-compose.yml`), nicht über Caddy — der `caddy/`-Ordner ist ein nie entfernter Alternativpfad (Details unter Frage 5).

---

## Executive Summary

Die Architektur ist für die Zielgröße (Kernanalyse ~1.100 Spieler, inzwischen ~142.000 gescrapte Spieler, ~9,5 Mio. Partien) **grundsätzlich tragfähig und in den kritischen Pfaden robuster als das Briefing-Dokument vermuten lässt** — Retry-Logik, Circuit-Breaker, atomare State-Writes und optimistisches Queue-Locking sind alle vorhanden und durchdacht. Die echten Schwachstellen liegen woanders:

1. **Operationelle Blindheit bei Fehlschlägen** — failed-Gruppen bleiben stumm liegen (der 22-Gruppen-Vorfall nach dem ProxyJet-Ausfall wurde erst Tage später manuell im Dashboard entdeckt).
2. **Kein Backup des Queue-States** — `scraper.db` lebt ungesichert in einem Docker-Volume; das PostgreSQL-Backup ist ebenfalls noch offen.
3. **Konfigurations-Split-Brain** — `profiles.yaml` vermischt statische Konfiguration mit Laufzeit-State, wird vom Dashboard umgeschrieben und driftet durch das `cp -n`-Volume-Seeding systematisch von der Git-Version weg.
4. **app.py ist ein 2.700-Zeilen-Monolith**, der Datenzugriff, Config-Persistenz und Präsentation vermischt — funktioniert, ist aber der am schwersten testbare Teil.

Die SQLite/PostgreSQL-Trennung (Frage 1) ist **kurzfristig kein Problem, mittelfristig aber die richtige Baustelle**: nicht wegen Concurrency, sondern weil der Scraping-Status heute schon auf beide DBs verteilt ist und Multi-Device-Betrieb (Pi, Mac Mini) nur über File-Export-Krücken funktioniert.

---

## Frage 1: SQLite vs. fidedb — macht die Trennung noch Sinn?

### Ist-Zustand

- **SQLite** (`/data/scraper.db`, Docker-Volume, WAL-Modus): `scrape_groups` (Queue, ~25.000 Zeilen), `scrape_runs` (Run-Historie). Zugriff durch Worker-Threads (bis zu 11 parallel, je eigene Connection) und Dashboard.
- **PostgreSQL/fidedb** (Host-Netz): Ratingdaten (`game_results`, `rating_history`, `players`) — **und** `scrape_periods`, also der Scraping-Status pro (Spieler, Periode).

### Bewertung

Das oft genannte Argument "operativer State getrennt von Analysedaten" trägt hier nur halb, denn **die Trennlinie verläuft nicht zwischen operativ und analytisch, sondern mitten durch den Scraping-Status**: ob ein Spieler-Monat gescrapt ist, steht in PG (`scrape_periods`); ob eine Gruppe fertig ist, steht in SQLite. Drei konkrete Folgen im heutigen Code:

1. **Joins nur in Python:** `app.py::_query_laender_data()` muss SQLite-Gruppen und PG-Spielerzahlen getrennt abfragen und im Python-Code zusammenführen (inkl. eigenem 5-Minuten-Cache mit Modul-Globals, `app.py:2192 ff.`). Jede neue Auswertung, die beide Seiten braucht, wiederholt dieses Muster.
2. **Multi-Device nur über Datei-Krücken:** Der Raspberry Pi kann die SQLite-Queue nicht direkt claimen — dafür existieren `export_pi_groups.py` und `merge_pi_status.py` (File-Export/-Merge). Läge die Queue in PG, würde der Pi über den ohnehin vorhandenen SSH-Tunnel dieselbe Queue nutzen wie der VPS-Worker, mit demselben optimistischen Locking (`UPDATE … WHERE status='pending'` funktioniert in PG identisch).
3. **Zwei Backup-Regime nötig:** PG-Backup ist ein bekannter offener Punkt; das SQLite-File im Named-Volume hat de facto *kein* Backup-Regime. Der Verlust wäre teilweise aus `scrape_periods` rekonstruierbar (welche Kombis fertig sind), aber Run-Historie, MB-Statistiken, Prioritäten und thread_affinity wären weg.

**Was für SQLite spricht** (und warum kein Notfall besteht): Die Queue funktioniert, das optimistische Claim-Locking in `queue_manager.py:137` ist korrekt, WAL-Modus verkraftet 11 Schreiber auf einem Host problemlos, und das Dashboard bleibt bedienbar, wenn PG kurz weg ist (`_query_pg_players()` degradiert absichtlich auf Cache/"—"). Concurrency ist **kein** Argument für den Umzug — bei dieser Last ist SQLite nicht der Engpass.

### Vorschlag (Wichtig, nicht dringend)

Queue-Tabellen als eigenes Schema `orchestrator.scrape_groups` / `orchestrator.scrape_runs` nach fidedb migrieren, `QueueManager` auf psycopg2 umstellen (die SQL-Statements sind zu ~90 % portabel; `AUTOINCREMENT`→`SERIAL`, Parameterstil `?`→`%s`). Danach `export_pi_groups.py`/`merge_pi_status.py` löschen. Ein sinnvoller Zeitpunkt wäre **zusammen mit** der Einführung des PG-Backups — dann ist mit einem Schlag der gesamte State in einem Backup-Regime. Bis dahin als Sofortmaßnahme: `scraper.db` ins bestehende tägliche Wartungsfenster mit `sqlite3 .backup` auf den Host sichern (Einzeiler im Cron).

---

## Frage 2: Fuzzy-Queue & Scheduling — robust gegen Fehlschläge?

### Was gut ist (besser als das Briefing vermuten lässt)

- **Retry pro Versuch mit frischer Proxy-IP** (`worker.py::_fetch`): exponentielles Backoff (`4^(attempt-1)`), pro Retry wird eine neue IP aus dem Pool gezogen — der Fix vom 2026-07-03, der das Totlaufen des Retry-Budgets auf einer toten IP behebt. Sauber.
- **429-Behandlung:** `Retry-After`-Header wird respektiert, sonst profilbasierter Cooldown (`cooldown_on_429`); der Cooldown pausiert den Pool des betroffenen Threads via `report_block()`.
- **Circuit-Breaker** (`_CIRCUIT_BREAKER_THRESHOLD = 15`): 15 aufeinanderfolgende Doppel-Fehlschläge brechen die Gruppe ab, statt stundenlang gegen eine Wand zu laufen.
- **Crash-Recovery:** `reset_stale_running()` beim Start, atomare `worker_state.json`-Writes (rename(2)), Startup-Grace gegen den Truncation-Race — alles Lehren aus realen Vorfällen, korrekt umgesetzt.
- **Optimistisches Locking** beim Gruppen-Claim mit 5 transparenten Retries — race-sicher bei parallelen Threads.

### Die drei echten Lücken

**(a) Failed-Gruppen sind eine Sackgasse ohne Alarm.** `mark_failed()` setzt `status='failed'`, inkrementiert `retries` — und dann passiert **nichts** mehr. Die `retries`-Spalte wird nirgends ausgewertet, es gibt keinen automatischen Re-Queue und keine Benachrichtigung. Genau so entstand der Vorfall dieser Woche: 21 Gruppen fielen während des ProxyJet-Ausfalls auf `failed` und lagen tagelang unbemerkt, bis sie manuell im Dashboard auffielen. Das ist die größte operative Schwäche des Systems — ein Provider-Ausfall von Stunden erzeugt stilles Datenloch von Tagen.

**(b) Der 429-Fallback fetcht direkt ohne Proxy — von einer IP, die FIDE geblockt hat.** `worker.py:470` ruft nach dem Cooldown `_fetch(…, proxy_manager=None)` auf. Auf dem Mac Mini ist das sinnvoll (dessen IP ist frei). Auf dem VPS — wo dieser Code seit dem P1/P2/P3-Umbau primär läuft — ist die eigene IP laut Projektdoku von FIDE gesperrt. Der Fallback ist dort also strukturell zum Scheitern verurteilt und sendet nebenbei bei jedem 429 ein Signal von der gesperrten VPS-IP an FIDE. Er "funktioniert" nur insofern, als sein Scheitern in den Circuit-Breaker-Zähler läuft.

**(c) Die "Fuzzy-Queue" existiert nicht mehr — nur noch in der Doku.** `TIER_WIDTH = 1` (`queue_manager.py:22`) kombiniert mit lückenlos durchnummerierten Prioritäten (`generate_monthly_refresh_batches.py` vergibt `base+rank`, eindeutig pro Gruppe) bedeutet: der Kandidaten-Tier enthält fast immer **genau eine Gruppe** — das gewichtete Zufalls-Sampling wählt aus einer Ein-Element-Menge. Die Abarbeitung ist deterministisch nach Priorität. Das ist für P1→P2→P3 sogar *gewollt und richtig*, widerspricht aber dem im Briefing dokumentierten Design-Ziel "kein erkennbares Muster". Verschleierung leistet heute nur noch der Timing-Jitter (der funktioniert: `get_wait_time` mit ±35–50 %, Minimum-Clamp) und die active_hours-Fenster. Das sollte man **entscheiden statt erben**: entweder das Fuzzy-Ziel offiziell aufgeben und die Doku anpassen, oder `TIER_WIDTH` wieder auf einen sinnvollen Wert heben, wo Reihenfolge egal ist.

Kleinere Beobachtungen: `_dc_is_active()` ist fail-open (Fehler → Thread gilt als aktiv — bei einem Tarnungs-Feature diskutabel, aber pragmatisch vertretbar); `report_block()` pausiert den gesamten Pool eines Threads statt nur der auslösenden IP (bei 53 Europa-IPs grob, aber konservativ-sicher); `get_wait_time()` gehört sachlich in den ProfileManager, nicht in den QueueManager (reine Ordnungsfrage).

### Vorschlag

1. **Auto-Retry mit Deckel** (Kritisch): Beim Worker-Start und/oder periodisch: `UPDATE scrape_groups SET status='pending' WHERE status='failed' AND retries < 3 AND last_run_at < datetime('now', '-2 hours')`. Die `retries`-Spalte existiert bereits — sie muss nur endlich gelesen werden. Dazu ein Dashboard-Badge "N failed > 24h" oder eine Logzeile auf WARNING, die man extern monitoren kann. (Deckt sich mit dem bestehenden TODO "Orchestrator Auto-Retry".)
2. **429-Fallback konfigurierbar machen** (Wichtig): `direct_fallback_on_429: false` als Profil- oder Env-Flag, auf dem VPS deaktiviert; stattdessen nach Cooldown erneut über den Pool (andere IP) versuchen.
3. **Fuzzy-Entscheidung dokumentieren** (Nice-to-have): TIER_WIDTH-Kommentar und `docs/scraping_orchestrator.md` Aufgabe 2 an die Realität anpassen — oder bewusst re-aktivieren.

---

## Frage 3: profiles.yaml — wartbar bei neuen Quellen/Profilen?

### Struktur-Befund

Die Datei vermischt **drei Verantwortlichkeiten mit unterschiedlichen Lebenszyklen**:

| Inhalt | Ändert sich | Geändert von |
|---|---|---|
| `profiles` (Rate-Limits, Timeouts) | selten, bewusst | Mensch/Git |
| `fuzzy_weights` | selten | Mensch/Git |
| `concurrency` (Threads, **enabled-Flags, active_hours, max_hours**) | laufend | **Dashboard zur Laufzeit** |

Weil das Dashboard die Datei per `yaml.safe_dump` komplett neu schreibt (`app.py` hat dafür **sieben** fast identische `_save_*`-Funktionen), gilt:

- **Kommentare sind unmöglich** — jede UI-Interaktion würde sie löschen. Deshalb ist die 274-Zeilen-Datei heute kommentarfrei, obwohl gerade die DC-Thread-Blöcke Erklärung bräuchten (warum hat `dc_mx` Timezone Mexiko-Stadt, aber FRA/BEL/NED als Föderationen? Antwort: ProxyJet-Erbe — steht in der Doku, nicht in der Datei).
- **Die Git-Version ist eine Lüge:** Durch das `cp -n`-Seeding in `docker-compose.yml` wird die Git-Version nur beim allerersten Container-Start kopiert; danach lebt die Wahrheit in `/data/profiles.yaml` und jede strukturelle Git-Änderung muss manuell in die Live-Datei gepatcht werden. Das hat diese Woche bereits einmal gebissen (Änderung kam nach `docker compose build` nicht an) und ist inzwischen dokumentiert — dokumentierte Landminen bleiben aber Landminen.
- **YAML-Writes sind nicht atomar** (`open(path, "w")` + dump, kein tmp+rename wie bei `worker_state.json`): Der Worker liest die Datei live zwischen Gruppen (`_read_dc_thread_enabled`). Ein Read während des Writes liefert Parse-Fehler → Fallback ist `True` ("weiterlaufen") — ein deaktivierter Thread könnte den Toggle verpassen. Selten, aber das exakt gleiche Race, das bei `worker_state.json` schon einmal behoben wurde.

### Hardcoding-Inventur (was konfigurierbar sein sollte, es aber nicht ist)

| Stelle | Hardcoded | Sollte |
|---|---|---|
| `app.py:58` `OVERVIEW_FEDERATIONS` | DC-Spaltenliste der Übersichts-Heatmap (enthält DC-DACH/DC-UPDATE-1 **nicht**) | aus `_dc_thread_maps()` ableiten — der Helper existiert seit dem DC-UPDATE-1-Bugfix, diese eine Liste blieb übrig |
| `app.py:66` `OVERVIEW_ELO_FLOOR = 1400` | unterer Heatmap-Rand | config |
| `app.py:386` `bucket >= 2300 # Mac Mini` | Gerätegrenze in der Heatmap | veraltetes Konzept (P1 ≥2300 läuft heute auf dem VPS) — mindestens Kommentar falsch |
| `worker.py:56` `_CIRCUIT_BREAKER_THRESHOLD = 15` | Abbruchschwelle | pro Profil sinnvoll (`conservative` darf mehr tolerieren) |
| `worker.py:32` FIDE-Module fest importiert | `AJAX_URL`, `parse_calculations`, `save_period` | siehe unten (Quellen-Frage) |
| `app.py` Slot-Nummern `range(4)`, Badge-Farben | max. 4 Residential-Slots | ok für Zielgröße, aber implizit |

### Neue Quelle (z. B. Freestyle Chess): ehrliche Einschätzung

`profiles.yaml` selbst ist dafür **nicht** der Engpass — die Profile (Wartezeiten, Retries) sind quellen-agnostisch. Der Engpass ist der Worker: `scrape_group()` ist fest auf FIDE verdrahtet (URL-Template, Perioden-Logik `valid_periods_for_year`, Parser, PG-Save-Funktionen, `update_only`-Semantik über `scrape_periods`). Eine zweite Quelle ist heute ein Fork von `scrape_group()`, nicht ein Config-Eintrag. **Empfehlung: das jetzt nicht generisch umbauen.** Ein Quellen-Plugin-System für eine hypothetische zweite Quelle wäre klassische spekulative Generalisierung. Wenn Freestyle Chess konkret wird, ist der saubere Schnitt klar erkennbar (Fetch-URL-Builder, Parser, Save-Funktion als Tripel pro Quelle, `source`-Spalte in `scrape_groups`) — vorher kostet er nur Indirektion.

### Vorschlag

1. **Runtime-State aus profiles.yaml herauslösen** (Wichtig): `enabled`, `active_hours`, `max_hours` der Threads/Slots in eine eigene kleine Datei (`runtime_settings.json`, atomar geschrieben wie `worker_state.json`) oder — falls der Queue-Umzug nach PG kommt — in eine `orchestrator.settings`-Tabelle. Danach ist `profiles.yaml` wieder rein statisch, kommentierbar, und das `cp -n`-Seeding kann durch einen normalen Read-only-Bind-Mount ersetzt werden (die ganze Volume-Landmine verschwindet).
2. Bis dahin (Nice-to-have): YAML-Write in `app.py` auf tmp+rename umstellen (eine 4-Zeilen-Hilfsfunktion, ersetzt 7 Duplikate).

---

## Frage 4: Dashboard-Layer — sauber getrennt?

### Befund: funktional getrennt, strukturell vermischt

Positiv zuerst: **Die Scraping-Logik selbst ist sauber draußen.** `app.py` scrapt nichts, importiert weder Fetcher noch Parser, und steuert den Worker ausschließlich über drei entkoppelte Kanäle: `worker_state.json` (Kommandos/Status), `profiles.yaml` (Config), SQLite (Queue). Der Worker läuft ohne Dashboard, das Dashboard ohne Worker. Das ist die wichtigste Trennung, und sie steht.

Innerhalb von `app.py` ist aber **alles andere vermischt** — 2.699 Zeilen enthalten:

- ~15 SQLite-Query-Funktionen mit Inline-SQL (`query_overview`, `query_queue`, `query_completed`, `_query_laender_data` …)
- 7 YAML-Persistenz-Funktionen (siehe Frage 3)
- Direkten PostgreSQL-Zugriff mit selbstgebautem TTL-Cache in Modul-Globals (`_pg_aktiv_cache`)
- Figure-Building (Heatmap-Kolorierung, Bucket-Splitting-Logik in `query_overview` — das ist *Fachlogik*, keine Präsentation: breite ELO-Bänder werden dort auf 50er-Buckets verteilt)
- Layout + ~20 Callbacks
- **Duplikate:** `read_worker_state`/`write_worker_state` existieren in app.py *und* worker.py in leicht unterschiedlichen Versionen; die Status-Farbkonstanten ebenso.

**Test-Konsequenz, konkret:** Das Tests-Verzeichnis deckt `queue_manager`, `proxy_manager`, Parser, Fetcher, DB und Sampling ab — **für app.py existiert kein einziger Test.** Das ist kein Zufall: die Query-Funktionen sind zwar prinzipiell testbar (nehmen keine Dash-Objekte), aber sie hängen an Modul-Level-Seiteneffekten (`pm = ProfileManager()` beim Import, `get_conn()` gegen den echten DB_PATH, `load_dotenv` beim Import der Kette). Man kann app.py nicht importieren, ohne dass es Config liest. Die Grid-Bucket-Logik (`query_overview`) — der Teil mit dem höchsten Fehlerpotenzial, siehe den DC-UPDATE-1-Report-Bug diese Woche, der genau in dieser Schicht saß — ist dadurch praktisch ungetestet.

### Vorschlag (Wichtig)

Kein Rewrite, sondern **eine Extraktion**: neues Modul `orchestrator/store.py` mit allen SQLite/PG-Lesefunktionen (reine Funktionen: `conn → list[dict]`, Connection wird hineingereicht statt im Funktionskörper geöffnet) plus `orchestrator/state_io.py` mit den *einen* kanonischen `read_worker_state`/`write_state`/YAML-Helpern, importiert von app.py **und** worker.py. app.py behält Layout, Callbacks, Figures. Das ist mechanisches Verschieben (~600 Zeilen), kein Umdesign — und macht die Bucket-/Aggregations-Logik erstmals mit einer In-Memory-SQLite testbar. Der jüngste Report-Bug (DC-UPDATE-1 fehlte still in der MB-Summe) wäre mit einem einzigen solchen Test aufgefallen.

---

## Frage 5: Docker/Caddy-Deployment — passt es zur Zielgröße?

### Befund

**Zur Zielgröße: ja, mit Reserven.** Zwei Container (dashboard, worker) + externes PG, 11 Threads in einem Worker-Prozess, SQLite-Queue mit 25.000 Gruppen — das System hat gerade 9,5 Mio. Partien verarbeitet und läuft im P3-Refresh mit gesunden Save-Raten. Für den Analyse-Endzweck (weibliche Top-Spielerinnen vs. männliche Kontrollgruppe ~2500) ist keine Skalierungsgrenze in Sicht. Mehr Gruppen/Profile sind für Compose kein Problem; der begrenzende Faktor ist FIDE-Rate-Toleranz, nicht die Infrastruktur.

**Brüchig sind drei konkrete Punkte, keiner davon Skalierung:**

1. **Caddy ist totes Erbe mit Verwechslungsgefahr.** Der echte Reverse-Proxy ist Traefik (Coolify-Labels in `docker-compose.yml:57-66`); `orchestrator/caddy/Caddyfile` ist ein nie entfernter, nie aktiver Alternativpfad — aber `docs/scraping_orchestrator.md` Aufgabe 6 beschreibt weiterhin das Caddy-Setup als *das* Deployment. Wer den Docs folgt (inkl. künftiger Claude-Sessions), installiert den falschen Proxy. Nebenbefund: der bcrypt-Hash der Basic-Auth ist in **beiden** Dateien dupliziert; bei einer Passwortänderung wird einer vergessen. (Der Hash selbst im Git ist vertretbar — bcrypt, kein Klartext — aber die Duplizierung nicht.)
2. **Ein Worker-Container = ein Blast-Radius.** Alle 11 Threads (Residential + DC) leben in einem Prozess; jeder Config-Neustart reißt alle laufenden Gruppen ab (abgefedert durch `reset_stale_running`, aber laufende Arbeit an großen Gruppen geht verloren — bei P3-Batches mit 3.000 Spielern sind das Stunden). Der `restart`-Befehl via `sys.exit(0)` + Docker-Restart-Policy ist funktional, aber die Kombination "Pool-Files nur beim Start gelesen + profiles.yaml-Strukturänderungen nur via Live-Patch + Neustart" macht jede Proxy-Wartung zur Restart-Orgie. Für die Zielgröße akzeptabel — aber der Grund, warum sich Neustarts diese Woche gehäuft haben.
3. **Backup-Lücke** (siehe Frage 1): weder `orchestrator_data`-Volume noch PG werden gesichert. Das ist der einzige Punkt, an dem das Deployment nicht "brüchig", sondern **fahrlässig gegenüber 6 Monaten Scraping-Arbeit** ist.

### Vorschlag

1. **Backup zuerst** (Kritisch, deckungsgleich mit Frage 1): `pg_dump` + `sqlite3 .backup` per Cron auf den Host, von dort wegkopieren (Hostinger-Snapshot oder rsync). Ein Nachmittag Arbeit, schützt Monate.
2. **Caddy-Ordner löschen oder als `_legacy/` markieren, Doku Aufgabe 6 auf Traefik/Coolify umschreiben** (Nice-to-have, 30 Minuten). Gleiches Schicksal für `reset_current_year.py`, `generate_update_batches.py`, `update_jobs.yaml` (alle "SUPERSEDED"-markiert, aber noch präsent — jede weitere Session muss sie erneut als tot einordnen).
3. **Pool-File-Hot-Reload** (Nice-to-have): `ProxyManager` liest Pool-Files nur beim Start; ein mtime-Check in `get_proxy()` (alle N Sekunden) würde IP-Tausch ohne Worker-Neustart ermöglichen und Punkt 2 der Restart-Ursachen eliminieren.

---

## Priorisierte Gesamtliste

| # | Priorität | Punkt | Konkreter Vorschlag | Aufwand |
|---|---|---|---|---|
| 1 | **Kritisch** | Kein Backup: `scraper.db` (Docker-Volume) + fidedb | Cron: `pg_dump` + `sqlite3 .backup` → Host → offsite/Snapshot | ~½ Tag |
| 2 | **Kritisch** | Failed-Gruppen bleiben stumm liegen (22-Gruppen-Vorfall) | Auto-Re-Queue mit `retries < 3`-Deckel + Alter-Schwelle; Dashboard-Badge/WARNING-Log für failed > 24 h | ~½ Tag |
| 3 | **Wichtig** | 429-Fallback fetcht direkt von der FIDE-geblockten VPS-IP | Flag `direct_fallback_on_429` (Env/Profil), auf VPS aus; stattdessen Pool-Retry nach Cooldown | ~1 h |
| 4 | **Wichtig** | Runtime-State in profiles.yaml (Kommentare unmöglich, `cp -n`-Drift, nicht-atomare Writes) | enabled/active_hours/max_hours in eigene atomar geschriebene Datei; profiles.yaml wird statisch + read-only-Mount | ~1 Tag |
| 5 | **Wichtig** | Queue-State-Split SQLite/PG (Python-Joins, Pi-Export-Krücken, 2 Backup-Regime) | Queue nach PG-Schema `orchestrator.*` migrieren, QueueManager auf psycopg2; zusammen mit #1 planen | ~1–2 Tage |
| 6 | **Wichtig** | app.py-Monolith: Datenzugriff untestbar, duplizierte State-I/O, Bucket-Logik ungetestet | Extraktion `store.py` + `state_io.py` (mechanisch), danach Tests für Bucket-/Aggregations-Logik | ~1 Tag |
| 7 | **Wichtig** | Hardcodierte `OVERVIEW_FEDERATIONS`-Liste (letzte Nicht-`_dc_thread_maps()`-Stelle) | aus profiles.yaml ableiten wie die übrigen 9 Stellen; `OVERVIEW_ELO_FLOOR` + 2300er-Grenze in Config | ~1 h |
| 8 | Nice-to-have | Fuzzy-Queue de facto deaktiviert (TIER_WIDTH=1), Doku behauptet Gegenteil | Entscheiden: Ziel aufgeben (Doku fixen) oder TIER_WIDTH kontextabhängig reaktivieren | ~1 h |
| 9 | Nice-to-have | Caddy-Erbe + superseded Skripte + Doku Aufgabe 6 veraltet | Löschen/`_legacy/`, Doku auf Traefik umschreiben, Auth-Hash-Duplikat auflösen | ~1 h |
| 10 | Nice-to-have | Pool-Files nur beim Start gelesen → Restart bei jedem IP-Tausch | mtime-basiertes Hot-Reload in `ProxyManager.get_proxy()` | ~1 h |
| 11 | Nice-to-have | Freestyle Chess / zweite Quelle | **Bewusst nichts tun** bis konkret; dann Quellen-Tripel (URL-Builder/Parser/Saver) + `source`-Spalte | — |

**Empfohlene Reihenfolge:** #1 und #2 unabhängig voneinander sofort machbar (kein Refactoring-Risiko, reiner Zugewinn). #4 vor #5 vor #6 — wer die Config entwirrt, bevor die Queue umzieht, muss nichts doppelt anfassen. #3 und #7 sind Kleinaufwände für zwischendurch.

---

*Keine Code-Änderungen vorgenommen. Alle Zeilenangaben beziehen sich auf den Stand von Commit `546e63a`.*
