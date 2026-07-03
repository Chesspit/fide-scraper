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

## Aufgabe 5 — Proxy-Integration (providerneutral, aktuell Webshare)

**Historie:** Ursprünglich **ProxyJet** (proxyjet.io) als Rotating-Residential-Proxy-Dienst — Domain am 2026-07-03 nicht mehr erreichbar (vermutlich Domain-Beschlagnahmung), kompletter Ausfall aller DC-Threads. Seitdem auf **Webshare** umgestellt, und `proxy_manager.py` providerneutral umgebaut, damit ein künftiger Wechsel nur noch Config/Credentials betrifft, keinen Code mehr.

Webshare (webshare.io, seit 2018 am Markt) liefert **keinen einzelnen Rotating-Gateway-Host**, sondern eine herunterladbare Liste einzelner statischer Datacenter-IPs (im gebuchten Plan: 100 Einträge), alle mit **einem gemeinsamen Credential-Paar**. `proxy_manager.py` gleicht das durch eigene Pool-Rotation aus: bei jedem Request wird zufällig eine `IP:PORT`-Kombination aus der Liste gewählt — Zugriff auf 100 verschiedene IPs statt einer einzigen, reduziert das Risiko einer erneuten FIDE-Sperre einer Einzel-IP bei Dauernutzung.

### Verbindungsformat

Standard-HTTP-Proxy mit Username/Password-Auth, IP und Port kommen aus dem Pool:

```
http://USERNAME:PASSWORD@IP:PORT
```

Verifiziert gegen Webshares offizielles Python-Beispiel (`requests.get(url, proxies={"http": "http://USER:PASS@IP:PORT/", ...})`) — exakt dieses Format, keine Sonderauth.

### Einrichtung

1. Account bei webshare.io anlegen, passenden Proxy-Plan buchen (Pay-as-you-go, siehe Kostenvergleich in `docs/scraping_status.md`, Session 2026-07-03).
2. IP-Liste über den Download-Link aus dem Dashboard holen (Proxy List → Download), Spalten 1+2 (`IP:PORT`) extrahieren, als `orchestrator/webshare_proxies.txt` speichern (**git-ignored**, eine Zeile pro Proxy).
3. Username/Passwort (Spalten 3+4, bei allen Einträgen identisch) in `.env` eintragen:

```env
PROXY_USERNAME=dein_username
PROXY_PASSWORD=dein_password
PROXY_POOL_FILE=orchestrator/webshare_proxies.txt
PROXY_DC_USERNAME=dein_username
PROXY_DC_PASSWORD=dein_password
```

### Modul `proxy_manager.py`

```python
import os, random, time
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

class ProxyManager:
    def __init__(self, username_env="PROXY_USERNAME", password_env="PROXY_PASSWORD",
                 host_override=None, pool_file=None):
        self.user = os.getenv(username_env)
        self.pw   = os.getenv(password_env)
        self._cooldown_until = 0  # Timestamp: Proxy pausiert bis
        pool_path = pool_file or os.getenv("PROXY_POOL_FILE")
        self._pool = self._load_pool(Path(pool_path)) if pool_path else []
        self.host = host_override or os.getenv("PROXY_HOST")
        self.port = os.getenv("PROXY_PORT", "1010")

    def get_proxy(self) -> dict | None:
        """Gibt ein requests-kompatibles Proxy-Dict zurück, oder None bei Cooldown."""
        if time.time() < self._cooldown_until:
            return None  # Fallback auf direkten Request
        if self._pool:
            host, port = random.choice(self._pool)  # zufällige IP aus dem Pool
        else:
            host, port = self.host, self.port        # Single-Host-Modus (echter Gateway)
        url = f"http://{self.user}:{self.pw}@{host}:{port}"
        return {"http": url, "https": url}

    def report_block(self, cooldown_seconds: int = 60):
        """Aufrufen bei HTTP 429 oder Verbindungsfehler."""
        self._cooldown_until = time.time() + cooldown_seconds
```

(Vollständige Version inkl. Thread-Safety und `#`-Kommentare im Pool-File: siehe `orchestrator/proxy_manager.py`.)

### Verhalten pro Scrape-Profil

| Profil       | Proxy-Nutzung         | Bei 429                          |
|--------------|-----------------------|----------------------------------|
| conservative | immer                 | 120s Cooldown, dann weiter       |
| normal       | immer                 | 60s Cooldown, dann weiter        |
| aggressive   | optional (`.env`-Flag)| sofortiger Retry ohne Proxy      |

### Hinweise

- Pool-Rotation ersetzt bei Webshare das automatische IP-Cycling, das ein echter Rotating-Gateway (wie ProxyJet früher) selbst übernommen hätte — 100 IPs sind reichlich Spielraum, um Dauernutzung einer Einzel-IP zu vermeiden
- Bei einem künftigen Provider mit echtem Rotating-Gateway: einfach `host_override` statt `pool_file` setzen, kein Code-Umbau nötig (beide Modi koexistieren in `proxy_manager.py`)
- Trage `webshare_proxies.txt` und `.env` in `.gitignore` ein (bereits erledigt)

### Bekannte Regression: Geo-Ausrichtung der DC-Threads geht verloren

**Ursprüngliches Design-Ziel** (ProxyJet-Ära): jeder DC-Thread hatte einen **eigenen, regionsspezifischen** Proxy-Host (`dc_us` → `ca.proxy-jet.io`, `dc_hk`/`dc_in`/`dc_ae` → `in.proxy-jet.io`, `dc_uk`/`dc_es`/`dc_dach` → `eu.proxy-jet.io`), kombiniert mit passender `timezone`+`active_hours` — Ziel: Anfragen aus/für eine Föderation sollten von einer IP in einer plausiblen Region kommen, **zur dortigen Wachzeit** (grob 16h/Tag), damit das Zugriffsmuster menschlich wirkt statt wie automatisiertes Scraping rund um die Uhr aus einer einzigen Quelle.

**Mit dem Wechsel auf Webshare geht das verloren:** alle 10 DC-Threads teilen sich denselben 100-IP-Pool (`orchestrator/webshare_proxies.txt`), jede Anfrage zieht eine zufällige IP daraus — die IPs "streuen über mehrere Regionen" (siehe Prüfung oben), aber **ohne Kontrolle darüber, welche Region welchem Thread zugeordnet ist**. `active_hours`+`timezone` pro Thread funktionieren technisch weiterhin (siehe Dashboard-Steuerung), aber die **geografische Plausibilität zwischen Zeitfenster und tatsächlich genutzter IP-Region ist nicht mehr sichergestellt** — ein `dc_us`-Request (zeitlich auf US-Geschäftszeiten getrimmt) kann z.B. über eine IP aus Europa oder Asien laufen.

**Nicht behoben, bewusst offen gelassen** (Stand 2026-07-03) — mögliche künftige Ansätze, falls das priorisiert werden soll:
1. Prüfen, ob Webshares Dashboard Land-/Regions-Filter beim Erzeugen der IP-Liste anbietet — falls ja, mehrere `pool_file`s (je Region eine) statt eines gemeinsamen Pools, dann pro DC-Thread die passende Datei zuweisen (analog zum alten ProxyJet-Host-Modell, nur mit Pool statt Einzel-Host).
2. Ohne Regions-Filter: IPs anhand einer GeoIP-Lookup-Bibliothek einmalig klassifizieren und in Buckets pro DC-Thread aufteilen — mehr Aufwand, keine Anbieter-Abhängigkeit.
3. Bewusst in Kauf nehmen, falls das Muster in der Praxis nicht zu erhöhter Sperr-Rate führt — bisher (siehe Health-Check-Ergebnisse) keine Hinweise auf ein akutes Problem, nur ein Abweichen vom ursprünglichen Design-Ziel.

**Machbarkeits-Check (2026-07-03):** GeoIP-Klassifizierung aller 100 Pool-IPs (via ip-api.com Batch-Lookup, Ansatz 2 oben) nach den vom User vorgeschlagenen 4 Regionen — lebend/gesamt (12 zu diesem Zeitpunkt tote IPs, siehe unten, ausgeklammert):

| Region | Lebend | Gesamt | Länder (Top) |
|---|---:|---:|---|
| Amerikas | 25 | 28 | USA (15), Kanada (5), Brasilien (4) |
| Europa | 49 | 53 | Frankreich (6), Italien (5), Deutschland (5), UK (4), Spanien (4), CH (4), + 20 weitere |
| Asien-Ozeanien | 9 | 9 | Singapur (3), Japan (3), Australien (3) |
| Naher Osten | **0** | 4 | Türkei (4) — **alle 4 aktuell tot**, zufällig identisch mit dem defekten `166.88.110.x`-Subnetz |
| (nicht zugeordnet) | — | 6 | Afrika |

**Fazit:** Code-technisch trivial umsetzbar (nur neue `pool_file`s + Zuweisung pro Thread in `profiles.yaml`, `proxy_manager.py` braucht keine Änderung — unterstützt pro-Instanz-Pools bereits). **Praktisch aber noch nicht robust genug** bei nur 100 IPs im Gesamt-Kontingent: Naher Osten hat gerade 0 nutzbare IPs, Asien-Ozeanien nur 9 (dünn für mehrere parallele Threads). Europa und Amerikas sind dagegen gut bestückt. Bevor eine echte Aufteilung sinnvoll ist, entweder (a) bei Webshare gezielt IPs für Naher Osten/Asien-Ozeanien nachkaufen/anfragen, falls das Dashboard das erlaubt, oder (b) die dünnen Regionen vorerst mit der Europa-/Amerikas-Region zusammenlegen statt strikt 4 getrennte Pools zu fahren.

**Ersatz-Runde 1 (2026-07-03):** User hat 5 der 12 toten IPs bei Webshare ersetzt (1× Ägypten, 4× Türkei/`166.88.110.x`). Nachgetestet: nur der Ägypten-Ersatz (`154.73.250.233:6134`) funktioniert — **alle 4 Türkei-Ersatz-IPs liegen wieder im selben `166.88.110.0/24`-Subnetz und sind ebenfalls tot.** Hinweis für den nächsten Webshare-Support-Kontakt: nicht einzelne IPs meldenden, sondern explizit erwähnen, dass der gesamte `166.88.110.0/24`-Block von unserem Netzwerkpfad aus unerreichbar ist — sonst ersetzt der Automatismus vermutlich wieder innerhalb desselben kaputten Blocks.

`orchestrator/webshare_proxies.txt` (lokal + VPS) wurde auf den aktuellen Webshare-Stand synchronisiert (100 Einträge, davon weiterhin ~11 tot). Der Pool-Rotation-Mechanismus toleriert das bereits gut (siehe Fix vom selben Tag: frischer Proxy pro Retry-Versuch) — keine Notwendigkeit, tote IPs manuell aus der Datei zu entfernen, nur bei jedem Sync/Tausch **den Worker neu starten** (`docker compose restart worker`), da `ProxyManager` die Pool-Datei nur beim Start einliest, nicht live nachlädt.

**Noch offen:** 7 der ursprünglich 12 toten IPs (außerhalb der Türkei-Subnetz-Ersatzrunde) sowie die 4 Türkei-Ersatz-IPs — insgesamt weiterhin ~11 tote Einträge im Pool.

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
