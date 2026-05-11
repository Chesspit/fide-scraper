#!/usr/bin/env bash
# SSH-Tunnel zum VPS:
#   Port 5434 → VPS TimescaleDB (5432)
#   Port 8051 → VPS Orchestrator-Dashboard (127.0.0.1:8050)
#
# Dashboard im Browser: http://localhost:8051
# Auto-reconnects if the tunnel drops (wichtig für lange Backfill-Läufe).
set -uo pipefail

SSH_OPTS=(
    -N
    -L 5434:localhost:5432
    -L 8051:localhost:8050
    -o ServerAliveInterval=30    # keep-alive every 30s
    -o ServerAliveCountMax=6     # reconnect after 3 min of no response
    -o ExitOnForwardFailure=yes
    -o TCPKeepAlive=yes
)

echo "Tunnel starting (auto-reconnect enabled)..."
echo "  DB:        localhost:5434"
echo "  Dashboard: http://localhost:8051"
while true; do
    ssh "${SSH_OPTS[@]}" pit@187.124.181.116
    EXIT=$?
    echo "$(date): Tunnel exited (code $EXIT), reconnecting in 5s..."
    sleep 5
done
