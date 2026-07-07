# Raspberry Pi auf die PG-Queue umstellen (Phase-B-Abschluss)

**Auszuführen vom MacBook Pro** (einziges Gerät mit funktionierendem Tailscale-Zugang zum Pi).
Stand: 2026-07-07. Kontext: `docs/orchestrator_redesign_2026-07.md` §2.3 (Phase B).

Seit dem Review-#5-Deploy (04.07., Queue SQLite→PG) ist der Pi-Status-Sync tot:
der Pi scrapt weiter und schreibt Partien direkt nach PG, aber seine done-Markierungen,
Runs und MB landen nur noch in seiner lokalen SQLite (`orchestrator/pi_data/scraper.db`).
Mit dem `claimed_by`-Patch (Commit `4a71c61` + Folge-Commit, Migration 014) kann der Pi
jetzt direkt gegen die geteilte PG-Queue claimen — dieser Runbook stellt ihn um.

## Voraussetzung

VPS-Deploy des claimed_by-Codes ist erfolgt. Prüfen (Tunnel: `bash scripts/tunnel.sh`):

```sql
SELECT claimed_by, count(*) FROM orchestrator.scrape_groups
WHERE status='running' GROUP BY claimed_by;
-- Erwartung: claimed_by='vps' auf den laufenden Gruppen (nach erstem Neustart)
```

## Schritt 0 — Pi erreichbar?

```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale ping 100.125.193.29
```

## Schritt 1 — Pi-Worker stoppen

```bash
ssh pit1@100.125.193.29 "kill \$(pgrep -f worker.py); sleep 2; pgrep -f worker.py || echo GESTOPPT"
```

## Schritt 2 — Lokalen Pi-Fortschritt nach PG übertragen (WICHTIG, vor allem anderen!)

Seit 04.07. ~07:50 UTC hat der Pi Gruppen nur noch **lokal** auf done gesetzt. Ohne diesen
Schritt stehen sie in PG weiter auf pending und würden doppelt gescrapt. Lokale done-Liste
auslesen (kein sqlite3-CLI auf dem Pi → Python):

```bash
ssh pit1@100.125.193.29 "python3 -c \"
import sqlite3
c = sqlite3.connect('/home/pit1/fide-scraper/orchestrator/pi_data/scraper.db')
for r in c.execute(\\\"SELECT federation, year, elo_min, records_found FROM scrape_groups WHERE status='done'\\\"):
    print('|'.join(str(x) for x in r))
\"" > /tmp/pi_done.txt
wc -l /tmp/pi_done.txt   # Erwartung: >452 (zentraler Stand vom 04.07. war 452)
```

Dann in PG nachziehen — Matching über den UNIQUE-Key `(federation, year, elo_min)`,
nur raspi-Gruppen anfassen, idempotent:

```bash
while IFS='|' read -r fed year elo rf; do
  psql "$DATABASE_URL" -c "UPDATE orchestrator.scrape_groups
    SET status='done', records_found=COALESCE(${rf:-NULL}, records_found), claimed_by='raspi'
    WHERE federation='$fed' AND year=$year AND elo_min=$elo
      AND device='raspi' AND status <> 'done';"
done < /tmp/pi_done.txt
```

(Oder als Einmal-Skript — Claude Code auf dem MacBook kann das ad hoc bauen. Kontrolle:
`SELECT status, count(*) FROM orchestrator.scrape_groups WHERE device='raspi' GROUP BY 1;`)

## Schritt 3 — Code auf dem Pi aktualisieren

```bash
ssh pit1@100.125.193.29 "cd ~/fide-scraper && git pull 2>&1 | tail -2 && git log --oneline -1"
```

## Schritt 4 — .env auf dem Pi prüfen/ergänzen

```bash
ssh pit1@100.125.193.29 "grep -E 'WORKER_DEVICE|DATABASE_URL' ~/fide-scraper/.env"
```

Soll-Zustand (fehlende Zeilen ergänzen):

```
WORKER_DEVICE=raspi        # PFLICHT — Claim-Filter: Pi nimmt NUR device='raspi'-Gruppen.
                           # Ohne diese Variable würde er die Residential-Queue des VPS claimen!
WORKER_DEVICE_ID=raspi     # claimed_by-Identität (neu, Phase B)
DATABASE_URL=…             # zeigt bereits auf die VPS-PG (Partien gingen immer direkt dorthin) — nicht ändern
```

## Schritt 5 — Worker starten

```bash
ssh pit1@100.125.193.29 "cd ~/fide-scraper && source .venv/bin/activate && nohup python3 orchestrator/worker.py > /tmp/worker_pi.log 2>&1 & sleep 5; tail -5 /tmp/worker_pi.log"
```

## Schritt 6 — Verifizieren

1. Log: `Queue-Identität: claimed_by='raspi'` muss erscheinen; kein Fehler beim Claim.
2. PG: `SELECT id, federation, year, claimed_by FROM orchestrator.scrape_groups WHERE status='running' AND claimed_by='raspi';` → genau 1 Zeile (Jahr 2020).
3. Nach Abschluss der ersten Gruppe: neuer Eintrag in `orchestrator.scrape_runs` mit `thread_slot` des Pi → Dashboard-Slot „Pi" zählt wieder MB.
4. Alte lokale Queue archivieren (erst nach erfolgreicher Verifikation):
   `ssh pit1@100.125.193.29 "mv ~/fide-scraper/orchestrator/pi_data/scraper.db ~/fide-scraper/orchestrator/pi_data/scraper.db.migrated-20260707"`

## Bei der Gelegenheit prüfen

Der Pi lief am 07.07. nur mit ~50 % seines üblichen Durchsatzes (3.900 → ~1.500–2.200
Perioden/h, unrund seit ~07:00 UTC). Ins `/tmp/worker_pi.log` schauen: Timeouts? Throttling?
CPU/Temperatur (`vcgencmd measure_temp`)?
