# Scraping-Status

Stand: 2026-06-28 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **9.268.017** |
| DB-Größe | **~9,4 GB** |
| Gruppen complete | **107 / 253** |
| Global-Gruppen complete | **51 / 51** — ELO ≥ 2300 weltweit vollständig ✅ |
| Spieler mit ≥ 1 Periode | **141.699** |

---

## Kern-Gruppen (Priorität 1–2)

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| female_top | 23 | 2400–2600 (F, inaktiv) | 2008-04 – 2026-04 | ✅ complete |
| male_control | 48 | 2400–2600 (M, age-matched) | 2008-04 – 2026-04 | ✅ complete |
| elite_2600 | 190 | ≥ 2600 | 2008-04 – 2026-05 | ✅ complete |
| swiss_2026 | 349 | — (SMM 2026) | 2009-01 – 2026-04 | ✅ partial (2008 fehlt) |
| female_2200 | 207 | 2200–2399 (F) | 2008-04 – 2026-05 | ✅ complete |
| male_2200 | 112 | 2200–2399 (M) | 2008-04 – 2026-04 | ✅ complete |

---

## Global-Gruppen (Mac Mini Backfill) — alle complete ✅

**51 Gruppen (global_02 – global_28b), ELO 2300–2603, alle Föderationen**

Zeitraum gescrapt: **2008-04-01 – 2026-04-01** (inkl. Pre-2012-Quartalsperioden)

| Bereich | Gruppen | ELO-Range | Abgeschlossen |
|---------|--------:|-----------|---------------|
| global_02 – global_11b | 12 | 2412 – 2603 | bis 2026-05-09 |
| global_12a – global_19b | 16 | 2351 – 2411 | bis 2026-05-17 |
| global_20a – global_23b | 8 | 2328 – 2350 | 2026-05-18 – 2026-05-21 |
| global_24a – global_25b | 4 | 2317 – 2327 | 2026-05-22 |
| global_26a – global_28b | 6 | 2300 – 2316 | **2026-05-22** (Chain, ~19:17 Uhr) |

→ **ELO ≥ 2300 weltweit vollständig und lückenlos gescrapt.**

---

## Female-Gruppen (Mac Mini Backfill) — laufend 🔄

**55 Gruppen (female_2100_01 – female_1800_24), ELO 1800–2199, nur Spielerinnen (F)**

Zeitraum: **2010-01-01 – 2026-04-01** (inkl. Pre-2012-Quartalsperioden)
Reihenfolge: jüngste Periode zuerst → älteste; **vollautomatische Chain** via `run_female_chain.sh`.

| Bereich | Gruppen | Spielerinnen | ELO-Range | Status |
|---------|--------:|-------------:|-----------|--------|
| female_2100_01 – female_2100_06 | 6 | 395 | 2104–2199 | ✅ complete |
| female_2000_01 – female_2000_09 | 9 | 626 | 2004–2103 | ✅ complete (seit 2026-06-03) |
| female_1900_01 – female_1900_16 | 16 | ~925 | 1903–2003 | ✅ complete (seit 2026-06-08, 11:42 Uhr) |
| female_1800_01 – female_1800_19 | 19 | ~1.400 | 1800–1902 | ✅ complete (seit 2026-06-17) |
| female_1800_20 – female_1800_24 | 5 | ~382 | 1800–1902 | ✅ complete (seit ~2026-06-28) |
| **Gesamt** | **55** | **3.952** | **1800–2199** | ✅ **alle complete** |

**Nächster Schritt:** female_1600-Gruppen anlegen (ELO 1600–1799, ~5.941 Spielerinnen — noch keine Gruppen in DB).

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| | |
|---|---|
| Dashboard | **https://scelo.chesspit.net** (BasicAuth) |
| Modus | **bis zu 12 Threads (2 Residential + 10 DC); aktuell 9 DC aktiv (DC-DE + DC-UK disabled)** |
| DC-Modus | Individuelle Von/Bis-Zeit pro Karte (Ortszeit, Timezone-basiert) |

### Alle Threads

| Thread | Typ | Profil | Föderationen | Timezone | Status |
|--------|-----|--------|--------------|----------|--------|
| T1 | Residential | semi_aggressive | DACH (Priority) | — | ✅ aktiv |
| T2 | Residential | normal | DACH (Priority) | — | ✅ aktiv |
| DC-DE (Slot 99) | Datacenter | semi_conservative | POL, UKR, LAT, LIT, EST, CZE, SVK, FID | Europe/Berlin | ⏸ disabled |
| DC-IN (Slot 100) | Datacenter | semi_conservative | IND, IRI | Asia/Kolkata | ✅ aktiv |
| DC-UK (Slot 101) | Datacenter | semi_conservative | ENG, SCO, WLS, IRL, NIR, DEN, NOR, SWE, FIN, ISL | Europe/London | ⏸ disabled |
| DC-US (Slot 102) | Datacenter | semi_conservative | USA, CAN | America/New_York | ✅ aktiv |
| DC-HK (Slot 103) | Datacenter | semi_conservative | CHN, VIE + Ozeanien | Asia/Hong_Kong | ✅ aktiv |
| DC-ES (Slot 104) | Datacenter | semi_conservative | ESP, ITA, POR, AND, GIB | Europe/Madrid | ✅ aktiv |
| DC-MX (Slot 105) | Datacenter | semi_conservative | FRA, BEL, NED, LUX | America/Mexico_City | ✅ aktiv |
| DC-AE (Slot 106) | Datacenter | semi_conservative | SRB, CRO, BIH, MKD, MNE, SLO, KOS, ALB, GRE, TUR | Asia/Dubai | ✅ aktiv |
| DC-DACH (Slot 107) | Datacenter | semi_conservative | GER, SUI, AUT (Vollbackfill) | Europe/Berlin | ✅ aktiv |
| DC-UPDATE (Slot 108) | Datacenter | semi_conservative | alle (Update-Batches, `update_only=1`) | Europe/Berlin | ✅ aktiv |

---

## Raspberry Pi (Slot 50 "Pi") — aktiv seit 2026-06-10

Raspberry Pi 500 als drittes Scraping-Gerät beim Bruder (Remote-Zugang via Tailscale).

| | |
|---|---|
| Gerät | Raspberry Pi 500 (Pi 5, ARM64, 8 GB), Benutzer `pit1` |
| Tailscale-IP | `100.125.193.29` |
| Profil | `normal` (1 Thread, kein Proxy — residential IP) |
| Queue | 1247 Gruppen (`device='raspi'`), Jahr 2020, ELO 1400–2840, alle Föderationen |
| Sync | `sync_pi_to_vps.sh` alle 5 Min → `merge_pi_status.py` → thread_slot 50 im Dashboard |

```bash
# Worker-Log:
ssh pit1@100.125.193.29 "tail -20 /tmp/worker_pi.log"
# Sync-Log:
ssh pit1@100.125.193.29 "tail -10 /tmp/sync_pi.log"
# Worker neu starten (falls nötig):
ssh pit1@100.125.193.29 "cd ~/fide-scraper && source .venv/bin/activate && kill \$(pgrep -f worker.py); sleep 2; nohup python3 orchestrator/worker.py > /tmp/worker_pi.log 2>&1 &"
```

**Hinweis SSH:** Tailscale-Tunnel muss aktiv sein. Falls Verbindung hängt:
```bash
/Applications/Tailscale.app/Contents/MacOS/Tailscale ping 100.125.193.29
```

### Residential Queue-Strategie (T0/T1)

**Nur DACH-Region**, Ziel: GER 1800+ bis 2022, AUT+SUI bis 2020–2021

| Priorität | Inhalt |
|-----------|--------|
| P1–P12 | GER 2026 ELO 1904–2002 |
| P13–P33 | Historische Gruppen 2010–2012, ELO ≥ 2300 (USA, POL, GER, ITA, ESP, RUS) |
| P1001–P3017 | DACH 2025–2023 (GER 1900+, AUT+SUI alle ELO) |
| P2001–P2234 | GER 1800–1899 2026 (234 Gruppen, interleaved) |
| P3013–P3246 | GER 1800–1899 2023 |
| P4001–P6069 | DACH 2022–2020 |
| P4053–P4286 | GER 1800–1899 2022 |
| P5059–P5292 | GER 1800–1899 2021 |
| P6057–P6290 | GER 1800–1899 2020 |
| P100000+ | DACH vor 2020 (deprioritisiert) |
| P500000+ | alle anderen Föderationen |

### DC Queue-Strategie

Jeder DC-Thread hat eigenen Pool (`thread_affinity`), Prio: 2026→2009, Jahr DESC / ELO DESC

| DC-Thread | Gruppen done | Gruppen pending | Jahresbereich |
|-----------|-------------:|----------------:|---------------|
| DC-DE | 82 | ~1.967 | 2009–2026 |
| DC-IN | 140 | ~2.344 | 2009–2026 |
| DC-UK | 46 | ~1.394 | 2009–2026 |
| DC-US | 59 | ~673 | 2009–2026 |
| DC-HK | 45 | ~530 | 2009–2026 |
| DC-ES | 11 | ~2.941 | 2009–2026 |
| DC-MX | 53 | ~3.079 | 2009–2026 |
| DC-AE | 2 | ~1.546 | 2009–2026 |

---

## Analytics-Frontend (Port 8055)

| | |
|---|---|
| URL | **https://scelo.chesspit.net/analytics** *(oder lokal Port 8055)* |
| Framework | Dash (Python), Multi-Page |
| Default-Spieler | Gukesh D (FIDE-ID 46616543, Weltmeister 2024) |

### Seiten

| Seite | Gruppe | Pfad | Beschreibung |
|-------|--------|------|--------------|
| ELO-Top100 | Aktiv | `/c` | Live-Top-100 Rangliste mit ELO-Verlauf |
| ELO-Verteilung | Aktiv | `/dist` | ELO-Verteilungshistogramm nach Kategorie |
| Spieler-Steckbrief | Aktiv | `/player-profile` | Profil + Rating-History + Spielstatistiken |
| Partien-Detail | Test | `/games` | Alle Partien eines Spielers, filterbar |
| GM/IM Entwicklung | Test | `/titles` | Zeitreihe der Titelträger |

---

## Änderungen Session 2026-06-28

### Mac Mini — female_1800_20–24 abgeschlossen, alle female_XX (1800–2199) complete
| Was | Details |
|-----|---------|
| **female_1800_20–24** ✅ | 5 Gruppen, ~382 Spielerinnen, `scraped_to='2026-05-01'` |
| **Alle 55 female-Gruppen** ✅ | ELO 1800–2199 komplett (female_2100–female_1800-Serie) |
| **DB-Stand** | 9.268.017 Partien, ~9,4 GB, 141.699 Spieler mit ≥ 1 Periode, 107/253 Gruppen complete |
| **Kein Backfill aktiv** | Mac Mini idle — nächste Gruppe noch nicht gestartet |
| **Nächster Schritt** | female_1600-Gruppen anlegen + seeden (~5.941 Spielerinnen, ELO 1600–1799, ~80 Gruppen) |

---

## Änderungen Session 2026-06-17

### Mac Mini — female_1800_11–18 abgeschlossen
| Was | Details |
|-----|---------|
| **female_1800_11–15** ✅ | Waren bereits vollständig in DB (inkl. pre-2012 Quartale) — `backfill_status` auf `complete` korrigiert |
| **female_1800_16** ✅ | Fehlende pre-2012 Quartalsperioden nachgeladen (16/16 Perioden), fertig 09:30 Uhr |
| **female_1800_17** ✅ | 7.877 Perioden, fertig 13:22 Uhr |
| **female_1800_18** ✅ | 7.172 Perioden, fertig 16:42 Uhr |
| **female_1800_19** ✅ | 8617/8617 Player-Period-Kombinationen, 0 Fehler, fertig 20:35 Uhr |
| **female_1800_20–24** ⏳ | 5 Gruppen offen — in neuer Session starten |
| **Spieler mit ≥ 1 Periode** | **140.168** (vs. 95.585 beim Start dc_update am 2026-06-07, +44.583) |

---

## Änderungen Session 2026-06-10

### Raspberry Pi 500 — Setup abgeschlossen, Scraping aktiv
| Was | Details |
|-----|---------|
| **Setup Phase 1–3** ✅ | Pi 500 eingerichtet: OS, SSH, Repo, venv, SSH-Key auf VPS, Tailscale (Fernzugriff via `100.125.193.29`) |
| **Worker aktiv** ✅ | 1.247 Gruppen (Jahr 2020, alle Föderationen außer DACH), 1 Thread (normal-Profil, kein Proxy), `profiles_pi.yaml` |
| **Pi-Sync** ✅ | `sync_pi_to_vps.sh` + `merge_pi_status.py`: alle 5 Min SCP → Merge → thread_slot 50 "Pi" im Dashboard |
| **VPS DB** ✅ | 1.247 Gruppen Jahr 2020 → `device='raspi'`, `thread_affinity=NULL`; 1 AUT-Gruppe → dc_dach |
| **Bugs gefixt** | `profile_manager.py`: dotenv vor PROFILES_PATH laden; `setup_pi_worker.sh`: fehlende .env-Einträge ergänzen |
| Commits | `efd21ec`, `95fe051`, `c85976c` |

### Mac Mini — female_1800_08–10 abgeschlossen
| Was | Details |
|-----|---------|
| **female_1800_04–07** ✅ | Abgeschlossen (seit 2026-06-09) |
| **female_1800_08–10** ✅ | Chain via `chain_female_1800_08_10.sh` abgeschlossen — _10 fertig 16:54 Uhr (5809/5810) |
| **female_1800_11–24** ⏳ | 14 Gruppen, ~1.037 Spielerinnen — Chain-Skripte ausstehend |
| **female_2000 Master** ✅ | `backfill_status = 'complete'` gesetzt (war vergessen worden) |

---

## Änderungen Session 2026-06-08

### Mac Mini — female_1900 abgeschlossen, female_1800 läuft
| Was | Details |
|-----|---------|
| **female_1900_01 – _16** ✅ | Komplette Reihe (16 Gruppen, ~925 Spielerinnen) abgeschlossen — _16 zuletzt um 11:42 Uhr (5191/5191, 0 Fehler) |
| **female_1800_01 – _02** ✅ | Abgeschlossen (5083/5084 bzw. 5824/5824 Perioden) |
| **female_1800_03** 🔄 | Läuft (Stand ~21:42 Uhr: 3665/5192, ETA ~22:45 Uhr) — letzte Gruppe der aktuellen Chain |
| **`scripts/chain_female_1900_16_1800_03.sh`** ✅ | Chain-Skript für female_1900_16 + female_1800_01–03, lief automatisch durch |
| **`scripts/chain_female_1800_04_07.sh`** ✅ | Folgekette vorbereitet (female_1800_04→05→06→07), morgen früh manuell starten |

### Orchestrator-Dashboard — Übersicht-Heatmap-Fix
| Was | Details |
|-----|---------|
| **Problem** | "Übersicht gesamt" zeigte ELO-Buckets ab 0 — `dc_update`-Batches tragen `elo_min=0` als Drift-Puffer (`REST_ELO_FLOOR` in `generate_update_batches.py`), wodurch leere Buckets weit unterhalb der realen Population (≥1400) sichtbar waren |
| **Fix** ✅ | Neue Konstante `OVERVIEW_ELO_FLOOR = 1400`; `query_overview()` klemmt `lo_bucket` jetzt auf `max(elo_min-Bucket, 1400)` — Worker-Auswahllogik (`get_fide_ids`, Drift-Schutz) bleibt unverändert |
| Deploy | Build + `up -d --no-deps dashboard` auf VPS ausgeführt |
| Commit | `6f53e4d` |

### Dokumentation
| Was | Details |
|-----|---------|
| **`docs/setup_raspi.pdf`** ✅ | PDF-Export von `docs/setup_raspi.md` erstellt (Pandoc → HTML → Chrome-Headless-Druck) |

---

## Änderungen Session 2026-06-07

### VPS Orchestrator — dc_update: monatliches Update für die "Rest"-Population
| Was | Details |
|-----|---------|
| **Problem** | Die 4 Update-Jobs `UP-ELO2300`/`UP-FEMALE`/`UP-GER`/`UP-DACH` (`update_jobs.yaml`) decken nur ~30.000 der 125.500 bereits gescrapten Spieler monatlich ab. Die übrigen **~95.585 "Rest"-Spieler** (alle gescrapten, aktiven Spieler außerhalb ELO≥2300/Female/GER/SUI/AUT) hatten keinen automatischen Refresh-Mechanismus |
| **`update_only`-Spalte** ✅ | Neue `scrape_groups.update_only`-Migration (`setup_db.py`); wenn `1`, filtert `get_fide_ids()` (`worker.py`) zusätzlich auf `EXISTS(scrape_periods WHERE status='ok')` — Update-Batches wählen garantiert nur bereits gescrapte Spieler aus, kein Risiko eines Vollbackfills durch Rating-Drift in dynamischen ELO-Bändern |
| **Auto-Einsortierung neuer Spieler** ✅ | Sobald ein Spieler erstmals vollständig gescraped wurde (Eintrag in `scrape_periods`), erscheint er automatisch im nächsten Update-Zyklus seines Föderations-Batches — ohne Re-Balancing |
| **`generate_update_batches.py`** ✅ | Neues Skript: 77 Batches generiert (1 pro Föderation, ELO 0–2299; große Föderationen ESP/IND in je 3 ELO-Unterbänder à 3.000–6.000 Spieler gesplittet); alle mit `thread_affinity='dc_update'`, `update_only=1`, Jahr 2026 — Initial-Lauf auf VPS ausgeführt: 95.585 Spieler erfasst |
| **`dc_update`-Thread aktiviert** ✅ | Slot 108, "Update", `enabled: true` in `profiles.yaml`; erster Lauf ITA/2026/0–2299 bestätigt korrekt 5.182 von 5.658 Spielern (nur bereits gescrapte) × 1 Periode |
| **`monthly_update.sh`** ✅ | Neuer Schritt 5/5: `reset_current_year.py` wird automatisch per SSH im VPS-Dashboard-Container ausgeführt (vorher nur als manueller Hinweis) — requeued die `dc_update`-Batches monatlich |
| **Bug-Fix `reset_current_year.py`** ✅ | Skript ignorierte `ORCHESTRATOR_DATA_DIR` und suchte die DB unter `/app/orchestrator/scraper.db` statt `/data/scraper.db` — hätte im Container nie funktioniert; jetzt nutzt es `setup_db.DB_PATH` |
| **Dashboard: Bericht Scraper** ✅ | DC-UPDATE-Spalte jetzt dauerhaft sichtbar (analog zu DC-DACH), auch ohne bisherige Run-Daten |
| Commits | `e59abe4` (dc_update Update-Batches + Migration + Worker-Filter), `757a0ff` (Bericht Scraper DC-UPDATE-Spalte) |

---

## Änderungen Session 2026-06-05

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2000_02 – _09** ✅ | 8 Gruppen, 561 Spielerinnen abgeschlossen (letzte: _09, 2026-06-03) |
| **female_1900_01 – _04** ✅ | 4 Gruppen, 269 Spielerinnen abgeschlossen |
| **female_1900_05 – _07** 🔄 | Chain gestartet: _05 läuft aktiv, _06 + _07 starten automatisch nach |

---

## Änderungen Session 2026-05-26

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **Bericht Länder-Tab** ✅ | Neuer Tab „🗺 Bericht Länder": hierarchische DataTable Welt → Kontinent → „In Arbeit"/„Ohne Daten" → Land; Gruppen-% + Spieler (gescraped/aktiv aus PostgreSQL); aufklappbar [+]/[−]; Auto-Refresh 300 s |
| **Bericht Scraper-Tab** ✅ | T1–T4 immer anzeigen (auch ohne Daten); Gesamt-Spalten neu: `%_Res | %_DC | MB_Res | MB_DC | Total` |
| **DC enabled-Flag Fix** ✅ | `run_dc_slot()` prüft `enabled` zwischen Gruppen; Toggle wirkt ohne Worker-Neustart (Commit `aec6f39`) |
| **Worker Restart Bug Fix** ✅ | Atomische `worker_state.json`-Writes + 1 s Startup-Grace; verhindert Sofort-Stopp von DC-Threads nach Neustart (Commit `92dd9a0`) |
| **profiles.yaml in git** ✅ | VPS-Version mit allen 8 DC-Threads nach git committed (Commit `68c0778`); war vorher nicht versioniert |
| **DC-DE + DC-UK** | `enabled: false` (manuell deaktiviert via Dashboard) |

---

## Änderungen Session 2026-05-25 (aktuell)

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2000_01** ✅ | 66 Spielerinnen, ELO 2090–2103, abgeschlossen |
| **female_2000_02** 🔄 | 65 Spielerinnen, ~50% fertig, läuft weiter |
| **run_female_chain.sh** | Chain läuft vollautomatisch durch alle female_XX-Gruppen |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **Worker-Neustart 17:14** | Nach HK-Abschluss (VIE 2025) automatisch neu gestartet, alle Threads aktiv |
| **Bericht-Tab** | Neuer Tab „📊 Bericht": tägliches Datenvolumen pro Thread (MB), Zwischensummen Residential/DC mit % |
| **3 neue DC-Scrapers** | DC-ES (ESP/ITA/POR/AND/GIB), DC-MX (FRA/BEL/NED/LUX), DC-AE (SRB/CRO/BIH/MKD/MNE/SLO/KOS/ALB/GRE/TUR) |
| **Übersicht-Heatmap** | DC-ES/MX/AE als neue Spalten ergänzt |
| **docker-compose.yml** | DC-ES/MX/AE Credentials als Env-Variablen in dashboard + worker |

### Analytics-Frontend
| Was | Details |
|-----|---------|
| **Navbar restrukturiert** | Gruppen „Aktiv" (ELO-Top100, ELO-Verteilung, Spieler-Steckbrief) und „Test" (Partien-Detail, GM/IM) |
| **Version A/B gelöscht** | `elo_a.py`, `elo_b.py`, `elo_dist_b.py` entfernt |
| **ELO-Top100** | Umbenannt von „Version C" |
| **Default-Spieler** | Gukesh D (46616543) auf allen Seiten als Vorauswahl |
| **Partien-Detail** | 2-Karten-Filter, Zeitraum-Slider (Ab Jahr), 2-zeilige Spaltenköpfe, keine Sortierung/Filter-Zeile |

---

## Änderungen Session 2026-05-23

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2100_01** 🔄→✅ | 65 Spielerinnen, ELO 2183–2199, abgeschlossen |
| **55 female_XX-Gruppen angelegt** | ELO 1800–2199, 3.952 Spielerinnen, 2010-01–2026-04, pending |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **GER 1800–1899 aktiviert** | 234 skipped → pending, interleaved in DACH-Jahresbänder |
| **Dashboard DC-Spalten** | Übersicht-Heatmap: DC-DE/IN/UK/US/HK als Aggregat-Spalten |
| **Dropdown-Favoriten** | ★ GER/SUI/AUT immer oben in Föderations-Dropdown |

---

## Änderungen Session 2026-05-22

### Mac Mini
| Was | Details |
|-----|---------|
| global_24b – global_28b ✅ | 9 Gruppen, ELO 2300–2324, alle fertig |
| **ELO ≥ 2300 complete** | **Alle 51 Gruppen fertig** — Chain-Script lief durch bis 19:17 Uhr |
| hist_-Gruppen | 5 historische Gruppen (2010–2012) in PostgreSQL angelegt |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **5 DC-Threads** | DC-DE/IN/UK/US/HK — alle mit eigenem Host/Credentials/Timezone |
| **DC Auto-Modus** | Timezone-basiert 07–23 Uhr Ortszeit, Toggle im Dashboard |
| **thread_affinity** | Jede Gruppe in SQLite-Queue hat DC-Thread-Zuweisung |

---

## Ältere Änderungen

→ Sessions 2026-05-13 bis 2026-05-21: global_14b – global_24a abgeschlossen,
   Dashboard-Verbesserungen, VPS-Profil-Umstellung, Retry-After-Fix.
