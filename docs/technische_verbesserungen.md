# Technische Verbesserungen

Stand: 26. April 2026

---

## 0. Backfill-Workflow: VPS als primäre Scraping-Maschine

Backfills sollten **immer vom VPS** aus gestartet werden — nicht vom Mac über
den SSH-Tunnel. Der VPS läuft 24/7, hat eine stabile Verbindung zur lokalen DB
und ist unabhängig von Mac-Ruhemodus oder Tunnel-Unterbrüchen.

### 0.1 Ablauf (du führst das selbst aus)

**Schritt 1 — SSH ins VPS** (Terminal auf dem Mac):
```bash
ssh pit@187.124.181.116
```

**Schritt 2 — tmux-Session starten** (Prozess läuft weiter nach Ausloggen):
```bash
tmux new -s backfill
```

**Schritt 3 — Backfill starten:**
```bash
docker compose -f /opt/fide-scraper/docker-compose.yml run --no-deps --rm \
  -e DATABASE_URL=postgresql://fide:nimzo194.@10.0.3.1:5432/fidedb \
  scraper python scripts/backfill.py --from 2010-01-01 --to 2026-03-01 \
  > /opt/fide-scraper/backfill_$(date +%Y-%m-%d).log 2>&1
```

**Schritt 4 — Session loslassen** (Prozess läuft weiter):
```
Ctrl+B, dann D
```

**Session später wieder anzeigen:**
```bash
ssh pit@187.124.181.116
tmux attach -t backfill
```

**Fortschritt prüfen** (vom Mac via Tunnel):
```bash
tail -5 /opt/fide-scraper/backfill_YYYY-MM-DD.log   # via SSH
# oder via Tunnel:
psql postgresql://fide:nimzo194.@localhost:5434/fidedb -c \
  "SELECT COUNT(*), ROUND(100.0*COUNT(*)/TOTAL,1) AS pct FROM scrape_periods ..."
```

### 0.2 Parallelbetrieb: Gruppen-Zuweisung (--group)

Jede Maschine bekommt eine oder mehrere Gruppen zugeteilt. Jede Maschine
arbeitet vollständig unabhängig — kein Ausfall einer Maschine beeinflusst
die anderen. **--shard ist dabei nicht nötig.**

**Typische Aufteilung bei neuen Gruppen (3 Maschinen):**

```bash
# VPS (SSH, tmux) — läuft immer stabil
docker compose -f /opt/fide-scraper/docker-compose.yml run --no-deps --rm \
  -e DATABASE_URL=postgresql://fide:nimzo194.@10.0.3.1:5432/fidedb \
  scraper python scripts/backfill.py \
  --from 2010-01-01 --to 2026-03-01 \
  --group neue_gruppe_A \
  > /opt/fide-scraper/backfill_vps.log 2>&1

# Mac Mini (Terminal, Tunnel offen)
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 scripts/backfill.py \
  --from 2010-01-01 --to 2026-03-01 \
  --group neue_gruppe_B \
  >> /tmp/backfill_mac_mini.log 2>&1 &

# MacBook Pro (Terminal, Tunnel offen, optional NordVPN)
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 scripts/backfill.py \
  --from 2010-01-01 --to 2026-03-01 \
  --group neue_gruppe_C \
  >> /tmp/backfill_macbook_pro.log 2>&1 &
```

**Verfügbare Gruppen:** `female_top`, `male_control`, `elite_2600`,
`female_2200`, `swiss_2026` — und jede künftige neue Gruppe.

**Wann ist --shard zusätzlich sinnvoll?**
Nur wenn eine einzelne neue Gruppe sehr gross ist (> 300 Spieler) und
eine Maschine allein zu lange brauchen würde:

```bash
# Beispiel: neue Gruppe mit 600 Spielern, ~37h auf einer Maschine → mit --shard auf 12h:
--group grosse_neue_gruppe --shard 1/3   # VPS
--group grosse_neue_gruppe --shard 2/3   # Mac Mini
--group grosse_neue_gruppe --shard 3/3   # MacBook Pro
```

**Faustregel:**
- Gruppe < 300 Spieler → eine Maschine, kein --shard nötig
- Gruppe > 300 Spieler → --shard über verfügbare Maschinen

### 0.3 Warum nicht vom Mac?

| Kriterium | Mac via Tunnel | VPS direkt |
|---|---|---|
| Stabilität | ❌ Tunnel kann abbrechen | ✅ Direkt, kein Tunnel |
| Ruhemodus | ❌ Mac schläft → Prozess stoppt | ✅ Läuft 24/7 |
| DB-Latenz | ❌ Tunnel-Overhead | ✅ Lokales Netz |
| Geschwindigkeit | gleich | gleich |

**NordVPN:** Sinnvoll als Backup-IP für das MacBook Pro bei parallelem Betrieb
oder falls FIDE einmal eine IP drosselt. Nicht als primäres Optimierungswerkzeug.

---

## 1. Scraping-Geschwindigkeit

### 1.1 Sleep-Zeiten reduziert *(implementiert 2026-04-25)*

**Datei:** `config.yaml`

```yaml
backfill_rate_limit:
  min_sleep: 1.0   # vorher: 2.0
  max_sleep: 2.0   # vorher: 4.0
```

**Speedup:** ~2× beim Backfill (von ~2.700 auf ~5.400 Perioden/Stunde).
FIDE's Endpoint toleriert die höhere Rate ohne 429-Fehler.
Normal-Scraping (monatlicher Lauf) bleibt bei 1,2–2,5s.

---

### 1.2 `--shard N/M` Flag *(implementiert 2026-04-25)*

**Datei:** `scripts/backfill.py`

Ermöglicht das Aufteilen eines Backfills auf mehrere Maschinen mit einem
einzigen Parameter. Jede Maschine verarbeitet jeden M-ten Eintrag der
Pending-Liste (Round-Robin), sodass beide Shards gleichzeitig fertig werden.

**Verwendung:**
```bash
# Maschine A (z.B. Mac via Tunnel) — erste Hälfte
DATABASE_URL=postgresql://fide:...@localhost:5434/fidedb \
  python3 scripts/backfill.py --from 2010-01-01 --to 2026-03-01 --shard 1/2

# Maschine B (z.B. VPS in tmux) — zweite Hälfte
docker compose run --no-deps --rm -e DATABASE_URL=... \
  scraper python scripts/backfill.py --from 2010-01-01 --to 2026-03-01 --shard 2/2
```

**Kombination mit `--fide-ids`** ist möglich für noch feingranularere Kontrolle.

**Warum Round-Robin statt Block-Split:**
- Block (erste 50% / zweite 50%): Shard 1 enthält alle frühen Perioden (viele
  no_data, schnell), Shard 2 alle späteren (viele ok, langsam) → ungleiche Laufzeit
- Round-Robin: jeder Shard enthält einen repräsentativen Mix → beide fertig
  zur gleichen Zeit

**IP-Sicherheit:** Jede Maschine scrapet mit eigener IP-Adresse. FIDE sieht
pro IP normale Nutzungsrate — kein erhöhtes Blocking-Risiko.

**Empfehlung:** Maximal 2 Shards einsetzen (Mac + VPS). Bei mehr als 2
Maschinen wäre eine `--shard 1/4` etc. Konfiguration möglich, aber die
verfügbare Infrastruktur limitiert den praktischen Nutzen.

---

### 1.3 Optionen für die Zukunft

#### Option A: Cloud-Instanzen für grosse Backfills

Für einmalige Backfills (z.B. 5.000 Spieler, ~13 Tage):

1. Kurze Cloud-VM buchen (Hetzner CX11: ~5€/Monat, oder spot)
2. Repo klonen + `.env` setzen
3. Mit `--shard 2/2` starten, nach Abschluss stoppen

**Kosten-Nutzen:** 2 Tage Cloud-VM statt 13 Tage warten → ~2€.

#### Option B: Async-Rewrite des Fetchers

Umbau von `requests` + `time.sleep` auf `asyncio` + `aiohttp` mit
gemeinsamem Semaphore:

```python
semaphore = asyncio.Semaphore(3)  # max 3 gleichzeitige Requests
async def fetch_with_rate_limit(session, fide_id, period):
    async with semaphore:
        await asyncio.sleep(random.uniform(0.5, 1.0))
        return await session.get(url)
```

**Speedup:** 3–4× von einer IP, ohne zusätzliche Infrastruktur.
**Aufwand:** ~1 Tag Code-Umbau.
**Risiko:** Höhere Last von einer IP — bei 3 concurrent und 0,5–1,0s Sleep
bleibt die effektive Rate bei ~3 Requests/Sekunde, was noch vertretbar ist.

---

## 2. SSH-Tunnel Stabilität *(implementiert 2026-04-25)*

**Datei:** `scripts/tunnel.sh`

**Problem:** Der SSH-Tunnel brach regelmässig ab wenn keine Aktivität war
(Server-seitiger Idle-Timeout), was laufende Backfill-Prozesse abstürzen liess.

**Lösung:** Keep-Alive-Pakete + Auto-Reconnect-Loop:

```bash
SSH_OPTS=(
    -N
    -L 5434:localhost:5432
    -o ServerAliveInterval=30    # keep-alive alle 30s
    -o ServerAliveCountMax=6     # reconnect nach 3 Min ohne Antwort
    -o ExitOnForwardFailure=yes
    -o TCPKeepAlive=yes
)

while true; do
    ssh "${SSH_OPTS[@]}" pit@187.124.181.116
    echo "Tunnel exited, reconnecting in 5s..."
    sleep 5
done
```

**Ergebnis:** Tunnel reconnectet automatisch ohne manuellen Eingriff.
`db.py` hat bereits einen eingebauten Reconnect-Mechanismus, der kurze
Tunnel-Unterbrechungen überbrückt.

---

## 3. Parser-Fixes für historische TXT-Dateien *(implementiert 2026-04-25)*

### 3.1 Kleingeschriebene Monats-Labels (pre-2015)

**Datei:** `scripts/seed_players.py`

FIDE-Dateien vor 2015 verwenden kleingeschriebene Monatsnamen im Header
(`sep12`, `oct13` statt `SEP12`, `OCT13`). Fix: `re.IGNORECASE` zum
`MONTH_RATING_PATTERN`.

```python
# Vorher
MONTH_RATING_PATTERN = re.compile(r"\b(?:JAN|FEB|...)\d{2}\b")

# Nachher
MONTH_RATING_PATTERN = re.compile(r"\b(?:JAN|FEB|...)\d{2}\b", re.IGNORECASE)
```

### 3.2 Doppelte fide_ids in alten Snapshots

**Datei:** `scripts/import_rating_snapshots.py`

Ältere FIDE-Listen enthalten gelegentlich doppelte Einträge für denselben
Spieler. Das führte zu `CardinalityViolation` beim Batch-Insert. Fix:
Deduplizierung per Dictionary vor dem INSERT.

```python
seen: dict[int, tuple] = {}
for p in players:
    if p["std_rating"]:
        seen[p["fide_id"]] = (p["fide_id"], period, p["std_rating"])
rows = list(seen.values())
```

---

## 4. QC-System Bug-Fix *(implementiert 2026-04-24)*

**Datei:** `scripts/quality_check.py`

**Problem:** Off-by-one in der Perioden-Bedingung für `scraped_change`.

Games in Periode T produzieren `published_rating[T]`. Deshalb entspricht
die Differenz `published[T2] − published[T1]` den Spielergebnissen in
Perioden **(T1, T2]**, nicht **[T1, T2)**.

```sql
-- Falsch (vorher):
AND gr.period >= p.period_start
AND gr.period <  p.period_end

-- Korrekt (nachher):
AND gr.period >  p.period_start
AND gr.period <= p.period_end
```

**Impact:** QC-OK-Rate stieg von 60,7 % auf 98,5 %. Die meisten früheren
„Errors" waren Randeffekt-Rauschen, keine echten Scraping-Fehler.

---

## 5. Datenbank-Migrationen (Übersicht)

| Migration | Inhalt | Datum |
|---|---|---|
| 001 | Initiales Schema (players, game_results, scrape_periods, rating_history) | 2026-04 |
| 002 | Analyse-Views (v_opponent_strength, v_rating_volatility, etc.) | 2026-04 |
| 003 | swiss_2026 Boolean-Flag in players | 2026-04 |
| 004 | qc_rating_check Tabelle | 2026-04-22 |
| 005 | rating_corrections Tabelle + März-2024-Daten | 2026-04-24 |
| 006 | correction-Spalte in qc_rating_check | 2026-04-24 |
| 007 | opponent_sex + tournament_type in game_results | 2026-04-25 |
| 008 | tournament_type: closed + knockout Kategorien | 2026-04-25 |
| 009 | expected_score + over_performance + opponent_match_quality | 2026-04-25 |
