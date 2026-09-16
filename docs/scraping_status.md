# Scraping-Status

Stand: 2026-09-16 (Quelle: `groups`-Tabelle DB + Orchestrator-Queue in PG (`orchestrator.*`, seit Review #5), Live-Abfrage)
Raspberry Pi: **vom User abgeschaltet (seit 2026-07-22)** — Restbestand (794 pending Gruppen) am 29.07. auf die DC-Threads umverteilt, kein Thema mehr, siehe Abschnitt unten.
⚠️ **Backup-Cron seit 21./22.07. gebrochen (Root Cause: Container-Konsolidierung, fidedb + Kunden-DB tunnelbliq teilen sich seither einen Container) — am 25.08. entdeckt, Datenlücke per manuellem fidedb-Dump geschlossen, Cron-Fix selbst noch offen (Stand 25.08., nicht erneut geprüft in dieser Session). Details: Abschnitt „Backup-Status".**

---

## Gesamtstand DB (Live 2026-09-16)

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **16.160.371** (+525.420 seit 05.09.; ~48 Tsd./Tag im Schnitt — niedriger als die ~105 Tsd./Tag vom letzten Stand, weil P1/P2/P3-Monatsrefresh inzwischen fertig ist und keine großen Nachtrags-Batches mehr liefert, siehe unten) |
| Gruppen complete | **108 / 253** (Stand 07.07., seither nicht neu geprüft) — bezieht sich auf die manuell gepflegten Mac-Mini-Analysegruppen, unabhängig vom P1/P2/P3-System (siehe unten) |
| Global-Gruppen complete | **51 / 51** — ELO ≥ 2300 weltweit vollständig ✅ (Vorbehalt: siehe Top-Spieler-Lückenanalyse unten) |
| VPS-Orchestrator-Queue gesamt | **8.820 done, 2.149 pending, 9 running, 9 failed, 13.873 skipped** — done +719 ggü. 05.09., **9 neue failed-Gruppen** (alle am 14.09. zwischen ~01:30–08:00 Uhr mit „server closed the connection unexpectedly" — sieht nach einer kurzen DB-Verbindungsstörung an dem Tag aus, betrifft 9 verschiedene Threads, noch nicht untersucht/retried, siehe Detail unten) |
| Aktive Threads | 10 DC/DI-Threads (Welt-Backfill) + `dc_update_1` (P1/P2/P3-Monatsrefresh, jetzt komplett durch, arbeitet aktuell AUT/GER/SUI-Restgruppen ab) + 2 P0-Threads `dc_newplayers_1`/`_2` (fast fertig, je 1 letzte/größte Gruppe läuft) — Details siehe Orchestrator-Abschnitt unten |

### Analyse: 9 failed-Gruppen vom 14.09. — Root Cause geklärt (16.09.)

**Auslöser (einmaliges Ereignis, kein FIDE-Blocking):** Am 14.09. um **00:38:06 UTC** wurde ein Postgres-Backend-Prozess auf `fide-tunnelbliq-shared-db` (das ist die tatsächlich produktiv genutzte DB hinter `host.docker.internal:5432` — **nicht** `fide-scraper-db-1`, siehe Randnotiz unten) mit **Signal 9 (SIGKILL)** beendet (`docker logs fide-tunnelbliq-shared-db`: „server process (PID 1362893) was terminated by signal 9: Killed"). Das klassische OOM-Killer-Signatur; kurz davor zeigten die Logs bereits „autovacuum worker took too long to start — canceled" (Ressourcen-Engpass-Vorbote). Postgres ging dadurch für ~3 Sekunden in Crash-Recovery („database system was not properly shut down; automatic recovery in progress" → um 00:38:09 wieder „ready to accept connections"). Nur dieses eine Ereignis im ganzen Zeitraum — keine wiederholten Abstürze.

**Warum daraus 9 verzögerte Fehlschläge über Stunden verteilt wurden (nicht ein einziger sofortiger):** Der Recovery-Moment hat alle zu dem Zeitpunkt offenen DB-Verbindungen ungültig gemacht. Threads, die gerade aktiv liefen (u. a. `dc_newplayers_1`/`_2`), haben das sofort per bestehender Reconnect-Logik (`scraper/db.py`) abgefangen — im Log sichtbar als „DB connection broken; will retry to reconnect" → „DB reconnected". **Threads, die zu dem Zeitpunkt außerhalb ihrer Timezone-Aktivzeiten schliefen** (`stop_event.wait()` in `worker.py::run_dc_slot`, Zeile ~958), haben ihre inzwischen tote `pg_conn` nicht erneuert — `ensure_connection()` wird dort nur **reaktiv nach einer Exception** aufgerufen (`worker.py:1018`), nicht proaktiv beim Aufwachen. Die jeweils erste Query nach dem Aufwachen (zur eigenen lokalen Weckzeit: dc_in 01:30 Asia/Kolkata, dc_ae 03:00 Asia/Dubai, dc_dach/dc_update_1 05:00, dc_es/dc_de 07:00, dc_hk 07:09, dc_uk 08:00 — alle Europe/-Zonen passend zu ihren `active_hours`) ist dadurch mit der toten Verbindung gescheitert → „server closed the connection unexpectedly" → sofort `mark_failed()` (kein Retry innerhalb desselben Versuchs). `dc_us` (03:52, kein rundes Weckzeit-Muster) vermutlich derselbe Effekt, nur ausgelöst beim Aufwachen aus dem Leerlauf-Sleep statt dem Timezone-Sleep.

**Warum die 9 Gruppen trotz Auto-Retry (`retries<3`) noch auf `failed` stehen:** `requeue_failed()` wird nur aufgerufen, wenn die eigene Warteschlange eines Threads leer ist (`_idle_queue_maintenance`, ausgelöst über `get_next_group() is None`) oder beim Worker-Neustart. Da jeder betroffene Thread noch Hunderte pending Gruppen hat, wird dieser Pfad seit dem 14.09. nie erreicht — die Gruppen bleiben strukturell liegen, bis entweder der Worker neu startet oder ein Thread zufällig komplett leerläuft.

**Fix-Vorschlag, Code-Ebene (noch nicht umgesetzt):** `pg_conn = ensure_connection(pg_conn)` zusätzlich proaktiv direkt nach dem Aufwachen aus dem Timezone-Sleep aufrufen (vor `qm_local.get_next_group(...)` in `run_dc_slot`, nach Zeile 958/959) — schließt die Lücke für zukünftige DB-Blips während langer Sleeps.

**✅ Erledigt (16.09.):** Die 9 Gruppen wurden manuell auf `pending` zurückgesetzt und beim folgenden Worker-Neustart sofort wieder aufgegriffen (7 von 9 direkt beim Neustart erneut gestartet, sichtbar in den Logs). Zusätzlich hat sich herausgestellt, dass der eigentliche Auslöser des OOM-Kills der **`orchestrator-worker-1`-Prozess selbst** war (RAM-Verlauf laut Hostinger: Spike 12.09. 4,3→7 GB, kurze Korrektur durch den Crash-Restart am 14.09., seither erneut bis auf ~7,3 GB gewachsen — schneller als beim ersten Mal). Kein konkreter Auslöser für den 12.09. gefunden (kein Code-Deploy — Image unverändert seit 02.09., kein passender Cron-Job; Worker-Logs vor dem 14.09. durch die knappe 30MB-Rotation bereits überschrieben). Als Sofortmaßnahme umgesetzt: `orchestrator/docker-compose.yml` (Commit `f404f22`) bekommt für den `worker`-Service ein **hartes 4GB-Memory-Limit** (statt unbegrenzt) + **großzügigere Log-Rotation** (50MB × 10 statt 10MB × 3), Worker neu deployt (Container recreated, nicht nur restart — RestartCount wieder bei 0). Zusätzlich läuft jetzt ein eigenes RAM-Monitoring auf dem VPS: `/home/pit/scripts/memory_watch.sh` per Cron alle 5 Min → `/home/pit/logs/memory_watch.log` (Host-Speicher + Top-Container), damit ein künftiger Vorfall nicht wieder an der Docker-Log-Rotation scheitert. Die eigentliche Leck-Ursache im Worker-Code ist damit noch nicht behoben, nur eingedämmt (cgroup-OOM trifft künftig nur noch den Worker selbst, nicht mehr Nachbar-Container wie die DB).

**✅ Konkreter Speicherfresser gefunden und behoben (16.09., Commit `060f8e7`):** Die beiden P0-„Neuzugänge"-Bänder mit `elo_min=0` waren keine ~300-Spieler-Gruppen, sondern trafen **1.216.068 Spieler** — die komplette aktive Population mit `std_rating=0` (unbewertet). `generate_new_entrant_batches.py` hatte keinen Filter gegen `std_rating=0`; da alle diese Spieler denselben Rating-Wert teilen, kollabierte das unterste Auffangband beim Batch-Bau zu einer einzigen Riesengruppe. Der Worker hielt dadurch bis zu ~14 Mio. `(fide_id, period)`-Kombinationen gleichzeitig im Speicher — plausible Erklärung für das schnelle RAM-Wachstum seit dem 14.09.-Neustart (für den ursprünglichen Spike am 12.09. bleibt die Ursache mangels Logs unklar, da beide Gruppen erst am 14.09. gestartet sind).

**User-Entscheidung 16.09.:** Unbewertete Spieler (`std_rating=0`) ergeben fachlich keinen Sinn zu scrapen — komplett ausgeschlossen, an drei Stellen: Populations-Query im Generator, Live-Query in `worker.py::get_fide_ids()`, `TIER_BOUNDS["P0"]`-Untergrenze. Die zwei betroffenen Gruppen manuell auf `elo_min=1` korrigiert. Nach Rebuild + Redeploy verifiziert: Gruppe läuft jetzt mit **278 Spielern statt 1,2 Mio.**, Worker-RAM direkt nach Neustart bei 40 MB statt mehreren GB. 4GB-Limit + Monitoring-Cron bleiben als Sicherheitsnetz aktiv, falls doch noch ein anderer Wachstumstreiber existiert.

*(Randnotiz, unabhängig vom Vorfall: Die produktiv genutzte DB läuft in `fide-tunnelbliq-shared-db` (Port 5432, offenbar mit einem anderen Projekt „tunnelbliq" geteilt), nicht in `fide-scraper-db-1` (Port 5433) — die CLAUDE.md-Verbindungsangabe über den Tunnel auf Port 5434 stimmt weiterhin, da der Tunnel auf VPS-Port 5432 zeigt. Nur als Klarstellung, welcher Container bei künftigen Diagnosen zu prüfen ist.)*

---

## Hochrechnung Restlaufzeit VPS-Backfill (Stand 2026-09-16)

Basis: erfolgreiche `scrape_runs` der letzten 7 Tage (09.–16.09.) pro Thread, hochgerechnet gegen die aktuell pending Gruppen je `thread_affinity`. Raspberry-Pi-Bestand ist bereits in den jeweiligen Föderations-Threads mitgezählt (siehe Umverteilung 29.07.). `dc_update_1` fehlt hier bewusst — P1/P2/P3-Monatsrefresh ist komplett durch (siehe Orchestrator-Abschnitt), Thread arbeitet nur noch 17 kleine AUT/GER/SUI-Restgruppen ab.

| Thread | Pending | Ø Gruppen/Tag (7d) | Hochrechnung |
|--------|--------:|---:|---|
| dc_dach | 97 | 8,9 | ~11 Tage → **ca. 27.09.** |
| dc_mx | 145 | 5,7 | ~25 Tage → **ca. 11.10.** |
| dc_hk | 145 | 5,4 | ~27 Tage → **ca. 13.10.** |
| dc_ae | 242 | 6,6 | ~37 Tage → **ca. 23.10.** |
| dc_es | 358 | 5,7 | ~63 Tage → **ca. 18.11.** |
| dc_us | 259 | 3,7 | ~70 Tage → **ca. 25.11.** |
| dc_uk | 277 | 3,4 | ~81 Tage → **ca. 06.12.** |
| dc_de | 302 | 3,6 | ~84 Tage → **ca. 09.12.** |
| dc_in | 290 | 2,6 | ~112 Tage → **ca. 06.01.2027** |

**`dc_in` bleibt der Flaschenhals** (2,6 Gruppen/Tag, weiterhin am langsamsten) — Verdacht auf Proxy-/Tarpit-Problem im IN-Pool ist nach wie vor nicht diagnostiziert. **Auffällig ggü. 05.09.: Tempo praktisch aller Threads spürbar gesunken** (z. B. dc_uk 8,3→3,4/Tag, dc_us 9,0→3,7/Tag, dc_de 6,1→3,6/Tag) — noch nicht untersucht, ob das an schwierigeren/älteren Jahrgängen (mehr Perioden bzw. weniger `skip`-fähige Kombis in den verbliebenen Bändern) liegt oder an einer echten Drossel (Proxy-Pool, Rate-Limiting). Lohnt sich vor der nächsten Statusprüfung genauer anzuschauen. Realistisches Ende des reinen Welt-Backfills bei aktuellem Tempo: **grob Anfang Januar 2027**, getrieben von `dc_in`/`dc_de`/`dc_uk`.
---

## Historie — Stand 2026-09-11

*(Abschnitt aus der Session vom 11.09., durch den 16.09.-Stand oben überholt, als Verlauf erhalten.)*

## Gesamtstand DB (Live 2026-09-11)

| Kennzahl | Wert |
|----------|------|
| Spieler gesamt / aktiv | **1.836.741** / **1.505.239** aktiv |
| Partien gesamt | **16.020.525** (+1.348.377 seit 25.08.; ~79 Tsd./Tag im Schnitt — Rückgang erklärt, siehe „MB-Rückgang untersucht" unten) |
| Gruppen complete | **108 / 253** (Stand 07.07., seither nicht neu geprüft) — bezieht sich auf die manuell gepflegten Mac-Mini-Analysegruppen, unabhängig vom P1/P2/P3-System (siehe unten) |
| Global-Gruppen complete | **51 / 51** — ELO ≥ 2300 weltweit vollständig ✅ (Vorbehalt: siehe Top-Spieler-Lückenanalyse unten) |
| VPS-Orchestrator-Queue gesamt | **8.520 done, 2.459 pending, 8 running, 0 failed, 13.873 skipped** — done +1.092 ggü. 25.08., **0 failed-Gruppen** (nur 27 einzelne Run-Retries am 01.09., alle danach erfolgreich) |
| Aktive Threads | Alle 9 DC-Threads + `dc_update_1` + 2 neue `dc_newplayers_1/2` (P0, seit 01.09.) aktiv, alle innerhalb der letzten 24h gelaufen, keiner hängt |
| Monatsrefresh P1/P2/P3 | ✅ Zyklus (Aug-Periode) **komplett fertig seit 06.09. ~13:54 Uhr** (49/49 Batches) |
| P0-Neuzugangs-Tier (neu) | 🔄 **~71 % fertig** (104/145 Bänder) — nie zuvor gescrapte aktive Spieler, siehe eigener Abschnitt unten |

---

## Hochrechnung Restlaufzeit VPS-Backfill (Stand 2026-09-11)

Basis: erfolgreiche `scrape_runs` der letzten 7 Tage (04.–11.09.) pro Thread, hochgerechnet gegen die aktuell pending Gruppen je `thread_affinity` (nur Welt-Backfill, `update_only=0`).

| Thread | Pending | Ø Gruppen/Tag (7d) | Hochrechnung |
|--------|--------:|---:|---|
| dc_update_1 | 45 | 8,9 | ~5 Tage → **ca. 16.09.** |
| dc_dach | 146 | 6,7 | ~22 Tage → **ca. 03.10.** |
| dc_ae | 274 | 10,6 | ~26 Tage → **ca. 07.10.** |
| dc_mx | 176 | 4,3 | ~41 Tage → **ca. 22.10.** |
| dc_hk | 175 | 4,0 | ~44 Tage → **ca. 25.10.** |
| dc_us | 277 | 5,9 | ~47 Tage → **ca. 28.10.** |
| dc_es | 387 | 7,3 | ~53 Tage → **ca. 03.11.** |
| dc_uk | 295 | 4,3 | ~69 Tage → **ca. 19.11.** |
| dc_de | 322 | 3,9 | ~84 Tage → **ca. 03.12.** |
| dc_in | 304 | 2,9 | ~106 Tage → **ca. 26.12.** |

**Bottleneck-Wechsel:** `dc_in` (2,9/Tag) hat `dc_de` (3,9/Tag) als langsamsten Thread überholt — in der 25.08.-Messung war es noch umgekehrt (dc_de 3,6, dc_in 4,3). Beide bleiben zusammen der reale Flaschenhals; Gesamtende des Welt-Backfills weiterhin grob **Ende Dezember 2026**, unverändert ggü. 25.08. **Weiterhin offen, noch nicht diagnostiziert:** das dc_de/dc_in-Tempoproblem besteht seit Juli unverändert fort (Proxy-/Tarpit-Verdacht, analog IN/HK/AE im Juni) — lohnt sich weiterhin zu prüfen, ist aber **nicht** die Ursache des unten untersuchten MB-Rückgangs (der ist strukturell, nicht technisch).

---

## MB-Rückgang im Dashboard untersucht (2026-09-11) — kein Fehler, sondern ELO-Mix

**Befund: User-Vermutung bestätigt.** Der zuletzt im Orchestrator-Dashboard sichtbare Rückgang der gescrapten Megabyte/Tag ist **kein technisches Problem** (Request-Durchsatz pro Tag ist stabil, 0 failed-Gruppen, keine Proxy-Störung), sondern eine direkte Folge davon, dass die Queue aktuell überwiegend **schwache ELO-Bänder (<1800)** abarbeitet — sowohl im regulären Welt-Backfill (die meisten Länder haben ihre starken Bänder längst durch) als auch besonders im neuen P0-Tier (nie gescrapte, überwiegend niedrig geratete Spieler) und den übervollen P3-Bändern.

| Messung (letzte 14 Tage, pro Spieler) | ≥2200 | 1800–2199 | <1800 |
|---|---:|---:|---:|
| Partien/Spieler (`records_found`) | 12,0 | 8,9 | **4,0** |
| KB/Spieler (`mb_downloaded`) | 8,8 | 7,2 | **4,0** |

Schwache Spieler liefern strukturell **weniger als halb so viel Datenvolumen pro Abfrage** wie starke (dünnere/keine Partiehistorie, mehr „No records found"-Antworten). Zusätzliche Belege:
- Von den aktuell 2.459 pending Welt-Backfill-Gruppen liegen **~72 % im Band <1800** (verteilt über alle 9 Threads) — die starken Bänder sind fast überall schon `done`.
- P0- und P3-Läufe seit 06.09. sind praktisch ausschließlich `<1800`; das erklärt zusätzlich, warum die Tages-MB seit dem 06.09. deutlicher gefallen sind als die reine Gruppen-/Run-Anzahl (die im normalen Rahmen 51–91/Tag blieb).
- `mb_downloaded` ist ohnehin nur ein grober interner Zähler (siehe DataImpulse-Kalibrierungs-Diskrepanz vom 29.07., Faktor ~9 ggü. echtem Traffic) — als Trendindikator innerhalb der eigenen Historie aber brauchbar, und der Trend zeigt sauber auf den ELO-Mix, nicht auf einen Fehler.

**Fazit:** nichts zu fixen. Die niedrigeren MB-Werte sind der erwartbare Verlauf, je tiefer der Backfill in schwächere Spielerpopulationen vordringt.

---

## P0-Neuzugangs-Tier — Fortschritt (Stand 2026-09-11)

Neuer Tier seit 01.09. (Memory `std-rating-sync-fix-2026-09-01`): scrapt alle aktiven Spieler nach, die noch **nie** einen `scrape_periods`-Eintrag hatten (unabhängig vom Status ihrer Föderations-Gruppe), über die zwei dedizierten Threads `dc_newplayers_1`/`_2`.

| | |
|---|---|
| Bänder (Jahre 2025+2026) | **104 done / 41 pending / 1 running** von 145 gesamt → **~71 %** |
| Spieler-Perioden-Kombos | 30.868 done, 12.136 pending, 296 running |
| Noch nie angefasste aktive Spieler | nur noch **2.586** (von ursprünglich ~26.000 im Zieljahrgang) |
| `dc_update_1`-Leihgabe (30 Gruppen, Memory `dc-update-1-p0-boost-2026-09-06`) | ✅ komplett fertig — Thread lief bereits automatisch wieder auf DACH/FRA-Backfill zurück |

Läuft deutlich schneller als die ursprüngliche ~30-Tage-Schätzung nahelegte.

---

## VPS-Sicherheitspatch 2026-07-24 — CVE-2026-31431 "Copy Fail" (Linux-Kernel-LPE)

*(Nachtrag: lag seit dem 24.07. unversioniert im Arbeitsverzeichnis, nie committed — jetzt mit dem 29.07.-Stand zusammengeführt.)*

Per Hosting-Mail auf reale, öffentlich bestätigte Kernel-Lücke hingewiesen worden (CVSS 7.8, lokale Root-Privilege-Escalation über `algif_aead`/AF_ALG, öffentlicher PoC im Umlauf, bestätigt u. a. von Ubuntu/Red Hat/Microsoft/CERT-EU, disclosed 29.04.2026). VPS lief auf `6.8.0-90-generic` (Ubuntu 24.04.4) — verwundbar.

| Was | Details |
|-----|---------|
| **Befund** | Kernel-Pakete (`linux-generic`, `linux-image-generic`, `linux-image-virtual`, `linux-virtual`) standen auf `apt-mark hold` — vermutlich Hostinger-seitig gesetzt. Normales `apt upgrade` scheiterte deshalb zunächst mit „held broken packages". |
| **Fix** | `apt-mark unhold` für die 4 Pakete, dann `apt update && apt upgrade -y` (Kernel + Headers) + Reboot. `cloud-init` bewusst weiter gehalten (unabhängig von der CVE, Standard bei Cloud-Images). |
| **Ausführung** | Sudo-Passwort ließ sich nicht über die SSH-Session von Claude aus eingeben — Update+Reboot als kurzes Skript (`~/kernel_update.sh`) auf dem VPS abgelegt, User hat es selbst mit `sudo bash kernel_update.sh` ausgeführt. |
| **Verifiziert danach** | Kernel `6.8.0-136-generic` (gepatcht), kein `reboot-required`-Flag mehr, `algif_aead` nicht geladen, alle 10 Docker-Container automatisch wieder oben (`unless-stopped`/`always`-Restart-Policies), Tunnel + DB-Verbindung wiederhergestellt, Orchestrator-Worker speichert nachweislich aktiv neue Partien (Live-Log-Check). |
| **Downtime** | Minimal — Container-Uptime nach Reboot bestätigt ~1 Minute, kein Datenverlust, keine übersehenen Gruppen. |
| **Nachwirkung: Dashboard-HTTPS down** | Nach dem Reboot war `https://scelo.chesspit.net` per TLS-Fehler nicht erreichbar. Ursache: ein **nie fertig konfigurierter host-level `caddy`-systemd-Service** (Platzhalter-`Caddyfile` mit `scraper.IHRE-DOMAIN.com`/Dummy-Passwort-Hash, offenbar Rest eines alten Setup-Versuchs) startet bei jedem Boot automatisch und belegt Port 80/443, bevor `coolify-proxy` (der echte Traefik-Proxy für die Domain) binden kann → `coolify-proxy` blieb dauerhaft `Exited (128)`. Erklärt vermutlich auch die wiederkehrenden ACME-Fehler in den `coolify-proxy`-Logs der letzten Monate (09.04./26.04./13.05./29.05./17.07.) — derselbe Konflikt bei früheren Reboots. **Fix:** `sudo systemctl disable --now caddy` + `sudo docker start coolify-proxy` — Dashboard danach wieder erreichbar (HTTP 401/BasicAuth wie erwartet). **Für künftige Reboots erledigt** (Service ist jetzt `disabled`, kommt nicht wieder). |

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

**Update 2026-07-04 — ✅ abgeschlossen:** Gruppe **`top_gap_2300`** angelegt, geseeded (170 Spieler) und per Backfill 2008-04→2026-06 lokal auf dem Mac Mini abgearbeitet (23.082 Kombos, 10:33–17:16 Uhr, 23.081 erfolgreich/1 Fehler, 0 Blockierungen). **Ergebnis: 121 von 170 (71 %) haben jetzt Partien** (2.601 neue Perioden gespeichert) — der Rückstand von 170 auf **49 endgültig leere Fälle** reduziert. Diese 49 wurden lückenlos für alle 191–193 Perioden seit 2008-04 versucht und liefern durchweg `no_data`: historische GM/IM, die vor dem FIDE-Datenbeginn ihre letzte Aktivität hatten oder verstorben sind (u. a. Wojtkiewicz †2006, Unzicker †2006, Kholmov, Savon, Vaulin) — kein Scraping-Loch mehr, sondern realer Datenbestand bei FIDE. `groups.backfill_status='complete'`.

**Gesamt-Bilanz Top-Spieler-Lücke:** von ursprünglich 171 fehlenden Top-Spielern (96,1 % Coverage) sind jetzt nur noch **49 dauerhaft leere Fälle** übrig (~98,9 % effektive Coverage unter Berücksichtigung historischer/verstorbener Spieler) — TODO aus 2026-07-01 damit erledigt.

---

## USA/2019-Anomalie aufgeklärt (2026-07-04, abends)

Die einzige `failed`-Gruppe der Orchestrator-Queue (USA/2019/1599–1623, ID 10362, seit 2026-07-01 auf „manual hold") wurde diagnostiziert und abgeschlossen:

- **Befund:** Kein einziger Eintrag in `scrape_runs` für diese Gruppe — technisch unmöglich, wenn sie über den normalen Worker-Fehlerpfad gescheitert wäre (jedes `mark_failed()` im Code ist zwingend mit einem `log_run()` gekoppelt, in allen drei Worker-Codepfaden). Der `failed`-Status + `retries=3` wurde daher vermutlich am 1. Juli manuell per SQL gesetzt, um sie vom Auto-Retry auszunehmen — keine echte Scraping-Störung.
- **Stichprobentest:** 3 Spieler × 3 Perioden direkt vom Mac Mini abgerufen — durchweg normale „No records found"-Antworten, kein Fehler, kein Block.
- **Gezielter Backfill** (Mac Mini, 185 Spieler der ELO-Band, Jahr 2019, 1.110 Kombos): **0 Fehler**, 11 von 185 Spielern hatten Partien (51 neue Partie-Zeilen), 174 echtes `no_data` — normal für dieses niedrige ELO-Band mit geringer Turnierfrequenz.
- Gruppe manuell auf `status='done'`, `records_found=51` gesetzt. **Damit steht die Queue jetzt bei 0 failed-Gruppen.**

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
| Modus | **bis zu 10 Threads (2 Residential + 9 DC); seit 2026-07-04 ~12:20 alle 9 DC aktiv** (DC-DE/DC-US/DC-ES nachträglich eingeschaltet, User-Entscheidung — Health-Check danach: nur 5 echte 429-Events in 4h, kein IP-Blocking erkennbar) |
| DC-Modus | Individuelle Von/Bis-Zeit pro Karte (Ortszeit, Timezone-basiert) |

### Alle Threads

| Thread | Typ | Profil | Föderationen | Timezone | Status |
|--------|-----|--------|--------------|----------|--------|
| T1 | Residential | semi_aggressive | DACH (Priority) | — | ✅ aktiv |
| T2 | Residential | normal | DACH (Priority) | — | ✅ aktiv |
| DC-DE (Slot 99) | Datacenter | semi_conservative | POL, UKR, LAT, LIT, EST, CZE, SVK, FID | Europe/Berlin | ✅ aktiv (seit 2026-07-04) |
| DC-IN (Slot 100) | Datacenter | semi_conservative | IND, IRI | Asia/Kolkata | ✅ aktiv |
| DC-UK (Slot 101) | Datacenter | semi_conservative | ENG, SCO, WLS, IRL, NIR, DEN, NOR, SWE, FIN, ISL | Europe/London | ✅ aktiv |
| DC-US (Slot 102) | Datacenter | semi_conservative | USA, CAN | America/New_York | ✅ aktiv (seit 2026-07-04) |
| DC-HK (Slot 103) | Datacenter | semi_conservative | CHN, VIE + Ozeanien | Asia/Hong_Kong | ✅ aktiv |
| DC-ES (Slot 104) | Datacenter | semi_conservative | ESP, ITA, POR, AND, GIB | Europe/Madrid | ✅ aktiv (seit 2026-07-04) |
| DC-MX (Slot 105) | Datacenter | semi_conservative | FRA, BEL, NED, LUX | America/Mexico_City | ✅ aktiv |
| DC-AE (Slot 106) | Datacenter | semi_conservative | SRB, CRO, BIH, MKD, MNE, SLO, KOS, ALB, GRE, TUR | Asia/Dubai | ✅ aktiv |
| DC-DACH (Slot 107) | Datacenter | semi_conservative | GER, SUI, AUT (Vollbackfill) | Europe/Berlin | ✅ aktiv |
| DI-UP-1 (Slot 108, `dc_update_1`, Label bis 02.09. `DI-UPDATE-1`) | Datacenter | semi_conservative | P1/P2/P3-Monatsrefresh (`update_only=1`) — **September-Zyklus seit 16.09. komplett durch** (P1 2/2, P2 7/7, P3 40/40), arbeitet bis zum nächsten Monatszyklus 17 restliche AUT/GER/SUI-Einzelgruppen ab | Europe/Berlin | ✅ aktiv |
| DI-NP-1 (Slot 109, `dc_newplayers_1`) | Datacenter | semi_conservative | P0-Tier: nie gescrapte Neuzugänge, alle Föderationen — **neu seit 01.09.** | America/Santiago | ✅ aktiv |
| DI-NP-2 (Slot 110, `dc_newplayers_2`) | Datacenter | semi_conservative | P0-Tier: nie gescrapte Neuzugänge, alle Föderationen — **neu seit 01.09.** | Asia/Ho_Chi_Minh | ✅ aktiv |

**DC-UPDATE-1 ersetzt seit 2026-07-02 den alten `dc_update`-Thread** — siehe Session-Änderungen unten. Zwischen 2026-07-06 und 2026-08-11 half er zusätzlich als **zweiter DACH-Backfiller** (50/50-Split der pending DACH-Gruppen mit `dc_dach`, danach + FRA-Anteil), da er zwischen den monatlichen Update-Läufen sonst leerlief — am 11.08. wieder rückgängig gemacht, weil diese Backfill-Gruppen strukturell niedrigere (=dringlichere) Priorität hatten als die P1/P2/P3-Batches und den Thread dadurch nie an den eigentlichen Monatsrefresh kommen ließen.

### P1/P2/P3-Monatsrefresh — Fortschritt (Stand 2026-07-07, historisch)

Ersetzt die alten 4 UP-Jobs (lokal, Mac Mini) + föderationsbasierte `dc_update`-Rest-Batches durch drei geschlechtsunabhängige Prioritätsstufen, komplett auf dem VPS (siehe `orchestrator/monthly_refresh_tiers.py`):

| Tier | Filter | Spieler | Batches | Status |
|---|---|---:|---:|---|
| P1 | ELO ≥ 2300, alle Föderationen | 4.189 | 2 | ✅ 1. Durchlauf fertig; 2. Durchlauf ✅ 2/2 (0 neue Partien) |
| P2 | GER/SUI/AUT, ELO < 2300 | 19.481 | 7 | ✅ 1. Durchlauf fertig; 2. Durchlauf 🔄 6/7 (0 neue Partien) |
| P3 | Rest (alle übrigen ≥1×gescrapten Spieler) | 118.066 | 40 | ✅ **1. Durchlauf komplett 06.07.** (~116 Tsd. neue Partien); 2. Durchlauf 🔄 1/40 (+4.745) |

**Erster Juli-Durchlauf am 06.07. abgeschlossen** (letzter P3-Batch 16:35–22:04 Uhr). Der Zyklus wurde am 06.07. ~16:30 zurückgesetzt (`last_run_at` genullt, `records_found` der pending-Batches blieb erhalten); der zweite Durchlauf läuft seither nebenher — P1/P2 gingen mangels neuer Daten in Minuten durch. Bei Bedarf per zweitem `dc_update_2`-Thread beschleunigbar (siehe `monthly_refresh_tiers.DC_UPDATE_POOL`).

### P1/P2/P3-Monatsrefresh — Zyklus abgeschlossen (Update 2026-08-25)

✅ **Kompletter Zyklus fertig seit 16.08. ~14:56 Uhr** — letzte `update_only=1`-Gruppe an diesem Zeitpunkt auf `done` gelaufen (per `scrape_groups.last_run_at` verifiziert, 126 Gruppen insgesamt `done`, 0 pending/running). Die am 12.08. revidierte Projektion „grob 16.–18.08." hat exakt gepasst. Seit dem 16.08. läuft die Queue durchgehend nur noch mit Welt-Backfill-Gruppen (`update_only=0`) weiter, keine Refresh-Aktivität mehr bis zum nächsten manuellen `reset_monthly_refresh.py`-Anstoß (nächster fälliger Zyklus: nach dem nächsten Perioden-Import, siehe `docs/project_status.md`).

### P1/P2/P3-Monatsrefresh — aktueller Zyklus (Stand 2026-08-12, Live-Abfrage, historisch)

Zyklus neu gestartet am 11.08. (`orchestrator/reset_monthly_refresh.py`, nachdem `dc_update_1` von der DACH/FRA-Backfill-Last befreit wurde, siehe Session unten) — deckt die Juli-Periode ab (Import `standard_aug26frl.zip`, 562.979 Zeilen `rating_history`).

| Tier | Filter | Batches | Spieler (Band-Summe) | Status |
|---|---|---:|---:|---|
| P1 | ELO ≥ 2300, alle Föderationen | 2 | ~4.189 | ✅ 2/2 fertig |
| P2 | GER/SUI/AUT, ELO < 2300 | 7 | ~18.792 | ✅ 7/7 fertig (seit ~12:25 Uhr) |
| P3 | Rest (alle übrigen ≥1×gescrapten Spieler) | 40 | ~117.900 | 🔄 1 läuft (ELO 2123–2193, seit 12:25 Uhr), 39 offen |

**Live-Stand 12.08. ~14:18 Uhr:** 9 von 49 Gruppen fertig, 1 läuft, 39 pending (**~18 %**). P2 seit dem Vormittags-Check komplett durchgelaufen, P3 hat begonnen. Kein Fehler-/Hänger-Status.

⚠️ **Bekanntes Problem, bewusst unangetastet (User-Entscheidung 11.08.):** Die P1/P2/P3-Bänder wurden am 02.07. für Ziel 2.000–3.000 Spieler/Batch erzeugt; durch den parallel laufenden Welt-Backfill sind die Bänder seither deutlich übervoll gewachsen (P2 jetzt ~2.700/Band statt ~2.800 Ziel, P3-Live-Nachzählung am 11.08. zeigte 118.066 → 183.604, +55,5 %). Fix bei Bedarf: alte pending P3-Zeilen löschen + `generate_monthly_refresh_batches.py` neu laufen lassen (baut dann ~62 statt 40 Bänder) — wächst beim nächsten Zyklus vermutlich wieder aus dem Rahmen, solange der Welt-Backfill weiterläuft, also eher wiederkehrendes Thema als Einmal-Fix. Vorerst nichts weiter anfassen, in ein paar Tagen Fortschritt erneut prüfen.

#### Restlaufzeit neu gerechnet (12.08., ~14:20 Uhr) — 13-Tage-Schätzung war zu pessimistisch

Die 11.08.-Schätzung (~13 Tage, Ende grob Ende August) basierte auf der **Design-Annahme** von ~655 Spieler/Std./Thread (Kommentar in `orchestrator/monthly_refresh_tiers.py`). Live aus den echten `scrape_runs`-Zeitstempeln dieses Zyklus nachgerechnet, sieht das Tempo deutlich höher aus:

| Messgröße | Wert |
|---|---|
| P1+P2 gesamt (9 fertige Bände) | 23.670 Spieler in 18h36min Wall-Clock (17:49 Uhr 11.08. → 12:25 Uhr 12.08.) |
| davon Nachtpause | 7h12min (21:48–05:00 Uhr) — offenbar festes Zeitfenster im DC-Profil |
| **Reine Scrape-Zeit** | **~2.080 Spieler/Std.** |
| **Blended inkl. Nachtpause** | **~1.270 Spieler/Std.** |

Das liegt klar über den 655/Std. der Design-Annahme. **Aber:** P3 hat gerade erst begonnen — der erste laufende Batch (ELO 2123–2193) lief bereits **113 Minuten** (länger als der P1/P2-Schnitt von ~75–90 Min) und war um 14:18 Uhr immer noch nicht fertig. Das deckt sich mit dem Befund übervoller P3-Bänder oben (P3 vermutlich tatsächlich näher an 3.700–5.200 Spielern/Band als an den gespeicherten ~2.950).

**Neuprojektion für P3** (117.898 gespeicherte Spieler, 40 Bänder), ab jetzt (12.08. ~12:25 Uhr, P3-Start):
- Optimistisch (P1/P2-Tempo durchgehalten): ~92h ≈ **4 Tage**
- Vorsichtig (Tempo des ersten, spürbar langsameren P3-Bandes): ~143h ≈ **6 Tage**

→ **Revidiertes Gesamtende: grob 16.–18.08.**, nicht erst 24.08. wie am 11.08. geschätzt. Belastbarer wird die Zahl, sobald mehrere echte P3-Bände durchgelaufen sind (Stand 12.08. 14:20 Uhr: 0 fertig, 1 läuft) — bei Gelegenheit erneut mit echten P3-Laufzeiten nachrechnen.

### P1/P2/P3-Monatsrefresh — aktueller Zyklus (Stand 2026-09-02, Live-Abfrage)

Label seit heute `DI-UP-1` (vorher `DI-UPDATE-1`, siehe Session unten). `federation`-Spalte in `scrape_groups` zeigt für diesen Zyklus P1/P2/P3-Tags **plus** vereinzelte ältere AUT/GER/SUI/FRA-Einzelgruppen (Herkunft nicht geklärt, keine funktionale Auswirkung):

| Tier/Rest | Done | Running | Pending | Skipped |
|---|---:|---:|---:|---:|
| P1 (ELO ≥ 2300) | 2/2 ✅ | – | – | – |
| P2 (GER/SUI/AUT < 2300) | 7/7 ✅ | – | – | – |
| P3 (Rest, 40 Bänder) | 1 | 1 (ELO 2123–2193, seit 07:00 Uhr) | 38 (~112.162 Spieler) | – |
| FRA | 89/89 ✅ | – | – | – |
| AUT/GER/SUI (Einzelgruppen) | 267 | – | 58 (~9.666 Spieler) | 201 (~34.164) |

**Live-Stand ~09:20 Uhr:** 367 von 464 relevanten Gruppen fertig (**~79 %**), 1 läuft, 96 offen. P1/P2 komplett durch, **P3 (die großen Bänder) läuft erst seit gestern (01.09.) an** — davor nur kleinere Restgruppen (FRA/AUT/GER/SUI), daher der sprunghafte Tagesdurchsatz von ~1.000 Spieler/Tag (23.–31.08.) auf 26.745 Spieler an einem einzigen Tag (01.09., 11 Bänder). Aktueller P3-Band braucht ~30–90 Min. (ein Ausreißer 2,5 Std.), ~2.800–2.950 Spieler/Band, seriell (kein zweiter `dc_update`-Thread aktiv).

**Hochrechnung:** bei fortgesetztem P3-Tempo (~11 Bänder/aktivem Tag, 07–23 Uhr Berlin) **~8–14 Tage für die restlichen 39 P3-Bänder + 58 kleinen Restgruppen → ca. 10.–16.09.2026**. Nur 1 Tag Datenbasis seit dem Tempowechsel — Spanne bewusst breit, in ein paar Tagen erneut prüfen. Läuft der aktuelle Zyklus durch, wird er beim nächsten `monthly_update.sh`-Lauf (neuer Monat) automatisch zurückgesetzt (`reset_monthly_refresh.py`) — die 39 P3-Bänder sind also kein Einmal-Ziel, sondern wiederkehrende Monatsarbeit.

**Neu seit 01.09.: P0-Tier / `dc_newplayers_1`+`dc_newplayers_2`** (nie gescrapte, aktive Neuzugänge — Commit `7bd302d`/`78731e2`, noch nicht in diesem Dokument beschrieben). Labels seit heute `DI-NP-1`/`DI-NP-2`. Live-Stand: `dc_newplayers_1` 1/73 Gruppen done (297 Spieler), `dc_newplayers_2` 2/73 done + 1 running (594+297 Spieler) — beide ganz am Anfang, ~10 Std./Gruppe beobachtet, noch keine belastbare Hochrechnung möglich.

### Update 2026-09-03, ~08:15 UTC (Live-Abfrage)

| Tier/Thread | Fertig | Läuft | Offen |
|---|---:|---|---:|
| P1 | 2/2 ✅ | – | – |
| P2 | 7/7 ✅ | – | – |
| P3 (40 Bänder) | 10 | 1 (ELO 1860–1877, 2.952 Spieler, seit 06:48 Uhr) | 29 |
| FRA | 89/89 ✅ | – | – |
| GER | 185 | – | 36 (+138 skipped, Jahresziel bis 2012) |
| AUT | 38 | – | 10 (+30 skipped) |
| SUI | 44 | – | 12 (+33 skipped) |
| `dc_newplayers_1` (P0) | 4/73 | – (pausiert) | 69 |
| `dc_newplayers_2` (P0) | 3/73 | 1 (seit 00:00 Uhr, ~8¼ Std.) | 69 |

**P3 (Update-Thread):** 9 Bänder in 24 h (1→10) — Tempo deckt sich mit gestriger Hochrechnung (~11 Bänder/aktivem Tag), Zieldatum **~10.–16.09.** bleibt gültig.

**P0 (New-Player-Threads) — Tempo geklärt (03.09., Live-Logs vom VPS geprüft):** effektiv nur ~1–1,5 Gruppen/Tag/Thread (echte Läufe 7–10¾ Std.) — bei 69 offenen Gruppen/Thread grob **6–10 Wochen** bis fertig. Zwei getrennte, beide verifizierte Ursachen (keine davon `active_hours` — die Fenster sind mit 14–16 Std./Tag ähnlich breit wie bei den übrigen DC-Threads):

1. **Gruppenlaufzeit:** `worker.py::scrape_group()` holt pro Gruppe `valid_periods_for_year(group.year)` — bis zu 12 Monatsperioden. Bei P1/P2/P3 (Update) ist fast immer nur 1 Periode/Spieler offen (Spieler schon mal gescraped). Bei P0 (`never_scraped_only`) hat der Spieler noch **keinen** `scrape_periods`-Eintrag → alle 12 Perioden des Zieljahres sind offen. Log-Beleg (`docker logs orchestrator-worker-1`): 4 abgeschlossene Gruppen mit exakt `Spieler × 12 Perioden` combos, Laufzeit 7h10min–10h45min bei ~8,1–10,5 s/Request (297 Spieler × 12 ≈ 3.500 Requests/Gruppe) — Request-Tempo selbst ist normal fürs `semi_conservative`-Profil, nur eben 12× mehr Requests/Spieler als bei einer Update-Gruppe.
2. **Die "Rate/h"-Spalte im Dashboard (Seite „Abgeschlossen") miedet keine Requests/Spieler, sondern `records_found / Laufzeit(h)`** (`orchestrator/store.py:322-331`) — also **gefundene Partien pro Stunde**. New-Player-Gruppen zeigen hier 24–31/h (z. B. P0/2025 1689–1703: 258 Partien / 10,75h = 24,0/h), weil brandneue/gerade erst aktive Spieler in ihren ersten Monaten naturgemäß wenige Partien haben (0,65–1,0 Partien/Spieler in den geprüften Gruppen). Das ist **kein New-Player-spezifisches Problem**: eine ganz normale DACH-Backfill-Gruppe mit wenig turnieraktiver Population (GER/2013, ELO 1583–1595: 104 Partien / 186 Spieler / 4,02h) zeigt mit **25,9/h** denselben niedrigen Wert — während turnieraktive Bänder (z. B. FRA/2022 1868–1879: 2.286 Partien/2,57h = 890/h; RUS/2023: 1.858/h) auf dem exakt gleichen Profil/Request-Tempo eine 30–70× höhere Rate/h zeigen. Die Kennzahl misst also Partien-Dichte der Population, nicht Scraper-Geschwindigkeit.

### Update 2026-09-05 (Live-Abfrage)

| Tier/Thread | Fertig | Läuft | Offen |
|---|---:|---|---:|
| P1 | 2/2 ✅ | – | – |
| P2 | 7/7 ✅ | – | – |
| P3 (40 Bänder) | 25 | 1 | 14 |
| FRA (gesamt, alle 3 Threads) | 500 | 1 | 284 |
| FRA auf `dc_update_1` | 89/89 ✅ | – | – |
| GER (gesamt) | 1.129 | – | 103 (+244 skipped) |
| AUT (gesamt) | 257 | – | 28 (+57 skipped) |
| SUI (gesamt) | 238 | – | 32 (+54 skipped) |
| `dc_newplayers_1` (P0) | 8/73 | – | 65 |
| `dc_newplayers_2` (P0) | 7/73 | 1 | 65 |

**P3:** von 10 (03.09.) auf 25 done in 2 Tagen (+15) — Tempo ~7,5 Bänder/Tag, etwas langsamer als die Hochrechnung vom 02./03.09. (~11/Tag), aber die verbleibenden 14+1 Bänder sind trotzdem in **~2 Tagen (ca. 07.09.)** durch.

**P0 (New-Player-Threads):** von 7 auf 15 done in 2 Tagen (+8, ~4/Tag kombiniert bzw. ~2/Tag/Thread) — etwas schneller als die erste Schätzung vom 03.09. (~1–1,5/Tag/Thread), aber noch dünne Datenbasis. Bei aktuellem Tempo grob **~5–6 Wochen** für die restlichen 130 Gruppen (statt der ursprünglich geschätzten 6–10 Wochen) — in ein paar Tagen mit mehr Daten erneut prüfen.

### Update 2026-09-16 (Live-Abfrage) — P1/P2/P3 komplett ✅, P0 fast fertig

**P1/P2/P3-Monatsrefresh: alle 3 Tiers 100 % durch** (P1 2/2, P2 7/7, P3 40/40 — P3 war am 05.09. bei 25/40, seither die restlichen 15 Bänder abgearbeitet). Erheblich schneller fertig als die Hochrechnung vom 05.09. (~10.–16.09.) vorhersagte — traf mit ~16.09. den oberen Rand der Spanne. `dc_update_1` ist seither wieder frei und arbeitet die verbliebenen 17 kleinen AUT/GER/SUI-Einzelgruppen ab (Restbestand aus dem alten Backfill-Split, siehe Session 2026-08-11/12). **Nächster Monatszyklus:** wird beim nächsten `monthly_update.sh`-Lauf (Oktober-Periode) automatisch per `reset_monthly_refresh.py` neu aufgesetzt — reine Wiederholung, kein Handlungsbedarf.

**P0 (`dc_newplayers_1`/`_2`): praktisch am Ende der ursprünglichen Warteschlange.** Beide Threads haben nur noch **1 Gruppe** offen — die jeweils letzte, größte Gruppe pro Thread (Rating-Band 0–1404, d. h. unbewertete/brandneue Spieler ohne Elo-Zahl). Diese läuft bei `dc_newplayers_1` seit 14.09. 11:00 Uhr (~45 Std.), bei `dc_newplayers_2` seit 14.09. 02:38 Uhr (~53 Std.) — deutlich länger als die bisher beobachteten 7–11 Std./Gruppe. **Live-Check bestätigt: kein Hänger** — `scrape_periods` zeigt laufend neue Einträge für Spieler mit `std_rating=0` (213 neue Zeilen in den letzten 15 Minuten), also aktiver Fortschritt, nur eben eine sehr viel größere/dichtere Spielerpopulation in diesem letzten Band als in den übrigen.

> ⚠️ **TODO, sobald diese 2 letzten Gruppen fertig sind:** Der komplette P0-„Neuzugänge"-Bestand vom 01.09. ist dann abgearbeitet — **es braucht einen neuen Lauf, der seither neu hinzugekommene, nie gescrapte Spieler erfasst** (analog zum ursprünglichen P0-Batch-Generator vom 01.09., Commit `7bd302d`/`78731e2`), sonst laufen `dc_newplayers_1`/`_2` leer/ohne Arbeit. Noch nicht eingeplant/terminiert — beim nächsten Status-Check zuerst prüfen, ob die 2 Gruppen durch sind, und dann diesen Lauf anstoßen.

*(Randnotiz zur Gruppenzahl: Anfang September wurden 73 Gruppen/Thread erwartet, jetzt zeigt die DB nur noch 58/Thread als Gesamtzahl — vermutlich wurde die Warteschlange zwischenzeitlich neu gebaut/konsolidiert; keine funktionale Auswirkung, nur als Erklärung für die Abweichung zur alten Hochrechnung.)*

---

## Raspberry Pi (Slot 50 "Pi") — abgeschaltet, Restbestand umverteilt (Stand 2026-07-29)

Raspberry Pi 500 als drittes Scraping-Gerät beim Bruder (Remote-Zugang via Tailscale). **User hat das Gerät seit 2026-07-22 bis auf Weiteres abgestellt** — kein aktives Thema. Die 452 bereits erledigten Gruppen bleiben als `device='raspi'`/`status='done'` stehen (reine Historie, unangetastet).

> ✅ **TODO erledigt (2026-07-29):** Die 794 pending `device='raspi'`-Gruppen (Jahr 2020, alle Föderationen außer DACH) wurden per SQL-Update auf ihre etablierten Föderations-Heimat-Threads umgehängt (`device=NULL`, `thread_affinity` gesetzt) — analog zum FRA-Split vom 22.07. **Bewusst ausgenommen: `dc_update_1`**, da dieser Thread in den nächsten Tagen für den monatlichen P1/P2/P3-Refresh gebraucht wird. Verteilung: dc_ae +166, dc_es +164 (davon 113 ESP), dc_mx +113 (davon 112 FRA), dc_us +110, dc_uk +89, dc_hk +77, dc_de +44, dc_in +30, dc_dach +1 (Sonderfall „staatenlos"/`NON`). Die Pi-Warteschlange ist damit vollständig ins reguläre VPS-Backfill integriert und läuft aktiv mit — kein separates Gerät mehr nötig. Auswirkung auf die Gesamt-Hochrechnung: siehe Abschnitt „Hochrechnung Restlaufzeit" oben (neuer Flaschenhals weiterhin `dc_de`, jetzt ~19.11.2026 statt ~08.11. ohne Pi-Bestand).

> ⚠️ **Status-Sync war zusätzlich seit 2026-07-04 ~08:04 UTC gebrochen** (Review-#5-Deploy): `merge_pi_status.py` + `sync_pi_to_vps.sh` wurden planmäßig gelöscht (Queue-Migration SQLite→PostgreSQL), da sie auf die alte VPS-`scraper.db` zielten, die es nicht mehr gibt. Betrifft nur noch die Historie/den Fall einer erneuten Reaktivierung — durch die Umverteilung nicht mehr akut, da der Pi keine eigene Queue mehr hat.

> ⚠️ **Befund 2026-07-19 (PG-Live-Abfrage):** Die am 07.07. abends vom MacBook Pro angestoßene PG-Queue-Umstellung (Runbook `docs/pi_pg_queue_umstellung.md`) ist **nie in der Queue angekommen** — es existiert kein einziger Claim mit `claimed_by='raspi'`, letzter Slot-50-Run 04.07. 09:27 UTC, raspi-Pool unverändert 452 done / 794 pending. Entweder ist die Umstellung gescheitert/nicht abgeschlossen worden, oder der Pi steht komplett. Ob der Pi noch mit alter Queue-Kopie weiterscrapt, ist von hier nicht sichtbar (Tailscale-Check nötig). **User-Entscheidung 19.07.: Pi-Umstellung ist momentan kein Thema — Punkt ruht, bis er wieder aufgegriffen wird.**

| | |
|---|---|
| Gerät | Raspberry Pi 500 (Pi 5, ARM64, 8 GB), Benutzer `pit1`, seit 22.07. abgeschaltet |
| Tailscale-IP | `100.125.193.29` |
| Profil | `normal` (1 Thread, kein Proxy — residential IP) |
| Queue | **0 Gruppen** (794 pending am 29.07. auf die DC-Threads umverteilt, siehe oben) — 452 `done` bleiben als Historie stehen |
| Sync | ~~`sync_pi_to_vps.sh` alle 5 Min → `merge_pi_status.py`~~ gebrochen seit 2026-07-04 — bei erneuter Reaktivierung neu aufsetzen (Pi hat dann keine bestehende Queue mehr, bräuchte neue Gruppen) |

**Historischer Fortschritt (bis zur Abschaltung):** 17/1246 am 12.06. → 362/1247 am 28.06. → 452/1246 am 04.07. (~15–18 Gruppen/Tag) → 452/1246 eingefroren bis zur Umverteilung am 29.07.

**Status abfragen** (Tailscale `up`, NordVPN aus; Pi-SQLite unter `orchestrator/pi_data/scraper.db`, kein `sqlite3`-CLI → Python):
**Diese Abfrage ist seit dem gebrochenen Sync (siehe Warnhinweis oben) der einzige Weg an den echten Fortschritt** — sie fragt die Pi-eigene lokale `scraper.db` direkt ab, unabhängig vom kaputten VPS-Sync:
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

Stand 2026-07-07, alle Threads aktiv:

| DC-Thread | Gruppen done | Gruppen pending | Jahresbereich |
|-----------|-------------:|----------------:|---------------|
| DC-DE | 336 | ~1.598 | 2009–2026 |
| DC-IN | 429 | ~1.916 | 2009–2026 |
| DC-UK | 332 | ~1.027 | 2009–2026 |
| DC-US | 291 | ~399 | 2009–2026 |
| DC-HK | 229 | ~314 | 2009–2026 |
| DC-ES | 337 | ~2.450 | 2009–2026 |
| DC-MX | 309 | ~2.649 | 2009–2026 |
| DC-AE | 368 | ~1.093 | 2009–2026 |
| DC-DACH | 344 | ~457 | 2009–2026 |
| DC-UPDATE-1 | 1 | ~594 | 2009–2026 (Backfill-Anteil) |

**Umverteilung 06./07.07.:** ~594 DACH-Backfill-Gruppen (`update_only=0`) wurden von `dc_dach` auf `dc_update_1` umgehängt (dc_dach pending: ~1.057 → ~457) — DC-UPDATE-1 arbeitet zwischen den Refresh-Zyklen jetzt am DACH-Vollbackfill mit (aktuell AUT/2017).

---

## Analytics-Frontend (Port 8055)

| | |
|---|---|
| URL | ~~https://scelo.chesspit.net/analytics~~ *(oder lokal Port 8055)* — **Stand 27.08.: nicht live, siehe Deploy-Status unten** |
| Framework | Dash (Python), Multi-Page (`frontend/app.py` + `frontend/pages/*.py`) |
| Default-Spieler | Gukesh D (FIDE-ID 46616543, Weltmeister 2024) |

### Seiten

| Seite | Gruppe | Pfad | Beschreibung |
|-------|--------|------|--------------|
| ELO-Top100 | Aktiv | `/c` | Live-Top-100 Rangliste mit ELO-Verlauf |
| ELO-Verteilung | Aktiv | `/dist` | ELO-Verteilungshistogramm nach Kategorie |
| Spieler-Steckbrief | Aktiv | `/player-profile` | Profil + Rating-History + Spielstatistiken |
| ARPAD | Aktiv | `/arpad` | Chatbot (Claude, Tool Runner) für Fragen zu den Rating-/Partiedaten — 4 feste Query-Tools, kein Text-to-SQL, braucht `ANTHROPIC_API_KEY`; siehe CLAUDE.md-Abschnitt „ARPAD (Chatbot)" |
| Partien-Detail | Test | `/games` | Alle Partien eines Spielers, filterbar |
| GM/IM Entwicklung | Test | `/titles` | Zeitreihe der Titelträger |
| QC Übersicht | QC | `/qc` | Jahres-/Monatsdetail zu Rating-Deltas |
| FIDE 2024 Korrekturen | QC | `/qc-corrections` | Analyse der einmaligen ELO-Anpassung März 2024 |

### Deploy-Status (Befund 27.08.2026)

**Läuft aktuell nicht auf dem VPS**, obwohl bisher hier als live dokumentiert:

| Geprüft | Befund |
|---|---|
| Docker-Container | Kein Container für `frontend/` (weder laufend noch gestoppt) — nur `orchestrator-dashboard-1` (Port 8050, Steuerung) und `orchestrator-worker-1` laufen |
| Port 8055 | Nicht offen (`ss -tlnp` auf dem VPS: nur 8050 lauscht) |
| Prozesse/Sessions | Kein `python`-Prozess, kein `screen`/`tmux` mit `frontend/app.py` |
| Traefik-Routing | Kein Router/Label mit `/analytics`-Pfad — der Host-Router `scelo.chesspit.net` aus `orchestrator/docker-compose.yml` matcht nur auf `Host()`, keinen Pfad, und zeigt ausschließlich auf `orchestrator-dashboard-1`. Das erklärt den HTTP 401 auf `/analytics`: das ist die BasicAuth-Antwort des Orchestrator-Dashboards, nicht das Analytics-Frontend |
| Code auf dem VPS | Liegt aktuell (`git pull`) unter `/opt/fide-scraper/frontend`, zuletzt per Commit vom 09.06. (QC-Seiten) bzw. neuere ARPAD/Karten-Commits im Repo — Code ist da, wird nur nicht ausgeführt |
| Deploy-Weg im Repo | **Keiner.** Kein `Dockerfile`, kein Eintrag in `docker-compose.yml`/`orchestrator/docker-compose.yml`, kein systemd-Unit, kein Cron — anders als Orchestrator-Dashboard/Worker, die beide über `orchestrator/Dockerfile` + `orchestrator/docker-compose.yml` mit `restart: unless-stopped` deployed sind |

**Vermutung:** wurde bisher nur ad hoc lokal oder per manuellem `python3 frontend/app.py` (Port 8055) für Screenshots/Demos gestartet und lief nie dauerhaft — daher auch kein Absturz-/Restart-Bedarf sichtbar. Für einen dauerhaften Live-Betrieb fehlt: ein `Dockerfile` (analog `orchestrator/Dockerfile`), ein Service-Eintrag mit `restart: unless-stopped` und ein Traefik-Router mit `PathPrefix(\`/analytics\`)`-Regel (eigener Host oder Pfad-Strip, da die App intern auf `/c`, `/dist` etc. statt `/analytics/c` erwartet) plus eigene oder geteilte BasicAuth. **Noch nicht umgesetzt — offener Punkt.**

---

## ⚠️ Backup-Status — Cron gebrochen seit 21./22.07., manuelle Lücke am 25.08. geschlossen

**Beide täglichen Cron-Backups auf dem VPS liefen seit 35 Tagen nicht mehr, ohne jede Fehlermeldung im Log.** Entdeckt bei einer Routine-Prüfung, nicht durch einen Vorfall. Am 25.08. manuell ein aktueller fidedb-Dump nachgezogen (siehe unten) — der Cron-Bug selbst ist **bewusst noch nicht gefixt** (User-Entscheidung: erstmal nur die Datenlücke schließen).

| Backup-Baustein | Letzter automatischer Erfolg | Status |
|---|---|---|
| VPS-Cron `backup_fide_vps.sh` (fidedb + `orchestrator`-Schema) | 2026-07-21 03:47 Uhr (907 MB) | 🔴 Cron tot seit 35 Tagen — **✅ Lücke am 25.08. per manuellem Dump geschlossen** |
| VPS-Cron `backup_db_vps.sh` (Kundenprojekt tunnelbliq, selbe Ursache) | 2026-07-21 17:53 Uhr | 🔴 Cron weiterhin tot — **bewusst nicht angefasst** (Kundendatenbank, User-Entscheidung 25.08.: nur fidedb) |
| Mac-Mini-Offsite-Pull (`pull_backup_macmini.sh` via launchd) | nie automatisch gelaufen | 🔴 `launchd`-Plist nie installiert; **✅ am 25.08. manuell ein aktueller Dump lokal abgelegt** (`~/backups/fide-scraper/vps/`), Automatisierung selbst weiterhin nicht eingerichtet |

**Root Cause — präziser als ursprünglich vermutet:** Es ist kein einfacher Namens-Rename, sondern eine **Infrastruktur-Konsolidierung am 2026-07-22**, exakt einen Tag nach dem letzten erfolgreichen Backup. An diesem Tag wurde der Container **`fide-tunnelbliq-shared-db`** neu angelegt (Docker-`Created`-Zeitstempel `2026-07-22T07:35:37Z`) — seither läuft die echte `fidedb` (14,67 Mio. `game_results`, 11 GB) **gemeinsam mit der `tunnelbliq`-Datenbank des Kundenprojekts** in diesem einen geteilten Postgres/TimescaleDB-Container, der auf dem Standard-Postgres-Port `0.0.0.0:5432` lauscht. Der alte, separate Container `fide-scraper-db-1` (existiert schon seit 13.05., läuft weiter, aber nur auf `127.0.0.1:5433`) ist dabei **nicht gelöscht, sondern praktisch leergelaufen** — er enthält nur noch 9 MB TimescaleDB-Katalogtabellen, keine einzige Zeile der eigentlichen Nutzdaten mehr.

Beide Backup-Skripte (`backup_fide_vps.sh` **und** `backup_db_vps.sh` für tunnelbliq) ermitteln den Ziel-Container per `docker ps --format '{{.Names}}' | grep '^timescaledb-'` — ein Namensmuster, das vor der Konsolidierung offenbar gepasst hat, seit der Umbenennung auf `fide-scraper-db-1` / `fide-tunnelbliq-shared-db` aber nicht mehr matcht. **Das erklärt auch, warum beide unabhängigen Skripte exakt am selben Tag synchron ausgefallen sind** — keine zwei getrennten Zufälle, sondern ein und dieselbe Infrastrukturänderung. Mit `set -euo pipefail` bricht die Pipeline (`grep` liefert exit 1) das Skript sofort bei der Container-Ermittlung ab, **bevor die `log()`-Funktion je aufgerufen wird** — daher keine Fehlerzeile im `backup.log`, obwohl der Cronjob selbst pünktlich feuert. Silent failure, kein Alerting vorhanden.

**⚠️ Falle beim manuellen Nachziehen:** Ein erster manueller Versuch am 25.08. lief versehentlich gegen den falschen (`fide-scraper-db-1`) Container und erzeugte einen scheinbar gültigen, aber nur 12 KB kleinen Dump ohne Nutzdaten — sofort an der Größe auffällig, verworfen und durch den korrekten Lauf gegen `fide-tunnelbliq-shared-db` ersetzt. **Bei einem künftigen Fix unbedingt gegen `fide-tunnelbliq-shared-db` zielen, nicht gegen `fide-scraper-db-1`.**

**2026-08-25 — manuelle Lücke geschlossen:**
- `docker exec fide-tunnelbliq-shared-db pg_dump -U fide -d fidedb -Fc` → `fidedb_2026-08-25T084823Z.dump`, **1,1 GB** (deutlich größer als die alten ~900-MB-Dumps, da die DB seit der Konsolidierung mitgewachsen ist), Laufzeit 3:50 Min, Eintrag in `/home/pit/backups/fide-scraper/backup.log`.
- Per `scp` auf diesen Mac gezogen nach `~/backups/fide-scraper/vps/fidedb_2026-08-25T084823Z.dump`; SHA-256 zwischen VPS- und lokaler Kopie verifiziert, identisch.
- **Nur fidedb** — die `tunnelbliq`-Kundendatenbank im selben Container wurde bewusst nicht gesichert (User-Entscheidung, nicht unser Datenbestand).
- Der Cron-Bug selbst (Container-Namensmuster) ist **weiterhin ungefixt** — nächster automatischer Lauf um 03:45 Uhr wird wieder silent fehlschlagen, bis das Skript korrigiert wird.

**Auswirkung der Lücke, falls nicht geschlossen worden wäre:** Rollback-Punkt bei einem VPS-Totalausfall wäre 35 Tage alt (21.07.) statt maximal 1 Tag gewesen — das hätte u. a. den kompletten P1/P2/P3-Monatsrefresh (16.08. fertig) und ~3,06 Mio. seit 29.07. gescrapte Partien gekostet. Die VPS-seitige 3-Dump-Rotation (`KEEP_DUMPS=3`) hatte unbemerkt nichts mehr zu rotieren.

**Noch offen, falls/wenn gewünscht:**
1. Container-Erkennung in `backup_fide_vps.sh` robuster machen (z. B. per Compose-Label/Image statt Namenspräfix), damit der tägliche Cron wieder greift.
2. `net.chesspit.fide-backup-pull.plist` tatsächlich auf dem Mac installieren (`launchctl load`), damit der Offsite-Pull automatisch läuft statt manuell.
3. Perspektivisch: einfaches Alerting bei `backup.log` ohne neuen Eintrag seit >24h.
4. `backup_db_vps.sh` (tunnelbliq) ist vom selben Bug betroffen, aber bewusst nicht Teil dieser Session — liegt außerhalb des fide-scraper-Projekts.

---

## Änderungen Session 2026-09-11 — MB-Rückgang untersucht + Live-Stand aktualisiert

| Was | Details |
|-----|---------|
| **MB-Rückgang aufgeklärt** | User bemerkte im Orchestrator-Dashboard sinkende Tages-MB und vermutete, dass zuletzt nur schwache Gruppen gescraped wurden. Bestätigt per Live-Query: `records_found`/Spieler und `mb_downloaded`/Spieler liegen im Band <1800 bei ~4,0 gegenüber ~8,8–12,0 bei ≥2200 — weniger als halb so viel Daten pro Spieler. ~72 % der pending Welt-Backfill-Gruppen liegen inzwischen im Band <1800 (starke Bänder in fast allen Ländern schon `done`), zusätzlich verstärkt durch die P0/P3-Läufe seit 06.09. (praktisch nur `<1800`). Kein technisches Problem: Run-Durchsatz/Tag blieb im normalen Rahmen (51–91), 0 failed-Gruppen. Details siehe eigener Abschnitt oben. |
| **P1/P2/P3-Zyklus (Aug-Periode) fertig** | Kompletter Durchlauf (49/49 Batches) am 06.09. ~13:54 Uhr abgeschlossen. |
| **P0-Neuzugangs-Tier: Fortschritt geprüft** | ~71 % der Bänder fertig (104/145), nur noch 2.586 aktive Spieler wirklich nie angefasst (von ursprünglich ~26.000). Die einmalige `dc_update_1`-Leihgabe von 30 P0-Gruppen (06.09.) ist komplett durchgelaufen, Thread lief danach automatisch zurück auf DACH/FRA-Backfill — kein manueller Eingriff nötig. |
| **Restlaufzeit neu gerechnet** | `dc_in` (2,9 Gruppen/Tag) hat `dc_de` (3,9/Tag) als langsamsten Thread überholt — beide bleiben unverändert unter Verdacht (Proxy-/Tarpit), noch nicht diagnostiziert. Gesamtende Welt-Backfill weiterhin grob Ende Dezember 2026. |
| **Games gesamt** | 16.020.525 (+1.348.377 seit 25.08., ~79 Tsd./Tag) — Rückgang ggü. der 25.08.-Spanne (85–120 Tsd./Tag) ist derselbe ELO-Mix-Effekt wie oben, kein separates Problem. |
| **Dashboard-Grid: „komische Bänder" bei Spanien aufgeklärt + gefixt** | User meldete zwei auffällig breite Bänder in Spaniens Scraping-Grid (ELO 1664–1844 und 1845–2299, je ~5.400 Spieler statt der üblichen ~100–250). Ursache: Überbleibsel des vor dem 02.07. abgelösten Single-Thread-Refreshs `dc_update` — legte im Juni pro Föderation eine `ELO 0–2299/Jahr 2026`-Gruppe an, bei zu großen Populationen (ESP ~16.228, IND ~14.574) sogar 2–3 gleich breite Perzentil-Drittel statt einer. **Live-Check bestätigt: betrifft 73 von ~75 Föderationen (77 Zeilen insgesamt)** — überall dort, wo `dc_update` gelaufen ist, liegt die alte Zeile ungefiltert zwischen den viel feineren, laufenden Jahres-/ELO-Bändern und würde im Grid auffallen; bei ESP/IND am stärksten (3 statt 1 Zeile, mitten in der Sequenz). Alle 77 Gruppen sind seit 07.–15.06.2026 `done`, Thread `dc_update` läuft in keinem aktiven Pool mehr — **kein Effekt auf laufendes Scraping**, nur Dashboard-Optik. **Fix (nicht-destruktiv):** `orchestrator/store.py::query_grid()` filtert jetzt `thread_affinity != 'dc_update'` (Commit `f07f2e8`), gebaut + deployed (`docker compose build dashboard` + `up -d --force-recreate --no-deps dashboard`, Worker unangetastet). Die 77 DB-Zeilen selbst wurden **nicht gelöscht** (FK `scrape_runs.group_id` ist `NO ACTION` — Löschen hätte auch die zugehörigen `scrape_runs` mitreißen müssen und damit Juni-Historie aus dem Bericht-Tab entfernt), stattdessen mit erklärendem `notes`-Feld versehen. |

---

## Änderungen Session 2026-08-25 — VPN-bedingter Verbindungsausfall + Routine-Check + Backup-Lücke entdeckt

| Was | Details |
|-----|---------|
| **VPS kurzzeitig unerreichbar** | User konnte Dashboard nicht erreichen; Diagnose zeigte VPS/SSH/DB/HTTPS allesamt tot, aber DNS + generelles Internet ok, Traceroute erreichte sauber Hostingers Netz und starb dann — sah wie VPS-Totalausfall aus. Ursache war ein **eingeschaltetes VPN auf dem Mac Mini**; nach Ausschalten sofort wieder alles erreichbar (Ping, SSH, Tunnel, Dashboard). Kein VPS-seitiges Problem. |
| **Orchestrator-Gesundheitscheck** | 0 failed-Gruppen, 462/462 `scrape_runs` der letzten 7 Tage `success`, alle 9 DC-Threads innerhalb 24h aktiv, Worker-Log zeigt nur selbstheilende Proxy-Retries (kein echter Fehler), Disk 52 % belegt. Bestätigt: **Monatsrefresh P1/P2/P3 tatsächlich am 16.08. ~14:56 Uhr fertig geworden**, wie am 12.08. projiziert. |
| **Backup-Lücke entdeckt (⚠️ ungelöst)** | Beide VPS-Cron-Backups (fidedb + tunnelbliq) seit 21.07. tot durch einen Container-Namens-Match-Bug, der das Skript beim Start silent abbrechen lässt; die Mac-Mini-Offsite-Kopie lief noch nie automatisch (launchd-Plist nie installiert). Details siehe eigener Abschnitt „Backup-Status" oben. Noch nicht gefixt — Rückfrage beim User, wie vorgegangen werden soll. |
| **Hochrechnung Restlaufzeit aktualisiert** | `dc_de` bleibt/verschärft sich als Flaschenhals (3,6 statt 5,0 Gruppen/Tag), Gesamtende jetzt auf **Ende Dezember 2026** revidiert (ggü. ~19.11. am 29.07.) — Ursache weiterhin nicht diagnostiziert. |

---

## Änderungen Session 2026-08-11/12 — dc_update_1 von DACH/FRA-Backfill befreit + Monatsrefresh neu gestartet

| Was | Details |
|-----|---------|
| **`dc_update_1` entlastet** | 233 pending AUT/GER/SUI-Gruppen zurück auf `thread_affinity='dc_dach'`, 134 pending FRA-Gruppen 1:1 auf `dc_dach`/`dc_mx` verteilt (ID-Parität). Ursache: diese Backfill-Gruppen hatten niedrigere (=dringlichere) `priority`-Werte als die P1/P2/P3-Batches (525.667+) — der Update-Thread kam dadurch strukturell nie an den eigentlichen Monatsrefresh. |
| **Juli-Periode importiert** | `standard_aug26frl.zip` (Periode 2026-08-01, enthält Juli-Partien) importiert — 562.979 Zeilen `rating_history`. |
| **Monatsrefresh neu gestartet** | `orchestrator/reset_monthly_refresh.py` angestoßen — P1 (2 Gruppen)/P2 (7)/P3 (40) auf `pending` zurückgesetzt. Live-Stand 12.08. ~08:52 Uhr: **P1 2/2 fertig, P2 4/7 fertig + 1 läuft, P3 0/40** — siehe Detailtabelle im Orchestrator-Abschnitt oben. |
| **P3-Bänder übervoll (bekannt, unangetastet)** | Live-Nachzählung 11.08.: P3-Population von 118.066 (Stand 02.07., Bänder erzeugt) auf 183.604 gewachsen (+55,5 %, getrieben vom parallel laufenden Welt-Backfill) — alle 40 Bänder liegen jetzt bei 3.694–5.187 Spielern statt Zielgröße. Kein Korrektheitsproblem (Bänder lückenlos 0–2299, Worker fragt live gegen DB ab), aber Gesamtlaufzeit für den P1/P2/P3-Durchlauf revidiert von ~8 auf ~13 Tage. **User-Entscheidung:** vorerst nichts anfassen (keine Neu-Erzeugung der Bänder), in ein paar Tagen Fortschritt erneut prüfen. |
| **Restlaufzeit neu gerechnet (12.08. nachmittags) — 13-Tage-Schätzung zu pessimistisch** | Aus den echten `scrape_runs`-Zeitstempeln von P1+P2 (9 fertige Bände, 23.670 Spieler in 18h36min) ergibt sich ein Ist-Tempo von ~2.080 Spieler/Std. reine Scrape-Zeit bzw. ~1.270 Spieler/Std. blended inkl. einer beobachteten ~7h12min-Nachtpause (21:48–05:00 Uhr) — deutlich über der 655/Std.-Design-Annahme, auf der die 13-Tage-Schätzung beruhte. P3 hat aber gerade erst begonnen (0 Bänder fertig, 1 läuft seit 113+ Min., länger als der P1/P2-Schnitt) — erstes Indiz, dass P3-Bänder tatsächlich wie oben vermerkt übervoll sind. Neuprojektion für die restlichen 117.898 P3-Spieler: **~4 Tage** (optimistisch, P1/P2-Tempo) bis **~6 Tage** (vorsichtig, Tempo des ersten P3-Bandes) → **revidiertes Gesamtende grob 16.–18.08.** statt 24.08. Details/Rechnung siehe Abschnitt „Restlaufzeit neu gerechnet" im Orchestrator-Abschnitt oben. Belastbarer erst mit mehreren abgeschlossenen P3-Bändern. |

---

## Änderungen Session 2026-07-29 — Pi-Bestand umverteilt + Hochrechnung aktualisiert

| Was | Details |
|-----|---------|
| **794 pending Pi-Gruppen umverteilt** | Per SQL-Update auf die etablierten Föderations-Heimat-Threads umgehängt (`device=NULL`, `thread_affinity` gesetzt), analog zum FRA-Split vom 22.07. `dc_update_1` bewusst ausgenommen (wird in Kürze für den monatlichen P1/P2/P3-Refresh gebraucht). Details siehe Raspberry-Pi-Abschnitt oben. Neuer Flaschenhals unverändert `dc_de`/`dc_in`, Gesamtende jetzt ~19.11.2026 (statt ~08.11. ohne Pi-Bestand). |
| **Hochrechnung Restlaufzeit** | Neuer Abschnitt oben, auf Basis 7-Tage-Durchsatz je Thread. Auffällig: `dc_de` (4,9 Gruppen/Tag) und `dc_in` (4,6/Tag) laufen fast 4× langsamer als `dc_us` (19,7/Tag) — noch nicht diagnostiziert, ob Proxy-/Tarpit-Ursache oder strukturell, lohnt Prüfung. |
| **DataImpulse-Kalibrierung: Diskrepanz gefunden** | Unser `mb_downloaded`-Zähler zeigt seit der Migration (16.07.) nur ~1,2 GB, DataImpulse-Dashboard zeigt real **11 GB** (Stand 29.07., 13 Tage) — Faktor ~9 Unterschied, Richtung entgegen der bisherigen Doku-Annahme („mb_downloaded überschätzt den Traffic"). Vermutlich TLS-Handshake/Verbindungsaufbau pro Request bei Residential-IP-Rotation, der nicht mitgezählt wird. **Interner Zähler taugt nicht zur Kostenkontrolle**, nur das DataImpulse-Dashboard ist verlässlich. Hochrechnung bis Fertigstellung (~19.11., 113 Tage ab 29.07.) auf Basis der realen 11 GB: **~100–160 GB Gesamtverbrauch seit Migration** (0,85–1,3 GB/Tag je nach Trend), deutlich mehr als die ursprünglich angenommenen 50 GB. |

---

## Änderungen Session 2026-07-22 — Doku-Nachzug + AUT/SUI-Prioritäts-Fix

Die Doku hatte seit 16.07. keinen Eintrag mehr (6 Tage Lücke); Live-Zahlen oben aktualisiert (10,82 Mio Partien, +852k seit 07.07.; Queue 0 failed). Zwei konkrete Ergebnisse dieser Session:

| Was | Details |
|-----|---------|
| **Raspberry Pi abgeschaltet** | User-Entscheidung: Pi bis auf Weiteres außer Betrieb, kein laufendes Thema mehr. Stand 452/1246 done bleibt eingefroren (siehe Pi-Abschnitt oben), `device='raspi'`-Gruppen (452 done/794 pending) unangetastet in der Queue liegen gelassen. |
| **AUT/SUI: Prioritäts-Lücke 2016–2019 gefixt** | User bemerkte, dass DACH-Threads bereits bei Jahr 2015 arbeiten, während für AUT/SUI in 2016–2019 noch offene (pending) Gruppen existieren. Live-Diagnose bestätigt: 55 Gruppen (niedrige ELO-Bänder, ELO 1400–1825, je Föderation/Jahr) trugen aus einem früheren Umbau eine Alt-Priorität im 50.200er-Bereich, während der Rest des DACH-Backfills (alle ELO-Bänder für 2015 abwärts sowie die höheren ELO-Bänder 2016–2019) längst im 1.000–1.400er-Bereich läuft bzw. bereits `done` ist — dadurch blieben diese 55 Gruppen strukturell abgehängt (niedrigere Priorität = später dran), obwohl sie chronologisch vor dem aktuellen Bearbeitungsstand (2015) liegen. **Fix:** Priorität der 55 betroffenen `pending`-Gruppen (AUT+SUI, Jahr 2016–2019, `thread_affinity` unverändert dc_dach/dc_update_1) neu auf 900–954 gesetzt (2019 zuerst, dann 2018→2016, je Föderation/ELO-Band absteigend) — damit vor dem bisherigen nächsten Batch (Priorität 1158/1212) einsortiert. Reine Priority-Umnummerierung in `orchestrator.scrape_groups`, keine Gruppen neu angelegt oder Status geändert. |
| **FRA: 0 %-Fortschritt diagnostiziert + auf 3 Threads verteilt** | User fragte, warum Frankreich bei 0 % steht, während NED/BEL/LUX (alle auf demselben Thread `dc_mx`) schon fast fertig sind. Diagnose: FRA war korrekt zugeordnet und prioritätsmäßig nicht blockiert — aber mit 669 offenen Gruppen (Jahre 2021–2026, Vor-2020 korrekt `skipped` laut Jahresziel) allein für einen einzigen sequenziellen Thread viel zu groß (NED+BEL+LUX+MEX zusammen nur noch 56 offen). Die 669 pending-FRA-Gruppen wurden per Round-Robin (`id % 3`) zu gleichen Dritteln auf `dc_mx`/`dc_dach`/`dc_update_1` verteilt (je 223). Die 112 FRA-Gruppen für Zieljahr 2020 liegen separat auf `device='raspi'` (Pi abgeschaltet) und sind von diesem und dem folgenden Fix nicht betroffen. |
| **FRA-Split nachgebessert: Priorität verzahnt statt angehängt** | Erste Version des Splits behielt FRAs alte (sehr hohe = späte) Priorität bei — dadurch stand FRA auf `dc_dach`/`dc_update_1` weiterhin komplett *hinter* dem gesamten bestehenden Bestand (222 bzw. 322 Gruppen) an, teils über Wochen, zumal `dc_update_1` künftig verstärkt für den P1/P2/P3-Monatsrefresh gebraucht wird. User-Einwand berechtigt. **Fix:** Bestehender Bestand (AUT/SUI/GER, ohne die separaten P1/P2/P3-Refresh-Reste) und FRA je Thread 1:1 im Prioritäts-Ranking verzahnt (jede zweite abgearbeitete Gruppe ist jetzt FRA) statt FRA ans Ende zu hängen. Dadurch kommt auf jedem der drei Threads die erste FRA-Gruppe direkt als zweite Gruppe nach der aktuell laufenden dran — nicht erst nach Wochen. Gesamtdauer bis FRA komplett fertig ist unverändert (gleiche Arbeitsmenge), aber der Fortschritt verteilt sich jetzt gleichmäßig statt in einem Rutsch am Ende. |

---

## Änderungen Session 2026-07-16 (nachmittags) — Jahresziele fürs rückwirkende Scraping

Peter hat pro Region festgelegt, wie weit der Backfill zurückgeht (jeweils einschließlich Zieljahr); umgesetzt via `orchestrator/set_backfill_targets.py` (mit `--dry-run`):

| Region | Föderationen | Backfill bis |
|---|---|---|
| DACH | GER, AUT, SUI | **2012** |
| Nordamerika | USA, CAN, MEX | **2018** |
| Asien-Pazifik | CHN, VIE, AUS | **2018** |
| Rest der Welt | alle übrigen | **2020** |

- ~13,9k pending/failed-Gruppen unter Zieljahr → `skipped` mit `notes='Jahresziel 2026-07: Backfill bis <J>'` (**Rollback:** dieselben notes zurück auf pending setzen; nichts un-skippt automatisch)
- ~3,3k Plan-Gruppen ohne `thread_affinity` (u.a. FRA, RUS, ganz Afrika/Südamerika) wurden per **Region + Lastausgleich** auf die DI-Threads verteilt (Claiming ist strikt affinity-basiert — ohne Zuweisung würde nie gescrapt); Geräte-Pools (`device` gesetzt) unangetastet
- `store.query_laender_data`/`query_federation_years`: Plan-Kennzahlen (Zeitraum, Nenner der %-Werte) zählen `skipped` nicht mehr mit — Karte + Bericht Länder messen jetzt Fortschritt **gegen die Jahresziele**; der Karten-Hover „Backfill geplant bis" zeigt das Ziel pro Land

---

## Änderungen Session 2026-07-16 — Proxy-Migration Webshare → DataImpulse Residential

Webshare (statische DC-IPs) ist bis auf Weiteres abgelöst — FIDE tarpittet die Pools sukzessive (MENA/Asien zuletzt 100 % tot, `dc_in`/`dc_hk`/`dc_ae` deaktiviert). Neuer Provider: **DataImpulse Residential Rotating** (~10 GB Pay-as-you-go, $1/GB, neue IP pro Request).

| Was | Details |
|-----|---------|
| Architektur | unverändert — die 10 DC-Threads bleiben (Queues, Föderations-Routing, Zeitfenster); reine Config-Migration, kein Code-Umbau. Labels `DC-*` → `DI-*` |
| Gateway | `orchestrator/dataimpulse_gateway.txt` (eine Zeile `gw.dataimpulse.com:823`, committed — kein Secret); ersetzt die 4 git-ignorierten `webshare_proxies*.txt` |
| Geo | Country-Targeting per Login-Suffix `__cr.xx` pro Thread, Land = Thread-Timezone (Requests aus einem Land zu dessen Tageszeiten); Mapping-Tabelle in `docs/scraping_orchestrator.md` |
| Env | neue Variablen `PROXY_DI_PASSWORD` + `PROXY_DI_USERNAME_{DE,IN,GB,US,HK,ES,MX,AE,CH}` (siehe `.env.example`); `PROXY_DC_*` entfällt |
| Budget | keine Code-Bremse (bewusst) — Kontrolle über DataImpulse-Dashboard; `mb_downloaded` = dekomprimierte Bytes, überschätzt den abgerechneten Traffic |
| Deploy | VPS: `.env` ergänzen → `git pull` → `docker compose build && up -d` (profiles.yaml ist ins Image gebacken, Mounts geändert) → alle 10 Threads im Dashboard aktivieren |

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

**Deployed + verifiziert 2026-07-04 ~08:05 UTC:** Worker gestoppt → Build → Migration (24.714 Gruppen + 4.234 Runs, alle Status-Zähler identisch) → `up -d --no-deps`. Neuer Code im Container bestätigt, Worker claimt aus PG (7 unterbrochene running → pending → 6 neu geclaimt), Dashboard HTTP 200 ohne Fehler, Backup-Testlauf OK (856-MB-Dump enthält Schema `orchestrator` inkl. Sequenzen), kein Pi-Sync-Cron vorhanden. Alte DB archiviert als `/data/scraper.db.migrated-20260704` (+`-wal`/`-shm`).

**Nachwirkung entdeckt (abends):** Das Löschen von `merge_pi_status.py`/`sync_pi_to_vps.sh` bricht den Raspberry-Pi-Status-Sync (Pi zielte auf die jetzt archivierte VPS-`scraper.db`) — siehe Warnhinweis im Pi-Abschnitt oben. Noch nicht behoben.

---

## Änderungen Session 2026-07-04 (Fortsetzung, nachmittags/abends)

| Was | Details |
|-----|---------|
| **top_gap_2300 abgeschlossen** | 170 fehlende Top-Spieler (ELO≥2300) geseeded + Mac-Mini-Backfill 2008-04→2026-06; 121/170 (71%) haben jetzt Partien, 49 endgültig leer. Details siehe Abschnitt „Top-Spieler-Lückenanalyse" oben. |
| **Alle 10 DC-Scraper aktiviert** | User-Entscheidung ~12:20 Uhr: DC-DE/DC-US/DC-ES zusätzlich zu den bereits laufenden 7 eingeschaltet, Worker neu gestartet. Sauberer Respawn (alle Threads claimten sofort neue Gruppen), Health-Check nach mehreren Stunden: nur 5 echte 429/Cooldown-Events in 4h, 0-mal Direktfetch-Fallback — kein Anzeichen von IP-Blocking. P3-Fortschritt dadurch spürbar beschleunigt (19/40 → 27/40 Batches an einem Nachmittag). |
| **USA/2019-Anomalie aufgeklärt** | Einzige `failed`-Gruppe der Queue diagnostiziert und aufgelöst — Details siehe eigener Abschnitt oben. Queue steht jetzt bei 0 failed. |
| **Backup-Skript gehärtet** | `scripts/pull_backup_macmini.sh`: `KEEP_MIN=2`-Schutz ergänzt (die 2 neuesten lokalen Dumps werden nie gelöscht, unabhängig vom Alter — schützt bei langem Mac-Aus/Urlaub); Bash-3.2-kompatibel (kein `mapfile`, macOS liefert nur diese uralte Version aus Lizenzgründen). |
| **Projekt umgezogen** | Live (ohne Session-Neustart) von `/Users/macminipit/Projekte/fide-scraper` nach `/Users/macminipit/PARA/1_Projects/fide-scraper` (PARA-Struktur). launchd-Plist + Claude-Memory-Ordner mitgezogen, `.venv`/Git/Tests danach verifiziert. **Alle Pfade in dieser Doku und in Skripten beziehen sich ab jetzt auf den neuen Standort.** |

---

## Änderungen Session 2026-07-06 — Projekt erneut umgezogen (PARA-Reorg: Git-Repos separiert)

| Was | Details |
|-----|---------|
| **Projekt umgezogen** | Von `/Users/macminipit/PARA/1_Projects/fide-scraper` nach `/Users/macminipit/PARA/1_Projects_Git/fide-scraper` — PARA-Reorg trennt git-Repos (`1_Projects_Git`) von sonstigen Projektordnern (`1_Projects`). launchd-Plist (`net.chesspit.fide-backup-pull`) + Claude-Memory-Ordner mitgezogen (umbenannt, nicht kopiert — Lehre aus einem Nachbarfall, wo ein paralleles Kopieren zu einem divergierenden zweiten Memory-Ordner führte). Sofort verifiziert: `runs=1`, `last exit code=0`, frischer Pull-Log-Eintrag. **Alle Pfade in dieser Doku und in Skripten beziehen sich ab jetzt auf den neuen Standort.** |
| **Mac-Mini-Offsite-Pull-Bug gefunden + behoben** | `launchctl print` zeigte `runs=0` seit Setup (03.07.) — der Mac Mini fährt nachts komplett runter (echter Shutdown, kein Sleep) und bootet erst gegen 08–09 Uhr, das reine `StartCalendarInterval` auf 07:30 lief daher nie. Fix: `RunAtLoad` ergänzt (feuert bei jedem Boot), Fallback-Zeit auf 09:30 verschoben. |
| **`.venv` nach Umzug neu gebaut** | Venvs sind pfadgebunden und überleben Ordner-Umzüge nicht — am neuen Standort fehlte `.venv` komplett; neu erstellt aus `scraper/requirements.txt`, Tunnel + DB-Verbindung danach verifiziert. |

### Abend: Zwei neue QC-/Analyse-Werkzeugpakete (Fable-Test-Branches, beide gemerged)

| Was | Details |
|-----|---------|
| **Reconciliation-Tool** (Ziel: stimmen die *Inhalte*?) | `scripts/reconcile_ratings.py` + 20 Tests — prüft, ob Σ gescrapte Partie-Änderungen die Deltas der offiziellen Listen erklärt (Regel-Schicht: Toleranz ±1, rollierendes 2/12-Monats-Fenster, FIDE-Korrekturen, Pre-2008; Fuzzy-Namensabgleich mit Confidence-Buckets). Validierung female_top+male_control: 13.235 Fenster, 99,9 % erklärt. Die 7 unerklärten Fenster per erzwungenem Re-Scrape (8 Perioden) geprüft: identische Daten → FIDE-seitige Listen-Inkonsistenzen, keine Scraping-Lücken. |
| **Orchestrator: Ist-Soll-Analyse + Redesign** | `docs/orchestrator_redesign_2026-07.md` — Top-3-Schwachstellen nach Review #5: (1) Completion behauptungs- statt evidenzbasiert, (2) Coverage-Blindheit entlang der Ground Truth, (3) Multi-Device-Queue ohne claimed_by. Phasenplan B (claimed_by → Pi), C (Perioden-Retry-Semantik: error/429-Zeilen blockieren Retry strukturell dauerhaft), D (optional). |
| **Coverage-Report** (Ziel 2) | `orchestrator/coverage.py` + `scripts/coverage_report.py` — ground-truth-basiert (players ⨯ scrape_periods ⨯ game_results), Dimensionen Federation / analysis_group / ELO-Band × Jahr. analysis_group-Coverage gab es vorher nirgends. Machte das Zukunftsmonate-Artefakt erstmals messbar (elite_2600/2026: 100,8 %). |
| **Integritätsprüfung** (Ziel 3, False Positives) | `orchestrator/integrity.py` + `scripts/verify_scrape_integrity.py` — 5 benannte Checks (Registry, erweiterbar), report-only, Exit-Code für Cron. **Erster Live-Lauf (~17 Min, kompletter Bestand): Perioden-Buchführung 100 % konsistent** (0 Findings bei ok_without_games / no_data_with_games / blocked_error_rows / orphan_games); done-Gruppen: 294 weiche Findings (2–4 %), Stichprobe bestätigt Rating-Drift (AUS-Band: exakt 6 nachgedriftete Spieler = 72 Kombos). Lehre eingebaut: update_only-Gruppen vom Audit ausgenommen (Erstlauf hatte 73 falsch-harte Treffer = alte dc_update-Batches). |
| **Tests** | conftest um `data_db`-Fixture erweitert (public-Tabellen minimal in Test-DB); Gesamtsuite 142 passed + bekannter Alt-Fehler `test_retry_on_429`. Test-PG braucht Wegwerf-Container (fide-User darf auf VPS-PG kein CREATE DATABASE). |
| **Beide Branches gemerged + gelöscht** | User-Entscheidung am Abend: erst `fable-test/orchestrator-redesign`, dann `fable-test/reconciliation-check` nach master (je konfliktfrei/Fast-Forward), gepusht bis `62dac9e`, Branches lokal + remote entfernt. |
| **Offene Kür** (kein Handlungsdruck) | `VerifiedInconsistencyRule` für die 7 geprüften Reconciliation-Fälle; Ergebnispersistenz analog `qc_rating_check`; Reconciliation/Integritätsprüfung als Schritt 4 in `monthly_update.sh` (Vormonat prüfen); Redesign-Phasen B + C; Bulk-Prioritäts-CLI. |

---

## Reconciliation-Tool (Session 2026-07-06) — ✅ gemerged

Neues eigenständiges QC-Tool gebaut und gegen die Live-DB validiert. Zunächst auf Branch `fable-test/reconciliation-check` entwickelt (Commit `73e3a96`), **am selben Abend nach master gemerged** — zusammen mit den Orchestrator-Tools aus `fable-test/orchestrator-redesign` (Coverage-Report + Integritätsprüfung, siehe `docs/orchestrator_redesign_2026-07.md`); beide Test-Branches danach gelöscht.

| Was | Details |
|-----|---------|
| **`scripts/reconcile_ratings.py`** | Prüft pro Spieler, ob Σ gescrapte `rating_change_weighted` die Differenzen der offiziellen Listen (`published_rating`) erklärt. Zwei Kommandos: `run` (Audit, `--group/--tolerance/--windows/--csv`) und `verify-names` (Fuzzy-Abgleich Gegnernamen ↔ offizielle Liste, rapidfuzz, Buckets exact/high/uncertain/unmatched — nichts wird still verworfen). |
| **Erweiterbare Regel-Schicht** | Benannte Regeln mit `adjust()`/`explain()`-Hooks: `KnownCorrectionRule` (rating_corrections, z. B. FIDE März 2024), `NoGameDataRule` (Fenster vor 2008-04 unprüfbar), `ToleranceRule` (±1 Elo), `RollingWindowRule` (Monatsverschiebung, kumulativ 2/12 Monate, überbrückt erklärte Ruhemonate). Neuer Sonderfall = neue Regelklasse, Kern bleibt unberührt. |
| **20 Tests ohne DB** | `tests/test_reconcile_ratings.py` — Toleranz, Verschiebung, Namensabgleich, Regel-Erweiterbarkeit; laufen in <1 s ohne PG. |
| **Validierungslauf** | female_top + male_control (71 Spieler, 13.235 Fenster): **99,9 % erklärt**, 7 unerklärt. Namensabgleich-Stichprobe (100 Namen gegen 1,83 Mio): 89 % exact, 4 % high, 5 % uncertain, 2 % unmatched. |
| **7 offene Fenster nachgescrapt → FIDE-seitig bestätigt** | Alle 8 betroffenen Perioden (Berkvens, Bodek, Grib×2, Saduakassova, Mihalichenko×3) per erzwungenem Re-Scrape (scrape_periods-Delete + Backfill, Live-DB!) neu geholt: **identische Daten, 0 Fehler** — keine Scraping-Lücke, sondern Inkonsistenzen in den offiziellen FIDE-Listen selbst (5 von 7 Residuen nur 1,1–1,4 Elo ≈ Rundung; Mihalichenko 2009 +5,7 vermutlich stille FIDE-Anpassung). |

**Offene Entscheidungen (bei Merge):** Ergebnispersistenz in Tabelle analog `qc_rating_check`; `VerifiedInconsistencyRule` für die 7 geprüften Fälle; Einbau als Schritt 4 in `monthly_update.sh` (Vormonat prüfen, da Refresh Tage läuft).

### Orchestrator-Wartung (Mac Pro, nachmittags) — Live-Snapshot ~16:40 UTC

**DB-Stand:** 9.911.808 Partien · 9.009.037 gescrapte Perioden · Queue: 4.252 done / 20.453 pending / 9 running / **0 failed**.

| Was | Details |
|-----|---------|
| **Monats-Refresh (P1/P2/P3) neu gestartet** | Der vorige Update-Zyklus (07.06.–06.07.) war komplett `done` (49 Batches: P1 2 / P2 7 / P3 40). Auf Wunsch per `python orchestrator/reset_monthly_refresh.py` neu angestoßen (49 done→pending, Jahr auf 2026 gezogen = No-Op, Welt-Backfill unangetastet). Der Lauf scrapt **2026-01…2026-06** nach (Juli erst ab August, `valid_periods_for_year()` deckelt beim Vormonat) — im Kern ein **schneller Re-Scrape** bereits erfasster (fide_id, Periode)-Kombis, zieht alle Tiers einheitlich bis Juni. P1+P2 (~21k Spieler) in ~20 Min durch; Restlaufzeit für P3 grob 1–3 h. **Kein Cron dafür** — der Reset ist reine Handarbeit (nur DB-Backups sind geplant). |
| **Failed-Gruppe #6449 entklemmt** | BEL/2022/1517–1570, `server closed the connection unexpectedly` (retries=1, terminal) → `pending`, retries=0, notes=NULL. Wird vom Worker neu versucht. Queue danach 0 failed. |
| **DACH-Backfill auf 2 Threads verdoppelt** | `dc_update_1` (Slot 108) ist infrastrukturell ein **Klon von `dc_dach`** (gleicher Europa-Proxy-Pool, `semi_conservative`, Europe/Berlin) und nach dem monatlichen Update faktisch arbeitslos. Deshalb **~50 % der pending DACH-Gruppen von `thread_affinity='dc_dach'` auf `'dc_update_1'` umgetaggt** (nur `status='pending'`, Split nach Gruppen-ID-Parität: gerade IDs → dc_update_1). Ergebnis: `dc_dach` 460 Gr./75.290 Spieler · `dc_update_1` 596 Gr./101.080 Spieler. Beide Threads ziehen jetzt parallel aus dem DACH-Bestand → grob **~2× Durchsatz (~8–9 statt ~17 Tage)**. `dc_update_1` beendet per priority zuerst die restlichen Update-Gruppen, wechselt dann automatisch auf DACH. **Kein Profil-Edit / Neustart nötig, voll reversibel** (zurücktaggen). ⚠️ Drei Europa-Threads (dc_uk + dc_dach + dc_update_1) teilen sich nun denselben Proxy-Pool — bei steigenden 429/Timeouts Split zugunsten dc_dach verschieben. |
| **Gotcha bestätigt: `running`-Status = In-Flight-Lock** | Der Worker markiert `scrape_groups.status='running'` als Lock und schreibt eine `scrape_runs`-Zeile **erst bei Abschluss** (success/failed). Eine `running`-Gruppe **ohne** zugehörigen `running`-Run in `scrape_runs` ist daher **normal**, kein verwaister Lock/Absturz. `last_run_at` = Claim-Zeitpunkt (streut bei parallelen Threads über Stunden). Nicht als „stuck" fehldeuten und zurücksetzen — erst Worker-Log prüfen. |

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
