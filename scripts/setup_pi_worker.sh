#!/usr/bin/env bash
# Setup-Skript für den Raspberry Pi als Scraping-Worker.
#
# Voraussetzungen (einmalig manuell):
#   - Tailscale installiert und eingeloggt
#   - SSH-Key für pit@187.124.181.116 hinterlegt
#   - Python 3.9+ und git vorhanden
#
# Ausführen (vom MacBook oder direkt auf dem Pi):
#   bash scripts/setup_pi_worker.sh [PI_TAILSCALE_IP]

set -euo pipefail

VPS="pit@187.124.181.116"
REPO_DIR="$HOME/fide-scraper"
DATA_DIR="$REPO_DIR/orchestrator/pi_data"
PI_IP="${1:-}"

# ── 1. Repo klonen / aktualisieren ──────────────────────────────────────────
if [ -d "$REPO_DIR/.git" ]; then
    echo "Repo vorhanden — aktualisiere..."
    git -C "$REPO_DIR" pull --ff-only
else
    echo "Klone Repo..."
    git clone git@github.com:$(git -C "$(dirname "$0")/.." remote get-url origin | sed 's/.*github.com[:/]\(.*\)\.git/\1/') "$REPO_DIR"
fi

# ── 2. Python-Venv einrichten ────────────────────────────────────────────────
cd "$REPO_DIR"
if [ ! -d .venv ]; then
    python3 -m venv .venv
fi
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r scraper/requirements.txt

# ── 3. Daten-Verzeichnis anlegen ─────────────────────────────────────────────
mkdir -p "$DATA_DIR"

# ── 4. Pi-Queue vom VPS laden ────────────────────────────────────────────────
echo "Lade scraper_pi.db vom VPS..."
# Zuerst auf VPS generieren (falls noch nicht vorhanden)
ssh "$VPS" "docker exec orchestrator-worker-1 python3 /app/orchestrator/export_pi_groups.py \
    --src /data/scraper.db --out /tmp/scraper_pi.db 2>&1"
scp "$VPS:/tmp/scraper_pi.db" "$DATA_DIR/scraper.db"
echo "Queue geladen: $DATA_DIR/scraper.db"

# ── 5. .env anlegen (falls nicht vorhanden) ──────────────────────────────────
ENV_FILE="$REPO_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    cat > "$ENV_FILE" << 'ENVEOF'
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb
ORCHESTRATOR_DATA_DIR=/HOME_PLACEHOLDER/fide-scraper/orchestrator/pi_data
ORCHESTRATOR_PROFILES=/HOME_PLACEHOLDER/fide-scraper/orchestrator/profiles_pi.yaml
WORKER_DEVICE=raspi
ENVEOF
    sed -i "s|/HOME_PLACEHOLDER|$HOME|g" "$ENV_FILE"
    echo ".env angelegt: $ENV_FILE"
else
    echo ".env bereits vorhanden — prüfe fehlende Einträge."
    if ! grep -q "ORCHESTRATOR_DATA_DIR" "$ENV_FILE"; then
        echo "ORCHESTRATOR_DATA_DIR=$HOME/fide-scraper/orchestrator/pi_data" >> "$ENV_FILE"
        echo "ORCHESTRATOR_PROFILES=$HOME/fide-scraper/orchestrator/profiles_pi.yaml" >> "$ENV_FILE"
        warn "ORCHESTRATOR_DATA_DIR + ORCHESTRATOR_PROFILES ergänzt"
    fi
    if ! grep -q "WORKER_DEVICE=raspi" "$ENV_FILE"; then
        sed -i 's/^WORKER_DEVICE=.*/WORKER_DEVICE=raspi/' "$ENV_FILE" 2>/dev/null || \
            echo "WORKER_DEVICE=raspi" >> "$ENV_FILE"
        warn "WORKER_DEVICE=raspi gesetzt"
    fi
fi

# ── 6. Tunnel starten (im Hintergrund) ──────────────────────────────────────
echo "Starte SSH-Tunnel..."
bash "$REPO_DIR/scripts/tunnel.sh" &
sleep 3
if lsof -i :5434 > /dev/null 2>&1; then
    echo "Tunnel aktiv auf Port 5434."
else
    echo "WARNUNG: Tunnel nicht aktiv — bitte manuell prüfen."
fi

echo ""
echo "══════════════════════════════════════════════════"
echo "Setup abgeschlossen. Worker starten mit:"
echo "  cd $REPO_DIR"
echo "  source .venv/bin/activate"
echo "  python3 orchestrator/worker.py"
echo "══════════════════════════════════════════════════"
