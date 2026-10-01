#!/usr/bin/env bash
# Läuft alle 5 Minuten per Cron. Loggt Host-Speicher + Top-Container-Verbraucher,
# damit ein RAM-Vorfall (siehe Session 2026-09-16, OOM-Kill auf fide-tunnelbliq-shared-db
# am 14.09.) sich künftig nachvollziehen lässt, statt in der Docker-Log-Rotation zu verschwinden.
set -euo pipefail

LOG=/home/pit/logs/memory_watch.log
TS=$(date -u +"%Y-%m-%dT%H:%M:%SZ")

{
  read -r _ total used free _ buff avail < <(free -m | awk '/^Mem:/')
  echo "${TS} host_mem_mb total=${total} used=${used} free=${free} buffcache=${buff} available=${avail}"
  docker stats --no-stream --format '{{.Name}} {{.MemUsage}}' 2>/dev/null \
    | awk -v ts="$TS" '{print ts, "container", $0}'
} >> "$LOG"

# Bounded growth: alle ~1000 Läufe (~3.5 Tage bei 5-Min-Intervall) auf die letzten
# 100000 Zeilen kappen (~ mehrere Monate Historie), statt unbegrenzt zu wachsen.
if [ "$(( RANDOM % 1000 ))" -eq 0 ] && [ -f "$LOG" ]; then
  tail -n 100000 "$LOG" > "${LOG}.tmp" && mv "${LOG}.tmp" "$LOG"
fi
