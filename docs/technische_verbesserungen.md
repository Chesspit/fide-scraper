# Technische Verbesserungen

Stand: 18. Mai 2026

---

## 0. Backfill-Workflow: VPS oder Mac Mini

**Primär:** Mac Mini lokal (ab 2026-04-29), weil die VPS-IP zeitweise von FIDE
geblockt wird. Residential-IPs wie der Mac Mini wirken natürlicher.

**Fallback:** VPS, sobald IP-Block aufgehoben ist (Prüfung nach 48h Pause).

### 0.1 Ablauf (du führst das selbst aus)

**Schritt 1 — SSH ins VPS** (Terminal auf dem Mac):
```bash
ssh pit@187.124.181.116
```

**Schritt 2 — Backfill starten:**
```bash
bash /opt/fide-scraper/backfill_group.sh GRUPPENNAME
```

Defaults: FROM=2008-04-01, TO=2026-03-01, --reverse (neueste Perioden zuerst).
Vor dem Backfill werden ungültige FIDE-Perioden (z.B. 2008-01) automatisch
als `no_data` vorausgefüllt (`prefill_invalid_periods.sh`).

Beispiele:
```bash
bash /opt/fide-scraper/backfill_group.sh male_2200
bash /opt/fide-scraper/backfill_group.sh male_2200 2008-04-01 2026-03-01
```

**Fertig.** Der Prozess läuft im Hintergrund weiter, auch nach dem Ausloggen.

**Fortschritt prüfen:**
```bash
tail /opt/fide-scraper/backfill_GRUPPENNAME.log
```

**Verfügbare Gruppen:** `female_top`, `male_control`, `elite_2600`,
`female_2200`, `male_2200`, `swiss_2026` — und jede künftige neue Gruppe.

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

### 0.3 Mac Mini lokal starten

```bash
# Seeden (einmalig pro Gruppe):
DATABASE_URL=postgresql://fide:nimzo194.@localhost:5434/fidedb \
  python3 scripts/seed_players.py --group global_03

# Backfill starten (caffeinate + auto-restart + tunnel-check):
bash scripts/run_local_backfill.sh global_03
```

Das Script `run_local_backfill.sh`:
- Verhindert Mac-Ruhemodus via `caffeinate -i`
- Startet SSH-Tunnel automatisch falls nicht aktiv
- Startet nach Absturz neu (inkl. Tunnel-Prüfung)
- Logs unter `/tmp/backfill_GRUPPE_local.log`

**NordVPN:** Optionaler Schutz der echten IP. Bei Overnight-Läufen stabil
auf einem Land lassen (Schweiz empfohlen — FIDE-Server in Lausanne).

---

## 1. Scraping-Geschwindigkeit

### 1.0 SGm Pre-Filter: no_data ohne HTTP-Request *(implementiert 2026-05-18)* ⭐

**Idee:** Die FIDE TXT-Snapshots enthalten pro Spieler und Monat die Spalte `SGm`
(Standard Games played). Wenn `SGm = 0`, hat der Spieler in diesem Monat keine
FIDE-gewerteten Partien gespielt — ein HTTP-Request würde garantiert `no_data` liefern.
Diese Requests können komplett übersprungen werden.

**Gemessene Einsparung nach ELO-Band:**

| ELO-Band | no_data-Rate | Geschwindigkeitsgewinn |
|---|---|---|
| ≥ 2400 | 59% | 2,4× schneller |
| 2300–2399 | 62% | 2,6× schneller |
| 2000–2199 | 62% | 2,6× schneller |
| 1800–1999 | 69% | 3,2× schneller |
| 1600–1799 | 77% | 4,3× schneller |
| 1400–1599 | 83% | 5,9× schneller |

**Implementierung (3 Dateien):**

1. **`scripts/seed_players.py`** — `detect_columns_from_header()` erkennt `SGm`-Spaltenposition;
   `parse_player_line()` gibt `std_games` im Dict zurück.

2. **`scripts/import_rating_snapshots.py`** — `upsert_rating_history()` schreibt
   `num_games` (= SGm) in `rating_history` via UPSERT. Gilt für alle 195 Snapshots
   (2006–2026). Pre-2013-Snapshots ohne SGm setzen `num_games = NULL` (kein Skip).

3. **`scripts/backfill.py`** + **`orchestrator/worker.py`** — Pre-Filter vor der
   Haupt-Scraping-Schleife:
   ```python
   # num_games=0 → direkt no_data schreiben, kein HTTP-Request
   # num_games IS NULL → Request trotzdem machen (kein TXT-Snapshot vorhanden)
   ```

**Einmalig ausführen** (befüllt num_games für alle ~1,8 Mio. Spieler rückwirkend):
```bash
DATABASE_URL=postgresql://fide:...@localhost:5434/fidedb \
  python scripts/import_rating_snapshots.py --force
```

**Verifizierung — so prüft man ob es funktioniert:**
```sql
-- 1. num_games ist befüllt:
SELECT COUNT(*) FROM rating_history WHERE num_games IS NOT NULL;
-- Erwartung: > 100 Mio. Zeilen

-- 2. Verhältnis überspringbar / gesamt:
SELECT
  SUM(CASE WHEN num_games = 0 THEN 1 ELSE 0 END) AS skip,
  SUM(CASE WHEN num_games > 0 THEN 1 ELSE 0 END) AS scrape,
  SUM(CASE WHEN num_games IS NULL THEN 1 ELSE 0 END) AS unknown
FROM rating_history;
-- Erwartung: skip ≈ 60-80% je nach ELO-Band
```

**Im Backfill-Log erkennbar:**
```
Pre-filter: 4832 periods skipped (num_games=0 in TXT snapshot)
Backfilling 3891 player-period combinations...
```
Ohne den Filter stünde dort: `Backfilling 8723 player-period combinations...`

**Wichtige Einschränkung:** `num_games IS NULL` bedeutet kein TXT-Snapshot vorhanden
(v.a. Spieler die erst nach 2026-04 hinzugekommen sind, oder Pre-2013-Perioden ohne SGm).
Diese werden normal gescrapt.

---

### 1.1 Menschliches Sleep-Muster *(implementiert 2026-04-29)*

**Datei:** `config.yaml` + `scraper/fetcher.py`

```yaml
backfill_rate_limit:
  min_sleep: 3.0
  max_sleep: 5.0
```

Sleep-Verteilung: **Beta(2,5)** — meistens ~3–3,5s, gelegentlich länger.
Zusätzlich 8 % Chance auf Extra-Pause von 4–6s (simuliert menschliches Lesen).

**Gemessener Durchsatz:** ~1.800 Combos/h (global_02, 6,9h für 12.544 Combos).
Effektiv ~2s/Combo, da viele `no_data`-Responses sehr schnell kommen.

### 1.2 Block-Erkennung (429/403) *(implementiert 2026-04-29)*

**Datei:** `scraper/fetcher.py`, `scripts/backfill.py`

- **HTTP 429 (rate limited):** Sofort 45-Minuten-Pause, dann Weiterfahren
- **HTTP 403 (blocked):** Sofortiger Stopp mit Fehlermeldung
- **HTTP-Code** wird in `scrape_periods.http_status` gespeichert

```python
class RateLimitedError(Exception): ...
class BlockedError(Exception): ...
```

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

### 1.3 --reverse Flag *(implementiert 2026-04-29)*

**Datei:** `scripts/backfill.py`

Scrapt Perioden von neu nach alt (2026-03 → 2013-01). Neueste Daten zuerst
verfügbar, und das Muster wirkt natürlicher (kein historischer Bulk-Scan).

```bash
python3 scripts/backfill.py --from 2013-01-01 --to 2026-03-01 --group global_03 --reverse
```

Ist Default in `backfill_group.sh` seit 2026-04-29.

---

## 2. SSH-Tunnel Stabilität *(implementiert 2026-04-25/29)*

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

`ensure_connection` in `db.py` wartet bei DB-Fehler bis zu **5 Minuten**
(10 Versuche mit 5s, 10s, 20s ... 60s Backoff) — genug Zeit für
Tunnel-Reconnect. Verhindert Prozessabsturz bei kurzen Unterbrüchen.

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
| 010 | no_data_reason in scrape_periods (system_gap / too_young / inactive) | 2026-04-28 |
| 011 | no_data_reason Enum-Constraint | 2026-04-28 |
| 012 | v_dynamic_membership View (dynamische Gruppenzugehörigkeit per Rating) | 2026-05-01 |

---

## 6. Scraping Orchestrator *(implementiert 2026-05-10)*

**Verzeichnis:** `orchestrator/`

Eigenständiges Tool für skalierbares globales Scraping via ProxyJet Rotating Residential Proxy.

### 6.1 Architektur

| Datei | Beschreibung |
|---|---|
| `app.py` | Dash-Dashboard (4 Tabs: Übersicht / Heatmap / Queue / Abgeschlossen) |
| `worker.py` | Worker-Schleife (Queue → ProxyJet → PostgreSQL) |
| `queue_manager.py` | SQLite-Queue, Prioritätsvergabe, Optimistic Locking, Startup-Reset |
| `proxy_manager.py` | ProxyJet Rotating Residential Proxy (eu.proxy-jet.io:1010) |
| `profile_manager.py` | Scrape-Profile + Fuzzy-Auswahl |
| `generate_groups.py` | 24.588 Gruppen (Föd. × Jahr × ELO-Band) generieren |
| `setup_db.py` | SQLite-Schema (scrape_groups, scrape_runs) |
| `profiles.yaml` | conservative / normal / aggressive + fuzzy_weights |
| `docker-compose.yml` | dashboard + worker Services auf VPS |

### 6.2 Features

| Feature | Details |
|---|---|
| 24.588 Gruppen | Föd. × Jahr (2009–aktuell) × ELO-Band |
| Priorität | Strikt nach priority-Spalte (TIER_WIDTH=1) |
| Fuzzy-Profil | Gewichtet: 0% conservative, 70% normal, 30% aggressive |
| Gerät-Zuweisung | device-Spalte: mac_mini / raspi / vps |
| Startup-Reset | Unterbrochene running-Gruppen → pending beim Start |
| Periods-Cap | valid_periods_for_year() gedeckelt auf Vormonat (keine Zukunftsanfragen) |
| Dashboard | Übersicht-Tab: Fortschritt nach Land & ELO-Band (grau→grün) |

### 6.3 Deployment

```bash
# VPS: Code pullen + Services starten
ssh pit@187.124.181.116
cd /opt/fide-scraper && git pull
cd orchestrator && docker compose up -d --build

# Dashboard via SSH-Tunnel:
ssh -N -L 8050:localhost:8050 pit@187.124.181.116
# → http://localhost:8050
```

### 6.4 Fuzzy-Profil-Gewichtung

| Profil | Gewicht | Wartezeit | Proxy |
|--------|--------:|-----------|-------|
| conservative | 0% | 8s ±50% | Ja |
| normal | 70% | 3s ±40% | Ja |
| aggressive | 30% | 1s ±30% | Nein |

> conservative bleibt als Option erhalten (manuell per Gruppe zuweisbar).
