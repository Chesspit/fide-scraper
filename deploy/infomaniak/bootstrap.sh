#!/usr/bin/env bash
# Grundeinrichtung des Infomaniak-VPS „ELO" (ov-31afa4, Ubuntu 24.04) für fide-scraper.
#
# Idempotent — darf beliebig oft laufen. Einmalig als `ubuntu` (hat sudo):
#
#   scp deploy/infomaniak/bootstrap.sh extra_keys.pub elo-infomaniak:/tmp/
#   ssh elo-infomaniak 'sudo bash /tmp/bootstrap.sh /tmp/extra_keys.pub'
#
# Das optionale Argument ist eine Datei mit zusätzlichen Public Keys für `pit`
# (Mac Mini, MacBook Pro …). Die Keys von `ubuntu` werden immer übernommen.
#
# Bewusst NICHT hier: .env, DB-Restore, Compose-Start — siehe docs/umzug_infomaniak.md.
#
# Zeitzone bleibt UTC wie auf Hostinger: Cron-Zeiten (Backup 03:45) und die
# Dump-Dateinamen (…T034501Z) bleiben damit unverändert.
set -euo pipefail

EXTRA_KEYS="${1:-}"
APP_USER=pit
APP_DIR=/opt/fide-scraper
REPO_URL=https://github.com/Chesspit/fide-scraper.git
SWAP_SIZE=4G

log() { echo "== $(date -u +%H:%M:%S) $*"; }

[ "$(id -u)" -eq 0 ] || { echo "Bitte mit sudo ausführen." >&2; exit 1; }
export DEBIAN_FRONTEND=noninteractive

log "Pakete aktualisieren"
apt-get update -q
apt-get -yq -o Dpkg::Options::=--force-confold full-upgrade
apt-get install -yq ca-certificates curl gnupg git rsync ufw fail2ban unattended-upgrades

log "Automatische Sicherheitsupdates"
dpkg-reconfigure -f noninteractive unattended-upgrades

log "Swap ${SWAP_SIZE}"
if ! swapon --show | grep -q /swapfile; then
    fallocate -l "$SWAP_SIZE" /swapfile
    chmod 600 /swapfile
    mkswap /swapfile
    swapon /swapfile
fi
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
echo 'vm.swappiness=10' > /etc/sysctl.d/90-swappiness.conf
sysctl -q --system

log "Docker Engine + Compose-Plugin"
if ! command -v docker >/dev/null; then
    install -m 0755 -d /etc/apt/keyrings
    curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
    chmod a+r /etc/apt/keyrings/docker.asc
    . /etc/os-release
    echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu ${VERSION_CODENAME} stable" \
        > /etc/apt/sources.list.d/docker.list
    apt-get update -q
    apt-get install -yq docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
systemctl enable --now docker

log "Benutzer ${APP_USER}"
id "$APP_USER" >/dev/null 2>&1 || adduser --disabled-password --gecos "" "$APP_USER"
usermod -aG sudo,docker "$APP_USER"
# Kein Passwort gesetzt (Login nur per Key) → sudo ohne Passwort, wie `ubuntu` im Cloud-Image.
echo "${APP_USER} ALL=(ALL) NOPASSWD:ALL" > "/etc/sudoers.d/90-${APP_USER}"
chmod 440 "/etc/sudoers.d/90-${APP_USER}"

install -d -m 700 -o "$APP_USER" -g "$APP_USER" "/home/${APP_USER}/.ssh"
AUTH="/home/${APP_USER}/.ssh/authorized_keys"
touch "$AUTH"
for src in /home/ubuntu/.ssh/authorized_keys ${EXTRA_KEYS:+"$EXTRA_KEYS"}; do
    [ -f "$src" ] || continue
    while IFS= read -r key; do
        [ -n "$key" ] || continue
        grep -qxF "$key" "$AUTH" || echo "$key" >> "$AUTH"
    done < "$src"
done
chown "$APP_USER:$APP_USER" "$AUTH"
chmod 600 "$AUTH"

log "SSH härten"
cat > /etc/ssh/sshd_config.d/90-fide.conf <<'EOF'
PermitRootLogin no
PasswordAuthentication no
KbdInteractiveAuthentication no
EOF
sshd -t
# Ubuntu 24.04 startet sshd per Socket-Aktivierung: ssh.service ist oft inaktiv,
# ein reload schlägt dann fehl. Neue Verbindungen lesen die Config ohnehin neu.
systemctl try-reload-or-restart ssh.service || true

log "ufw (Zweitschutz zur Infomaniak-Firewall; Docker-Ports zusätzlich nur an 127.0.0.1)"
ufw default deny incoming
ufw default allow outgoing
ufw allow 22/tcp
ufw allow 80/tcp
ufw allow 443/tcp
ufw allow 443/udp
ufw --force enable

log "fail2ban"
systemctl enable --now fail2ban

log "Verzeichnisse + Repo"
install -d -o "$APP_USER" -g "$APP_USER" "$APP_DIR" \
    "/home/${APP_USER}/backups/fide-scraper" "/home/${APP_USER}/logs" "/home/${APP_USER}/scripts"
if [ ! -d "${APP_DIR}/.git" ]; then
    sudo -u "$APP_USER" git clone "$REPO_URL" "$APP_DIR"
else
    sudo -u "$APP_USER" git -C "$APP_DIR" pull --ff-only
fi

log "Fertig. Prüfen: ssh ${APP_USER}@$(hostname -I | awk '{print $1}') 'docker ps'"
