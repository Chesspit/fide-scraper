# Raspberry Pi 500 — Setup-Anleitung

Raspberry Pi 500 (Pi 5, ARM64, 8 GB) als drittes Scraping-Gerät einrichten.

---

## Voraussetzungen

- Raspberry Pi 500 mit beiliegendem Netzteil und Micro-HDMI-Kabel
- Monitor
- WLAN-Zugangsdaten
- Mac Mini mit Zugang zum VPS (für SSH-Key-Eintrag)

---

## Phase 1: Erster Start

### 1.1 Pi 500 einschalten

USB-C Netzteil und Micro-HDMI-Kabel anschliessen, dann einschalten.
Der Pi 500 startet in den Raspberry Pi OS Setup-Wizard.

Setup-Wizard durchlaufen:
- Sprache und Tastaturlayout wählen
- Benutzer `pi` + Passwort setzen (merken!)
- WLAN auswählen und Passwort eingeben
- System-Update abwarten

### 1.2 SSH aktivieren

Nach dem ersten Login im Terminal:

```bash
sudo raspi-config
```

→ **Interface Options** → **SSH** → **Enable**

Ab jetzt ist der Pi ohne Monitor per SSH erreichbar:

```bash
ssh pi@raspberrypi.local
```

### 1.3 System updaten

```bash
sudo apt update && sudo apt upgrade -y
```

---

## Phase 2: Scraper installieren

### 2.1 Repo clonen

```bash
cd ~
git clone https://github.com/Chesspit/fide-scraper.git
cd fide-scraper
```

### 2.2 Setup-Script ausführen

```bash
bash scripts/setup_raspi.sh
```

Das Script erledigt automatisch:
- Python-Version prüfen (≥ 3.9)
- System-Pakete installieren (`libpq-dev` für psycopg2 auf ARM64)
- Python venv + Packages aus `scraper/requirements.txt`
- SSH-Key generieren (`~/.ssh/id_ed25519`)
- `.env` mit `WORKER_DEVICE=raspi` erstellen
- Tunnel + DB-Verbindung testen

### 2.3 SSH-Key auf VPS eintragen

Das Script gibt den Public Key aus. Diesen auf dem **Mac Mini** eintragen:

```bash
# Auf Mac Mini ausführen — KEY ersetzen durch Ausgabe des Scripts:
ssh pit@187.124.181.116 "echo 'ssh-ed25519 AAAA... raspi-fide-scraper' >> ~/.ssh/authorized_keys"
```

### 2.4 Verbindung testen

```bash
source .venv/bin/activate
bash scripts/tunnel.sh &
sleep 5
python3 -c "import psycopg2; psycopg2.connect('postgresql://fide:nimzo194.@localhost:5434/fidedb').close(); print('OK')"
```

---

## Phase 3: Tailscale (Remote-Zugang)

Tailscale ist ein kostenloses Mesh-VPN. Der Pi bekommt eine feste private IP-Adresse
(`100.x.x.x`) die von überall erreichbar ist — auch hinter NAT, ohne Port-Forwarding.

### 3.1 Tailscale-Account erstellen

→ [tailscale.com](https://tailscale.com) → **Sign up** (kostenlos)

### 3.2 Tailscale auf Pi installieren

```bash
curl -fsSL https://tailscale.com/install.sh | sh
sudo tailscale up
```

→ Ausgabe zeigt einen Login-Link → im Browser öffnen → mit Tailscale-Account einloggen.

Der Pi ist danach als `raspberrypi` (oder ähnlich) im Tailscale-Netzwerk sichtbar.

### 3.3 Tailscale auf Mac Mini installieren

```bash
brew install tailscale
sudo tailscale up
# → gleicher Account wie auf Pi
```

### 3.4 Verbindung testen (gleiche Netzwerk)

```bash
tailscale ping raspberrypi
ssh pi@raspberrypi
```

### 3.5 Fernzugriff testen (vor Abreise)

Pi an Handy-Hotspot anschliessen (anderes WLAN) und prüfen:

```bash
ssh pi@raspberrypi   # muss trotzdem funktionieren
```

Wenn das klappt, funktioniert es auch beim Bruder.

---

## Phase 4: WLAN für Bruder vorbereiten

WLAN-Zugangsdaten des Bruders vorher auf dem Pi eintragen:

```bash
sudo raspi-config
# → System Options → Wireless LAN → SSID + Passwort eingeben
```

Oder direkt in `/etc/wpa_supplicant/wpa_supplicant.conf` eintragen (mehrere WLANs möglich).

---

## Phase 5: Beim Bruder

1. Pi einstecken + starten (verbindet automatisch mit dem eingetragenen WLAN)
2. Vom Mac Mini verbinden:
   ```bash
   ssh pi@raspberrypi   # Tailscale macht das möglich
   ```
3. Scraping starten:
   ```bash
   source ~/fide-scraper/.venv/bin/activate
   cd ~/fide-scraper
   bash scripts/tunnel.sh &
   bash scripts/run_local_backfill.sh female_1800_01
   ```

---

## Scraping-Befehle auf dem Pi

```bash
# venv aktivieren (einmal pro Session):
source ~/fide-scraper/.venv/bin/activate
cd ~/fide-scraper

# Tunnel starten:
bash scripts/tunnel.sh &

# Gruppen-Backfill:
bash scripts/run_local_backfill.sh female_1800_01 2012-08-01

# Monatliches Update (UP-Jobs):
bash scripts/run_update_job.sh UP-GER
bash scripts/run_update_job.sh UP-FEMALE

# Female Chain:
bash scripts/run_female_chain.sh female_1800_01
```

---

## Vom Mac Mini auf Pi zugreifen

```bash
# SSH:
ssh pi@raspberrypi

# Log prüfen:
ssh pi@raspberrypi "tail -20 ~/fide-scraper/tmp/backfill_female_1800_01_local.log"

# Job im Hintergrund starten:
ssh pi@raspberrypi "cd ~/fide-scraper && source .venv/bin/activate && \
  nohup bash scripts/run_local_backfill.sh female_1800_01 > /tmp/backfill_female_1800_01_local.log 2>&1 &"
```

---

## Hinweise

- **Kein `caffeinate` nötig**: Pi im Headless-Mode schläft nicht
- **WORKER_DEVICE=raspi**: In `.env` gesetzt — Pi bearbeitet nur Gruppen die `device='raspi'` oder `device IS NULL` haben
- **Tunnel-Host bleibt gleich**: `pit@187.124.181.116` — egal wo der Pi steht
- **Proxy nicht nötig**: Pi-IP ist residential und nicht von FIDE geblockt
