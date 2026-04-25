#!/usr/bin/env bash
# Forward local port 5434 → VPS TimescaleDB (5432).
# Leave this running in a separate terminal while working in notebooks.
# Port 5434 avoids clash with the local docker-compose dev DB on 5433.
#
# Auto-reconnects if the tunnel drops (wichtig für lange Backfill-Läufe).
set -uo pipefail

SSH_OPTS=(
    -N
    -L 5434:localhost:5432
    -o ServerAliveInterval=30    # keep-alive every 30s
    -o ServerAliveCountMax=6     # reconnect after 3 min of no response
    -o ExitOnForwardFailure=yes
    -o TCPKeepAlive=yes
)

echo "Tunnel starting (auto-reconnect enabled)..."
while true; do
    ssh "${SSH_OPTS[@]}" pit@187.124.181.116
    EXIT=$?
    echo "$(date): Tunnel exited (code $EXIT), reconnecting in 5s..."
    sleep 5
done
