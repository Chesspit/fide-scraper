#!/usr/bin/env bash
# fide-scraper — einmalig: S3-Schlüssel für das Cloud-Backup erzeugen und rclone einrichten.
#
# Läuft INTERAKTIV auf dem VPS (von Pit gestartet, nicht per Cron):
#   ssh -t pit@<VPS> 'bash /opt/fide-scraper/scripts/setup_cloud_backup_keys.sh'
#
# Was es tut:
#   1. fragt OpenStack-Benutzer (PCU-…), Projekt (PCP-… = Keystone-Projektname) und
#      Passwort (verdeckt) ab,
#   2. meldet sich bei Keystone an (Domain-NAME «Default» – mit der ID «default» kommt 401),
#   3. erzeugt EC2-Credentials (S3-Schlüssel) für dieses Projekt,
#   4. schreibt zwei rclone-Remotes nach ~/.config/rclone/rclone.conf (Rechte 600):
#        elo-s3     S3-Zugang zum Object Storage
#        elo-crypt  verschlüsselte Ablage für die Server-Konfiguration (.env) unter
#                   elo-s3:<bucket>/config-crypt – Passphrase aus Pits Passwort-Manager
#   5. zeigt nur Access-Key-ID, Endpoint und Katalog-Einträge an – nie Passwort, Secret
#      oder Passphrase. Nichts davon landet in Logs, in der Shell-History oder in der
#      Prozessliste (JSON per stdin an curl, Token per Header-Datei).
#
# Voraussetzung: rclone, curl, jq. Bucket wird hier NICHT angelegt (rclone mkdir danach).
# Schlüssel neu erzeugen: Skript erneut starten; vorhandene Remotes werden nur nach
# Rückfrage ersetzt. Alte Schlüssel zurückziehen: openstack ec2 credentials delete …
set -euo pipefail

KEYSTONE="${KEYSTONE_URL:-https://api.pub1.infomaniak.cloud/identity/v3}"
S3_ENDPOINT_DEFAULT="${S3_ENDPOINT:-https://s3.pub2.infomaniak.cloud}"
BUCKET="${CLOUD_BUCKET:-elo-backup}"
S3_REMOTE="${S3_REMOTE:-elo-s3}"
CRYPT_REMOTE="${CRYPT_REMOTE:-elo-crypt}"
CONF="${RCLONE_CONFIG:-$HOME/.config/rclone/rclone.conf}"

for tool in rclone curl jq; do
    command -v "$tool" >/dev/null || { echo "FEHLER: $tool fehlt"; exit 1; }
done

umask 077
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT

if [ -f "$CONF" ] && grep -qE "^\[(${S3_REMOTE}|${CRYPT_REMOTE})\]" "$CONF"; then
    read -rp "Remote ${S3_REMOTE}/${CRYPT_REMOTE} existiert schon in $CONF. Ersetzen? (j/n): " ok
    [ "$ok" = "j" ] || { echo "Abgebrochen, nichts geändert."; exit 0; }
fi

read -rp  "OpenStack-Benutzer (PCU-…): " OS_USER
read -rp  "Projekt (PCP-…):            " OS_PROJECT
read -rsp "Passwort OpenStack-Benutzer: " OS_PASS; echo
# Nur die Länge zeigen (nie den Inhalt): erkennt leeres oder doppeltes Einfügen.
echo "  (${#OS_PASS} Zeichen erhalten)"

# ── 1a. Anmeldung OHNE Projekt: stimmen Benutzer + Passwort? ──────────────
jq -n --arg u "$OS_USER" --arg p "$OS_PASS" '{auth: {
    identity: {methods: ["password"], password: {user: {name: $u, domain: {name: "Default"}, password: $p}}}}}' \
    > "$TMP/auth0.json"
HTTP=$(curl -sS -o "$TMP/token0.json" -D "$TMP/headers0" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @"$TMP/auth0.json" "$KEYSTONE/auth/tokens")
rm -f "$TMP/auth0.json"
if [ "$HTTP" != "201" ]; then
    echo "FEHLER: Anmeldung ohne Projekt HTTP $HTTP – Benutzername oder Passwort stimmt nicht"
    echo "        (oder der Benutzer ist noch nicht aktiv; nach dem Anlegen einige Minuten warten)."
    jq -r '.error.message // empty' "$TMP/token0.json" 2>/dev/null || true
    exit 1
fi
echo "Benutzer + Passwort ok. Projekte dieses Benutzers:"
awk 'tolower($1)=="x-subject-token:" {print "X-Auth-Token: " $2}' "$TMP/headers0" | tr -d '\r' > "$TMP/authhdr0"
curl -sS -H @"$TMP/authhdr0" "$KEYSTONE/auth/projects" \
    | jq -r '.projects[]? | "  \(.name)  (ID \(.id), Domain \(.domain_id))"' || true
rm -f "$TMP/authhdr0" "$TMP/headers0" "$TMP/token0.json"

# ── 1b. Token holen (Projekt-scoped) ──────────────────────────────────────
jq -n --arg u "$OS_USER" --arg p "$OS_PASS" --arg proj "$OS_PROJECT" '{auth: {
    identity: {methods: ["password"], password: {user: {name: $u, domain: {name: "Default"}, password: $p}}},
    scope: {project: {name: $proj, domain: {name: "Default"}}}}}' > "$TMP/auth.json"
unset OS_PASS

HTTP=$(curl -sS -o "$TMP/token.json" -D "$TMP/headers" -w '%{http_code}' \
    -H 'Content-Type: application/json' --data-binary @"$TMP/auth.json" "$KEYSTONE/auth/tokens")
rm -f "$TMP/auth.json"
if [ "$HTTP" != "201" ]; then
    echo "FEHLER: Anmeldung am Projekt HTTP $HTTP"
    jq -r '.error.message // empty' "$TMP/token.json" 2>/dev/null || true
    echo "(Projektname aus der Liste oben verwenden.)"
    exit 1
fi
TOKEN=$(awk 'tolower($1)=="x-subject-token:" {print $2}' "$TMP/headers" | tr -d '\r')
printf 'X-Auth-Token: %s\n' "$TOKEN" > "$TMP/authhdr"
unset TOKEN
USER_ID=$(jq -r '.token.user.id' "$TMP/token.json")
PROJECT_ID=$(jq -r '.token.project.id' "$TMP/token.json")

echo
echo "Angemeldet: Benutzer-ID $USER_ID, Projekt-ID (Keystone) $PROJECT_ID"
echo "Katalog – Object-Storage-Einträge (public):"
jq -r '.token.catalog[] | select(.type|test("object|s3";"i")) | .type as $t
       | .endpoints[] | select(.interface=="public") | "  \($t)  \(.region // .region_id)  \(.url)"' \
    "$TMP/token.json" || true

# ── 2. EC2-Credentials erzeugen ───────────────────────────────────────────
jq -n --arg t "$PROJECT_ID" '{tenant_id: $t}' > "$TMP/ec2req.json"
HTTP=$(curl -sS -o "$TMP/ec2.json" -w '%{http_code}' -H @"$TMP/authhdr" \
    -H 'Content-Type: application/json' --data-binary @"$TMP/ec2req.json" \
    "$KEYSTONE/users/$USER_ID/credentials/OS-EC2")
if [ "$HTTP" != "201" ]; then
    echo "FEHLER: EC2-Credentials HTTP $HTTP"
    jq -r '.error.message // empty' "$TMP/ec2.json" 2>/dev/null || true
    exit 1
fi
ACCESS=$(jq -r '.credential.access' "$TMP/ec2.json")
SECRET=$(jq -r '.credential.secret' "$TMP/ec2.json")
rm -f "$TMP/ec2.json"

echo
read -rp "S3-Endpoint [${S3_ENDPOINT_DEFAULT}]: " EP
EP="${EP:-$S3_ENDPOINT_DEFAULT}"

# ── 3. Passphrase für die verschlüsselte Konfig-Ablage ────────────────────
echo
echo "Passphrase für ${CRYPT_REMOTE} (aus dem Passwort-Manager einfügen; ohne sie ist die"
echo "verschlüsselte .env-Kopie in der Cloud wertlos):"
while :; do
    read -rsp "  Passphrase:            " P1; echo
    read -rsp "  Passphrase wiederholen: " P2; echo
    [ -n "$P1" ] && [ "$P1" = "$P2" ] && break
    echo "  leer oder ungleich – nochmal"
done
CRYPT_PW=$(printf '%s' "$P1" | rclone obscure -)
unset P1 P2

# ── 4. rclone.conf schreiben (alte Abschnitte der beiden Remotes entfernen) ──
mkdir -p "$(dirname "$CONF")"
chmod 700 "$(dirname "$CONF")"
touch "$CONF"
awk -v a="[${S3_REMOTE}]" -v b="[${CRYPT_REMOTE}]" '
    /^\[/ { skip = ($0 == a || $0 == b) }
    !skip' "$CONF" > "$TMP/conf"
{
    cat "$TMP/conf"
    printf '\n[%s]\ntype = s3\nprovider = Other\naccess_key_id = %s\nsecret_access_key = %s\nendpoint = %s\nacl = private\n' \
        "$S3_REMOTE" "$ACCESS" "$SECRET" "$EP"
    printf '\n[%s]\ntype = crypt\nremote = %s:%s/config-crypt\nfilename_encryption = standard\ndirectory_name_encryption = true\npassword = %s\n' \
        "$CRYPT_REMOTE" "$S3_REMOTE" "$BUCKET" "$CRYPT_PW"
} > "$TMP/conf.new"
mv "$TMP/conf.new" "$CONF"
chmod 600 "$CONF"
unset SECRET CRYPT_PW

echo
echo "Fertig. $CONF (Rechte $(stat -c %a "$CONF")) enthält ${S3_REMOTE} und ${CRYPT_REMOTE}."
echo "Access-Key-ID: $ACCESS   Endpoint: $EP"
echo "Test:  rclone lsd ${S3_REMOTE}:"
