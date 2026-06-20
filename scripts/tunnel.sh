#!/usr/bin/env bash
# SSH-Tunnel zum VPS:
#   Port 5434 → VPS TimescaleDB (5432)
#   Port 8051 → VPS Orchestrator-Dashboard (127.0.0.1:8050)
#
# Dashboard im Browser: http://localhost:8051
# Auto-reconnects if the tunnel drops (wichtig für lange Backfill-Läufe).
set -uo pipefail

# ── Idempotenz-Lock ──────────────────────────────────────────────────────────
# Verhindert den "Tunnel-Storm": run_female_chain.sh startet bei jedem Backfill-
# Neustart ein `bash tunnel.sh &`, wenn Port 5434 kurz kein LISTEN zeigt (z.B.
# während eines Reconnects oder nach MacBook-Sleep). Da tunnel.sh eine Endlos-
# Reconnect-Schleife ist, häuften sich so Dutzende Instanzen an, die um Port 5434
# kämpften → Dauerflattern. Ein atomarer mkdir-Lock macht jeden Zweitstart zum
# No-Op, solange bereits ein Tunnel läuft.
LOCK_DIR="/tmp/fide_tunnel.lock"
if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    OLD_PID=$(cat "$LOCK_DIR/pid" 2>/dev/null || true)
    if [ -n "$OLD_PID" ] && kill -0 "$OLD_PID" 2>/dev/null; then
        echo "Tunnel läuft bereits (PID $OLD_PID) — dieser Start ist ein No-Op."
        exit 0
    fi
    # Verwaister Lock (Halter tot) → übernehmen
    rm -rf "$LOCK_DIR"
    mkdir "$LOCK_DIR"
fi
echo "$$" > "$LOCK_DIR/pid"
trap 'rm -rf "$LOCK_DIR"' EXIT

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
