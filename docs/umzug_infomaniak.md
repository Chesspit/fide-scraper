# Runbook: Umzug Hostinger → Infomaniak

Neuer Server: Infomaniak VPS Lite „ELO" (ov-31afa4), `179.237.106.106` / `2001:1600:18:20b::64`,
2 CPU / 4 GB RAM / 60 GB, Ubuntu 24.04. Alter Server: Hostinger `187.124.181.116` (bleibt wegen n8n).

Stack auf dem neuen Server: `deploy/infomaniak/docker-compose.yml` — `db` (TimescaleDB 2.26.2-pg16,
Container `fide-db`), `traefik`, `dashboard`, `worker`, `frontend`. Secrets nur in `/opt/fide-scraper/.env`
(Symlink `deploy/infomaniak/.env`).

> **Regel: Nie zwei Worker gleichzeitig.** Bis zum Umschalten läuft auf Infomaniak alles außer `worker`.

## Zugang

| Gerät | Weg |
|---|---|
| Mac Mini | `ssh pit@179.237.106.106` (Key `id_ed25519`) · Notfall: `ssh elo-infomaniak` (User `ubuntu`, Infomaniak-Key `~/.ssh/infomaniak_elo`, Kopie im Schlüsselbund) |
| MacBook Pro | eigenen Public Key in `/home/pit/.ssh/authorized_keys` eintragen |

Firewall doppelt: Infomaniak-Panel (nur 22/80/443, IPv4 + IPv6) und ufw. Docker-Ports der Dienste
hängen nur an `127.0.0.1` (Docker umgeht ufw).

## Phase 2 — Grundeinrichtung ✅ 01.10.2026

```bash
scp deploy/infomaniak/bootstrap.sh keys.pub elo-infomaniak:/tmp/
ssh elo-infomaniak 'sudo bash /tmp/bootstrap.sh /tmp/keys.pub'
```
Updates, Swap 4 GB, Docker, Benutzer `pit`, SSH ohne Passwort/Root, ufw, fail2ban, Repo-Clone.
Zeitzone bleibt UTC (Cron 03:45 und Dump-Namen wie auf Hostinger).

## Phase 3 — Probe-Umzug (Hostinger läuft weiter)

```bash
# Mac Mini: neuesten Dump hochladen
scp ~/backups/fide-scraper/vps/fidedb_<neuester>.dump pit@179.237.106.106:/home/pit/backups/restore.dump
rsync -a pit@187.124.181.116:/opt/fide-scraper/data/ /tmp/fide-data/ && rsync -a /tmp/fide-data/ pit@179.237.106.106:/opt/fide-scraper/data/
rsync -a frontend/*.parquet pit@179.237.106.106:/opt/fide-scraper/frontend/

# Server
cd /opt/fide-scraper/deploy/infomaniak
docker compose up -d db            # initdb/10-fide.sh legt Rolle fide + fidedb + Extension an
```

Restore (TimescaleDB-Reihenfolge, Dauer messen):
```bash
docker exec -i fide-db psql -U postgres -d fidedb -c "SELECT timescaledb_pre_restore();"
time docker exec -i fide-db pg_restore -U postgres -d fidedb --no-owner --role=fide -j 2 < /home/pit/backups/restore.dump
# -j funktioniert nicht mit stdin → Datei vorher in den Container kopieren:
#   docker cp /home/pit/backups/restore.dump fide-db:/tmp/r.dump
#   docker exec fide-db pg_restore -U postgres -d fidedb --no-owner --role=fide -j 2 /tmp/r.dump
docker exec fide-db psql -U postgres -d fidedb -c "SELECT timescaledb_post_restore();" -c "ANALYZE;"
```
Warnungen zu zirkulären FKs (hypertable/chunk) sind normal.

Prüfen — gleiche Zahlen wie Hostinger zum Dump-Zeitpunkt:
```sql
SELECT (SELECT count(*) FROM game_results)  gr, (SELECT count(*) FROM scrape_periods) sp,
       (SELECT count(*) FROM rating_history) rh, (SELECT count(*) FROM players) pl,
       (SELECT count(*) FROM orchestrator.scrape_groups) sg;
SELECT fn_elo_band(2437), fn_fide_expected(100);
```

Dienste ohne Worker starten und über Tunnel testen:
```bash
docker compose up -d traefik dashboard frontend
# Mac: ssh -N -L 8052:localhost:8050 -L 8056:localhost:8055 pit@179.237.106.106
#      → http://localhost:8052 (Dashboard), http://localhost:8056/c, /dist, /player-profile
```

Proxy-Test (ein Abruf über DataImpulse, kein Direktabruf):
```bash
docker compose run --rm --no-deps worker python -c "…"   # siehe Session-Notiz
```

Backup-Probelauf: `FIDE_DB_CONTAINER=fide-db bash /opt/fide-scraper/scripts/backup_fide_vps.sh`.

## Phase 4 — Umschalten (≈ 1 h Scraping-Pause)

1. **Mac Mini:** `launchctl bootout gui/$(id -u)/net.chesspit.fide-monthly-update` und `…/net.chesspit.fide-backup-pull`.
2. **Hostinger:** Threads im Dashboard stoppen, dann `cd /opt/fide-scraper/orchestrator && docker compose stop worker`.
3. **Hostinger:** frischer Dump + Laufzeitdaten:
   ```bash
   docker exec fide-tunnelbliq-shared-db pg_dump -U fide -d fidedb -Fc > /home/pit/backups/final.dump
   docker run --rm -v orchestrator_orchestrator_data:/d -v /home/pit/backups:/b alpine tar czf /b/orch_data.tgz -C /d .
   ```
   Übertragen (über den Mac oder direkt) nach `pit@179.237.106.106:/home/pit/backups/`.
4. **Infomaniak:** `docker compose stop dashboard frontend`, Probe-DB ersetzen:
   ```bash
   docker exec fide-db psql -U postgres -c "DROP DATABASE fidedb;" -c "CREATE DATABASE fidedb OWNER fide;"
   docker exec fide-db psql -U postgres -d fidedb -c "CREATE EXTENSION timescaledb;"
   ```
   dann Restore wie Phase 3 mit `final.dump`; Zahlen gegen Hostinger-Live-Werte.
   Laufzeitdaten: `docker run --rm -v infomaniak_orchestrator_data:/d -v /home/pit/backups:/b alpine tar xzf /b/orch_data.tgz -C /d`
   (Volume-Name mit `docker volume ls` prüfen).
5. **DNS bei manitu:** `scelo` A → `179.237.106.106`; neuer A-Eintrag für den Frontend-Host (`FRONTEND_HOST` in `.env`).
   Vorerst kein AAAA.
6. **Infomaniak:** `docker compose up -d` (jetzt mit Worker). Prüfen: `curl -sI https://scelo.chesspit.net` → 401,
   Zertifikat gültig, Dashboard mit Login, erste `scrape_runs` = success.
7. **Mac Mini:** `scripts/vps.env` auf `pit@179.237.106.106` + `/opt/fide-scraper/deploy/infomaniak`; in `.env` und
   `.env.notebook` neues `DB_PASSWORD`/`DATABASE_URL` (Wert aus der Server-`.env`, Port bleibt 5434 über den Tunnel);
   Tunnel neu starten; launchd-Jobs wieder laden (`launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/<plist>`);
   `bash scripts/pull_backup_macmini.sh` und `bash scripts/monthly_update.sh` einmal von Hand. Commit + Push.
8. **Infomaniak, Crontab `pit`:**
   ```
   45 3 * * * FIDE_DB_CONTAINER=fide-db /opt/fide-scraper/scripts/backup_fide_vps.sh
   */5 * * * * /opt/fide-scraper/deploy/infomaniak/memory_watch.sh
   ```
9. **MacBook Pro:** `git pull`, `.env`/`.env.notebook` mit neuem Passwort.

**Rückfallweg** (bis Schritt 6): DNS zurück auf `187.124.181.116`, auf Hostinger `docker compose start worker`.
Die Hostinger-DB bleibt unverändert, solange dort kein Worker läuft.

## Phase 5 — Nachbereitung

- 24–48 h beobachten: 0 failed, `~/logs/memory_watch.log`, Backup am Folgetag auf dem Mac.
- IPv6 von außen prüfen, dann optional AAAA.
- Passwort der alten Hostinger-Rolle `fide` ändern.
- `CLAUDE.md` (Verbindung, Container `fide-db`), `docs/project_status.md` aktualisieren.
- Nach ~1 Woche Hostinger aufräumen (ELO-Container, shared-db, Volumes, ~25 GB). **coolify-proxy und n8n bleiben.**
