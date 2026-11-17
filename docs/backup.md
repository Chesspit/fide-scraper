# Backup fidedb (VPS ELO)

Stand 17.11.2026. Vorlage für die Cloud-Stufe: tunnelbliq `ops/backup_db_vps.sh`.

## Überblick

| Stufe | Wo | Was | Aufbewahrung |
|---|---|---|---|
| 1 lokal | ELO `~/backups/fide-scraper/` | `pg_dump -Fc` von `fidedb` (~1,7 GB) + `config/` (`.env`, `runtime_settings.json`, Proxy-Marker; Rechte 600) | die 3 neuesten Dumps |
| 2 Mac | `~/backups/fide-scraper/vps/` | `pull_backup_macmini.sh` (launchd, Boot + 09:30): Dumps, `config/`, Parquet-Caches | 5 Tage, mind. 2 |
| 3 Cloud | Infomaniak Object Storage, Bucket `elo-backup` | `daily/` jeder Dump · `monthly/` ein Dump je Monat · `config-crypt/` verschlüsselt | `daily/` 14 Tage, `monthly/` **nie gelöscht** |

Cron auf ELO (unverändert): `45 3 * * * FIDE_DB_CONTAINER=fide-db /opt/fide-scraper/scripts/backup_fide_vps.sh`.
Das Skript läuft direkt aus dem Checkout – Änderungen kommen per `git pull`. Log: `~/backups/fide-scraper/backup.log`.

## Cloud (seit 17.11.2026)

| | |
|---|---|
| Infomaniak | Public Cloud «via» (23482) → Projekt `elo-backup` = `PCP-J2TYRZR`, Region `dc4-a`. **Getrennt von `via-backup` (tunnelbliq) – nie anfassen** |
| OpenStack-Benutzer | `PCU-J2TYRZR`, Passwort nur in LastPass («ELO Cloud-Backup OpenStack») |
| S3-Endpoint | `https://s3.pub2.infomaniak.cloud` (Keystone `https://api.pub1.infomaniak.cloud/identity/v3`, Domain-**Name** `Default`) |
| rclone | 1.60.1 (apt), `~/.config/rclone/rclone.conf` auf ELO, Rechte 600: `elo-s3` (S3, `acl = private`), `elo-crypt` (crypt über `elo-s3:elo-backup/config-crypt`) |
| Passphrase `elo-crypt` | nur in LastPass («ELO Cloud-Backup Passphrase»). **Ohne sie ist die `.env`-Kopie in der Cloud wertlos** |

Aufbau im Bucket:
- `daily/fidedb_<UTC>.dump` – jede Nacht, `rclone delete --min-age 14d`.
- `monthly/fidedb_<UTC>.dump` – erster erfolgreicher Lauf je Monat; fehlt er, versucht es jeder weitere Lauf.
- `config-crypt/` – Namen und Inhalt verschlüsselt. Über `elo-crypt:` sichtbar als `latest/` (jede Nacht per `sync`) und `monthly/YYYY-MM/`.

Kosten (~0,0095 €/GB/Monat): `daily/` ≈ 24 GB ≈ 0,23 €/Monat, je Monats-Dump ≈ 0,02 €/Monat dazu.

Schlüssel neu erzeugen: `ssh -t pit@179.237.106.106 'bash /opt/fide-scraper/scripts/setup_cloud_backup_keys.sh'`
(fragt Benutzer, Projekt, Passwort und Passphrase verdeckt ab; schreibt nur `rclone.conf`).
Bekannte Schwäche wie bei tunnelbliq: die Schlüssel auf ELO dürfen auch löschen. Ein kompromittierter
Server könnte die Cloud-Kopien löschen, nicht aber die von tunnelbliq. Abhilfe bei Bedarf: Versionierung/Object Lock.

## Alerting (Healthchecks.io)

Check «ELO Backup», Ping-URL auf ELO in `~/.config/fide-scraper/backup_healthcheck_url` (600, nicht im Repo).
`/start` zu Beginn, Erfolg mit Dateiname und Grösse, `/fail` bei jedem Fehler: DB-Container fehlt, `pg_dump`,
Upload, Cloud-Rotation, unvollständige Konfig-Sicherung, unerwarteter Abbruch (ERR-Trap). Läuft das Skript
gar nicht, alarmiert Healthchecks nach Period + Grace. Ohne Ping-URL läuft das Backup weiter, nur ohne Alarm.
`CLOUD_BACKUP=0` überspringt die Cloud-Stufe (z. B. für einen reinen Probe-Dump).

## Status prüfen

```bash
ssh pit@179.237.106.106 'tail -8 ~/backups/fide-scraper/backup.log; rclone lsl elo-s3:elo-backup/daily | tail -3; rclone lsl elo-s3:elo-backup/monthly'
```

## Restore

Aus der Cloud holen (auf ELO oder auf jedem Rechner mit derselben `rclone.conf`):
```bash
rclone copy elo-s3:elo-backup/monthly/<datei>.dump .        # oder daily/
rclone copy elo-crypt:latest/ ./config                        # entschlüsselt env, runtime_settings.json …
```
Einspielen in einen **Wegwerf-Container**, nie in die laufende DB (TimescaleDB-Reihenfolge):
```bash
docker run -d --name fide-restore-probe -e POSTGRES_PASSWORD=probe -p 127.0.0.1:55433:5432 \
    -v fide-restore-probe-data:/var/lib/postgresql/data timescale/timescaledb:2.26.2-pg16
docker exec fide-restore-probe psql -U postgres -c "CREATE ROLE fide LOGIN" -c "CREATE DATABASE fidedb OWNER fide" \
    -c "CREATE ROLE fide_reader" -c "CREATE ROLE fide_qc" -c "CREATE ROLE fide_ingest"   # sonst ~100 Fehler bei den GRANTs (Migration 022)
docker exec fide-restore-probe psql -U postgres -d fidedb -c "CREATE EXTENSION IF NOT EXISTS timescaledb" -c "SELECT timescaledb_pre_restore()"
docker cp <datei>.dump fide-restore-probe:/tmp/r.dump      # -j (parallel) liest nicht von stdin
docker exec fide-restore-probe pg_restore -U postgres -d fidedb --no-owner --role=fide -j 4 /tmp/r.dump
docker exec fide-restore-probe psql -U postgres -d fidedb -c "SELECT timescaledb_post_restore()"
```
`pg_restore` meldet dabei rund 26 harmlose Fehler (TimescaleDB-Katalog, `COMMENT ON EXTENSION`), siehe
`docs/umzug_infomaniak.md`. Fehlen die drei Rollen aus Migration 022, kommen gut 100 «role … does not exist»
dazu (nur die GRANTs; Tabellen und Daten sind trotzdem vollständig). Für einen echten Ersatz-Server danach
Migration 022 und die Passwörter laut `docs/db_rollen.md` setzen. Danach Zeilenzahlen gegen Produktion vergleichen; Container und Volume wieder löschen.
Auf ELO reicht der Platz für eine zweite 18-GB-Kopie nicht – Restore-Tests auf dem Mac.

Letzte Restore-Tests:
- 16.11.2026: lokaler Dump, Objekt-Inventar gegen Produktion identisch (`docs/review.md`, 5a).
- **17.11.2026: Dump aus der Cloud** (`monthly/fidedb_2026-11-17T110240Z.dump`, 1,79 GB). Über ELO auf den Mac
  gestreamt (1:24 min), MD5 identisch mit lokal/`daily/`/`monthly/`. Restore in 6 min (`-j 4`), 13 GB.
  Exakt gleich wie Produktion: `players` 1'838'941, `groups` 254, `rating_corrections` 379'703, Queue bis Id 26897
  26'862 Gruppen, `qc_audit_runs` 16, 35 Indizes, 7 `fn_`-Funktionen, 5 Views. Grosse Tabellen (Kopie exakt,
  Produktion nur Schätzwert): `game_results` 21'417'860, `scrape_periods` 25'239'732, `rating_history` 61'177'453.
  `pg_restore`: 26 TimescaleDB-Katalog-Fehler + GRANTs an die im Wegwerf-Container fehlenden Rollen (s. oben).
  Die aus `elo-crypt:latest/env` entschlüsselte `.env` war byteweise gleich mit der auf dem Server.
