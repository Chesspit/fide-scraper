#!/usr/bin/env bash
# Einmaliges Setup-Script für den Raspberry Pi 500 (Pi 5, ARM64).
# Auf dem Pi ausführen nach: git clone https://github.com/Chesspit/fide-scraper.git
#
# Verwendung:
#   cd ~/fide-scraper
#   bash scripts/setup_raspi.sh

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

ok()   { echo -e "${GREEN}✓${NC} $*"; }
warn() { echo -e "${YELLOW}!${NC} $*"; }
err()  { echo -e "${RED}✗${NC} $*"; exit 1; }
step() { echo -e "\n${YELLOW}==>${NC} $*"; }

echo "FIDE Scraper — Raspberry Pi Setup"
echo "==================================="

# --- Schritt 1: Python-Version prüfen ---
step "Python-Version prüfen"
PY=$(python3 --version 2>&1 | grep -oE '[0-9]+\.[0-9]+')
MAJOR=$(echo "$PY" | cut -d. -f1)
MINOR=$(echo "$PY" | cut -d. -f2)
if [ "$MAJOR" -lt 3 ] || { [ "$MAJOR" -eq 3 ] && [ "$MINOR" -lt 9 ]; }; then
    err "Python $PY gefunden — mindestens 3.9 erforderlich"
fi
ok "Python $PY"

# --- Schritt 2: System-Pakete ---
step "System-Pakete installieren (sudo erforderlich)"
sudo apt update -qq
sudo apt install -y git python3-pip python3-venv libpq-dev lsof 2>/dev/null
ok "System-Pakete installiert"

# --- Schritt 3: Python venv + Packages ---
step "Python Virtual Environment einrichten"
cd "$SCRIPT_DIR"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
    ok "venv erstellt unter $SCRIPT_DIR/.venv"
else
    ok "venv bereits vorhanden"
fi

source .venv/bin/activate
pip install --quiet --upgrade pip
pip install --quiet -r scraper/requirements.txt
ok "Python-Packages installiert (scraper/requirements.txt)"

# --- Schritt 4: SSH-Key generieren ---
step "SSH-Key für VPS-Tunnel"
KEY="$HOME/.ssh/id_ed25519"
if [ ! -f "$KEY" ]; then
    mkdir -p "$HOME/.ssh"
    chmod 700 "$HOME/.ssh"
    ssh-keygen -t ed25519 -C "raspi-fide-scraper" -f "$KEY" -N ""
    ok "SSH-Key generiert: $KEY"
else
    ok "SSH-Key bereits vorhanden: $KEY"
fi

echo ""
echo "========================================================"
echo "  WICHTIG: Diesen Public Key auf dem VPS eintragen!"
echo "========================================================"
cat "${KEY}.pub"
echo "========================================================"
echo "  Befehl auf dem Mac Mini:"
echo "  ssh pit@187.124.181.116 \"echo '$(cat ${KEY}.pub)' >> ~/.ssh/authorized_keys\""
echo "========================================================"
echo ""
read -rp "Wurde der Key auf dem VPS eingetragen? (j/n): " CONFIRM
if [[ "$CONFIRM" != "j" && "$CONFIRM" != "J" ]]; then
    warn "Key noch nicht eingetragen — Tunnel-Test wird übersprungen."
    SKIP_TUNNEL=true
else
    SKIP_TUNNEL=false
fi

# --- Schritt 5: .env erstellen ---
step ".env Konfiguration"
if [ ! -f "$SCRIPT_DIR/.env" ]; then
    cp "$SCRIPT_DIR/.env.example" "$SCRIPT_DIR/.env"
    # WORKER_DEVICE setzen
    sed -i 's/^WORKER_DEVICE=.*/WORKER_DEVICE=raspi/' "$SCRIPT_DIR/.env"
    ok ".env erstellt aus .env.example (WORKER_DEVICE=raspi)"
else
    ok ".env bereits vorhanden"
    if ! grep -q "WORKER_DEVICE=raspi" "$SCRIPT_DIR/.env"; then
        sed -i 's/^WORKER_DEVICE=.*/WORKER_DEVICE=raspi/' "$SCRIPT_DIR/.env"
        warn "WORKER_DEVICE auf raspi gesetzt"
    fi
fi

# --- Schritt 6: Tunnel testen ---
if [ "$SKIP_TUNNEL" = false ]; then
    step "SSH-Tunnel zur VPS-Datenbank testen"
    bash "$SCRIPT_DIR/scripts/tunnel.sh" &
    TUNNEL_PID=$!
    sleep 6

    if lsof -i :5434 | grep -q LISTEN 2>/dev/null; then
        ok "Tunnel aktiv (Port 5434)"
        if source "$SCRIPT_DIR/.venv/bin/activate" && python3 -c "
import psycopg2
conn = psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb')
conn.close()
print('DB-Verbindung OK')
" 2>/dev/null; then
            ok "Datenbankverbindung erfolgreich"
        else
            warn "Tunnel läuft, aber DB-Verbindung fehlgeschlagen — Credentials prüfen"
        fi
        kill $TUNNEL_PID 2>/dev/null || true
    else
        warn "Tunnel konnte nicht gestartet werden — SSH-Key auf VPS prüfen"
        kill $TUNNEL_PID 2>/dev/null || true
    fi
fi

# --- Abschluss ---
echo ""
echo "==================================="
echo "  Setup abgeschlossen!"
echo "==================================="
echo ""
echo "Nächste Schritte:"
echo "  1. Tailscale installieren (Remote-Zugang):"
echo "     curl -fsSL https://tailscale.com/install.sh | sh"
echo "     sudo tailscale up"
echo ""
echo "  2. Scraping starten:"
echo "     source .venv/bin/activate"
echo "     bash scripts/tunnel.sh &"
echo "     bash scripts/run_local_backfill.sh female_1800_01"
echo ""
echo "  Anleitung: docs/setup_raspi.md"
