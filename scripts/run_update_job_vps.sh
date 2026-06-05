#!/usr/bin/env bash
# VPS-Wrapper für run_update_job.sh — leitet Requests über ProxyJet Residential Proxy.
#
# Baut HTTP_PROXY aus den bestehenden PROXYJET_* Env-Variablen auf (dieselben
# die der Orchestrator-Worker nutzt). requests.get() in fetcher.py respektiert
# HTTP_PROXY automatisch.
#
# Ausführen im Worker-Container auf dem VPS:
#   docker compose run -T worker bash scripts/run_update_job_vps.sh UP-GER
#   docker compose run -T worker bash scripts/run_update_job_vps.sh UP-FEMALE 2026-01-01 2026-06-01
#
# Alle 4 Jobs sequenziell:
#   for JOB in UP-ELO2300 UP-FEMALE UP-GER UP-DACH; do
#     docker compose run -T worker bash scripts/run_update_job_vps.sh $JOB
#   done

set -uo pipefail

JOB=${1:?Job-Name angeben (z.B. UP-GER)}

# Proxy aus Env-Vars aufbauen
if [ -n "${PROXYJET_USERNAME:-}" ] && [ -n "${PROXYJET_PASSWORD:-}" ]; then
    HOST="${PROXYJET_HOST:-proxy-jet.io}"
    PORT="${PROXYJET_PORT:-1010}"
    export HTTP_PROXY="http://${PROXYJET_USERNAME}:${PROXYJET_PASSWORD}@${HOST}:${PORT}"
    export HTTPS_PROXY="$HTTP_PROXY"
    echo "$(date): Proxy aktiv: ${HOST}:${PORT} (Residential)"
else
    echo "$(date): WARNUNG: Keine ProxyJet-Credentials (PROXYJET_USERNAME/PROXYJET_PASSWORD) — direkte Verbindung"
fi

exec bash "$(dirname "$0")/run_update_job.sh" "$@"
