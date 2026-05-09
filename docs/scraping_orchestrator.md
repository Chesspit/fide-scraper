# Scraping Orchestrator — Briefing für Claude Code

## Kontext

Dieses Projekt ist Teil der FIDE-Ratings-Datenanalyse. Ein Scraper existiert bereits. Ziel dieses Teilprojekts ist ein **Verwaltungstool**, das:

1. Die zu scrapenden Daten in **Gruppen** aufteilt (Land × Jahr × Elo-Bereich)
2. Eine **Queue** dieser Gruppen verwaltet und in zufälliger, nicht erkennbarer Reihenfolge abarbeitet
3. Den Fortschritt in einem **Web-Dashboard** als 2D-Grid visualisiert
4. Auf dem **Hostinger VPS** läuft (Python-Dienst, kein PaaS)

Lies vor dem Start alle relevanten Dateien im Projekt — insbesondere die Verteilung der Elo-Zahlen und die Liste der abzudeckenden Länder. Diese Daten sind massgebend für die Gruppen-Definitionen.

---

## Aufgabe 1 — Gruppen-Modell & Datenbank

Erstelle ein SQLite-Schema mit mindestens folgenden Tabellen:

```sql
scrape_groups (
  id          INTEGER PRIMARY KEY,
  land        TEXT NOT NULL,   -- ISO-3166 Alpha-3 oder FIDE-Code, wie im Projekt definiert
  year        INTEGER NOT NULL,
  elo_min     INTEGER NOT NULL,
  elo_max     INTEGER,         -- NULL = unbegrenzt (z.B. 2400+)
  status      TEXT DEFAULT 'pending',  -- pending | running | done | failed | skipped
  priority    INTEGER DEFAULT 5,       -- 1 (hoch) bis 10 (niedrig)
  retries     INTEGER DEFAULT 0,
  last_run_at TEXT,            -- ISO-8601 Timestamp
  record_count INTEGER,        -- Anzahl gefundener Einträge nach dem Scrape
  notes       TEXT
)

scrape_runs (
  id           INTEGER PRIMARY KEY,
  group_id     INTEGER REFERENCES scrape_groups(id),
  started_at   TEXT,
  finished_at  TEXT,
  status       TEXT,           -- success | failed | timeout
  records_found INTEGER,
  error_msg    TEXT,
  proxy_used   TEXT
)
```

- Leite die konkreten Elo-Bänder (`elo_min`, `elo_max`) aus den bestehenden Projektdaten ab
- Leite die Länderliste aus den bestehenden Projektdaten ab
- Jahresbereich: **2009–2026**
- Generiere alle Gruppen per kartesischem Produkt und befülle die Tabelle mit Status `pending`
- Erstelle Indices auf `(land, year, status)` für schnelle Filterabfragen

---

## Aufgabe 2 — Queue & Fuzzy-Scheduling

Implementiere ein Modul `queue_manager.py` mit folgender Logik:

### Nächste Gruppe auswählen (`get_next_group`)

- Wähle aus allen `pending`-Gruppen **nicht** sequenziell, sondern per **gewichtetem Zufalls-Sampling**
- Gewichtungsfaktoren (kombiniert):
  - `priority` aus der Datenbank (niedrigerer Wert = höheres Gewicht)
  - Leichte Bevorzugung von Jahren, die noch wenig `done`-Einträge haben
  - Leichte Bevorzugung von Elo-Bereichen, die im aktuellen Lauf noch nicht vorgekommen sind
- Ziel: kein erkennbares chronologisches oder geographisches Muster in der Abfolge

### Timing-Jitter (`get_wait_time`)

- Nimmt ein **Scrape-Profil** als Parameter (siehe Aufgabe 3)
- Gibt eine Wartezeit zurück: `base_wait * (1 + random.uniform(-jitter, +jitter))`
- Wartezeit nie unter einem konfigurierbaren Minimum

---

## Aufgabe 3 — Scrape-Profile

Erstelle eine Konfigurationsdatei `profiles.yaml` mit drei vordefinierten Profilen:

```yaml
profiles:
  conservative:
    base_wait_seconds: 8
    jitter: 0.5          # ±50 %
    timeout_seconds: 30
    max_retries: 5
    use_proxy: true

  normal:
    base_wait_seconds: 3
    jitter: 0.4
    timeout_seconds: 20
    max_retries: 3
    use_proxy: true

  aggressive:
    base_wait_seconds: 1
    jitter: 0.3
    timeout_seconds: 10
    max_retries: 2
    use_proxy: false
```

Das aktive Profil soll zur Laufzeit über die Web-UI wechselbar sein (kein Neustart nötig).

---

## Aufgabe 4 — Web-Dashboard (Dash)

Erstelle eine Dash-App `app.py` mit einer einzigen Hauptseite:

### Status-Grid

- **X-Achse:** Jahre 2009–2026
- **Y-Achse:** Elo-Bänder (aus Projektdaten abgeleitet)
- **Filter:** Dropdown zur Länderauswahl (ein Land pro Ansicht)
- **Zellenfarbe** kodiert den Status:
  - `done` → grün (`#1D9E75`)
  - `pending` → blau (`#378ADD`)
  - `failed` → rot (`#E24B4A`)
  - `running` → orange (`#EF9F27`)
  - `skipped` → grau (neutral)
- Klick auf eine Zelle öffnet ein kleines Popup/Modal mit Details (last_run, record_count, retries) und ermöglicht manuelles Status-Überschreiben

### Steuerleiste

- Dropdown: aktives Scrape-Profil wählen
- Button: Scraper starten / pausieren / stoppen
- Anzeige: aktuell laufende Gruppe, geschätzte Restzeit, Erfolgsrate

### Gruppen-Verwaltung

- Formular: neues Land / Jahr / Elo-Bereich auf `pending` setzen
- Button: ganzes Jahr für ein Land auf `pending` setzen
- Button: alle Gruppen eines Landes auf `skipped` setzen

### Metriken (oben)

- Gesamt / Done / Pending / Failed als kompakte Zahlenkarten
- Auto-Refresh alle 10 Sekunden (Dash Interval)

---

## Aufgabe 5 — Proxy-Integration via ProxyJet

Wir verwenden **ProxyJet** (proxyjet.io) als Rotating Residential Proxy-Dienst. ProxyJet verrechnet nur erfolgreiche Requests (`No charge for failed requests`) und hat eine Bandbreiten-Gültigkeit von 1 Jahr — kein monatlicher Verfall.

### Verbindungsformat

ProxyJet verwendet den Standard-Residential-Proxy-Endpunkt mit HTTP/SOCKS5 und Username/Password-Auth:

```
http://USERNAME:PASSWORD@gate.proxyjet.io:PORT
```

Die genauen Endpunkt-Details (Host, Port, optionale Country-Targeting-Parameter) findest du nach dem Login im ProxyJet Dashboard unter "Access Details". Trage diese in eine `.env`-Datei ein:

```env
PROXYJET_USERNAME=dein_username
PROXYJET_PASSWORD=dein_password
PROXYJET_HOST=gate.proxyjet.io
PROXYJET_PORT=10000
```

### Modul `proxy_manager.py`

```python
import os, random, time
from dotenv import load_dotenv

load_dotenv()

class ProxyJetManager:
    def __init__(self):
        self.user = os.getenv("PROXYJET_USERNAME")
        self.pw   = os.getenv("PROXYJET_PASSWORD")
        self.host = os.getenv("PROXYJET_HOST", "gate.proxyjet.io")
        self.port = os.getenv("PROXYJET_PORT", "10000")
        self._cooldown_until = 0  # Timestamp: Proxy pausiert bis

    def get_proxy(self) -> dict | None:
        """Gibt ein requests-kompatibles Proxy-Dict zurück, oder None bei Cooldown."""
        if time.time() < self._cooldown_until:
            return None  # Fallback auf direkten Request
        url = f"http://{self.user}:{self.pw}@{self.host}:{self.port}"
        return {"http": url, "https": url}

    def report_block(self, cooldown_seconds: int = 60):
        """Aufrufen bei HTTP 429 oder Verbindungsfehler."""
        self._cooldown_until = time.time() + cooldown_seconds
```

### Verhalten pro Scrape-Profil

| Profil       | Proxy-Nutzung         | Bei 429                          |
|--------------|-----------------------|----------------------------------|
| conservative | immer                 | 120s Cooldown, dann weiter       |
| normal       | immer                 | 60s Cooldown, dann weiter        |
| aggressive   | optional (`.env`-Flag)| sofortiger Retry ohne Proxy      |

### Hinweise

- ProxyJet rotiert die IPs automatisch bei jedem neuen Request — kein manuelles IP-Cycling nötig
- Country Targeting ist möglich (z.B. `gate.proxyjet.io:10000` mit Parameter `?country=de`) — lies die genaue Syntax im ProxyJet Dashboard nach, da sich Endpunkte gelegentlich ändern
- Bandbreite wird nur bei erfolgreichen Responses verbraucht — fehlgeschlagene Requests kosten nichts
- Trage `proxies.txt` und `.env` in `.gitignore` ein

---

## Aufgabe 6 — Deployment auf Hostinger VPS

Erstelle folgende Dateien für den VPS-Betrieb:

### `Dockerfile`
- Python 3.11 slim
- Installiert alle Abhängigkeiten aus `requirements.txt`
- SQLite-Datei in einem gemounteten Volume (`/data/scraper.db`)

### `docker-compose.yml`
- Service `dashboard`: Dash-App, Port 8050
- Service `worker`: Queue-Worker-Loop (separater Prozess)
- Volume für `/data`
- Restart-Policy: `unless-stopped`

### `caddy/Caddyfile` (Reverse Proxy)
```
deine-domain.example.com {
    reverse_proxy dashboard:8050
}
```

### `requirements.txt`
Mindestens: `dash`, `plotly`, `flask`, `requests`, `beautifulsoup4`, `apscheduler`, `pyyaml`, `python-dotenv`

---

## Arbeitsweise

- Diskutiere zuerst die abgeleiteten Elo-Bänder und die Länderliste mit mir, bevor du Code schreibst
- Implementiere eine Aufgabe nach der anderen; warte auf mein OK bevor du zur nächsten gehst
- Schreibe Tests für `queue_manager.py` und `proxy_manager.py`
- Kein eigener Scraping-Code — der bestehende Scraper wird als Modul importiert
