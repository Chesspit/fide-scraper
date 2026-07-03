#!/usr/bin/env bash
# VPS-Wrapper für run_update_job.sh — leitet Requests über den Webshare-Proxy-Pool.
#
# Baut HTTP_PROXY aus den PROXY_* Env-Variablen + einer zufällig gewählten
# IP:PORT-Zeile aus orchestrator/webshare_proxies.txt (dieselben, die der
# Orchestrator-Worker nutzt — siehe orchestrator/proxy_manager.py). Ein fester
# Einzel-Host ergibt bei einer 100er-IP-Liste keinen Sinn mehr, daher die
# kleine Python-Auswahl statt reinem Bash-String-Build. requests.get() in
# fetcher.py respektiert HTTP_PROXY automatisch.
#
# Ausführen im Worker-Container auf dem VPS:
#   docker compose run -T worker bash scripts/run_update_job_vps.sh UP-GER
#
# Alle 3 Jobs sequenziell (UP-FEMALE entfällt, siehe P1/P2/P3-Umbau):
#   for JOB in UP-ELO2300 UP-GER UP-DACH; do
#     docker compose run -T worker bash scripts/run_update_job_vps.sh $JOB
#   done

set -uo pipefail

JOB=${1:?Job-Name angeben (z.B. UP-GER)}
SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
POOL_FILE="${PROXY_POOL_FILE:-$SCRIPT_DIR/orchestrator/webshare_proxies.txt}"

# Proxy aus Env-Vars + zufälliger Pool-Zeile aufbauen
if [ -n "${PROXY_USERNAME:-}" ] && [ -n "${PROXY_PASSWORD:-}" ] && [ -f "$POOL_FILE" ]; then
    PROXY_ENTRY=$(python3 -c "
import random
with open('$POOL_FILE') as f:
    entries = [l.strip() for l in f if l.strip() and not l.startswith('#')]
print(random.choice(entries))
")
    export FIDE_PROXY="http://${PROXY_USERNAME}:${PROXY_PASSWORD}@${PROXY_ENTRY}"
    echo "$(date): Proxy aktiv: ${PROXY_ENTRY} (aus Webshare-Pool, $(wc -l < "$POOL_FILE") IPs verfügbar)"
else
    echo "$(date): WARNUNG: Keine Proxy-Credentials (PROXY_USERNAME/PROXY_PASSWORD) oder Pool-Datei ($POOL_FILE) fehlt — direkte Verbindung"
fi

exec bash "$(dirname "$0")/run_update_job.sh" "$@"
