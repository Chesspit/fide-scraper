# Scraping-Status

Stand: 2026-05-25 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **3.285.732** |
| Spieler mit ok-Daten | **28.728** |
| DB-Größe | **~7,2 GB (est.)** |
| Global-Gruppen complete | **51 / 51** — ELO ≥ 2300 weltweit vollständig ✅ |

---

## Kern-Gruppen (Priorität 1–2)

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| female_top | 23 | 2400–2600 (F, inaktiv) | 2008-04 – 2026-04 | ✅ gescrapt (wenig ok-Perioden, da inaktiv) |
| male_control | 48 | 2400–2600 (M, age-matched) | 2008-04 – 2026-04 | ✅ gescrapt |
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
| female_2000_01 – female_2000_09 | 9 | 671 | 2004–2103 | 🔄 female_2000_01 läuft (ETA ~20:00) |
| female_1900_01 – female_1900_16 | 16 | 1.117 | 1903–2003 | ⏳ pending |
| female_1800_01 – female_1800_24 | 24 | 1.769 | 1800–1902 | ⏳ pending |
| **Gesamt** | **55** | **3.952** | **1800–2199** | |

Laufzeitabschätzung Mac Mini (~14 Perioden/min): **~2–3 Tage gesamt**

Chain läuft automatisch durch alle 49 Gruppen:
```bash
# Status prüfen:
tail -20 /tmp/female_chain.log
# Bei Abbruch neu starten (ab erster pending-Gruppe):
bash scripts/run_female_chain.sh female_2000_02
```

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| | |
|---|---|
| Dashboard | **https://scelo.chesspit.net** (BasicAuth) |
| Modus | **bis zu 10 Threads (2 Residential + 8 DC)** |
| DC-Modus | Individuelle Von/Bis-Zeit pro Karte (Ortszeit, Timezone-basiert) |

### Alle Threads

| Thread | Typ | Profil | Föderationen | Timezone |
|--------|-----|--------|--------------|----------|
| T0 | Residential | semi_aggressive | DACH (Priority) | — |
| T1 | Residential | normal | DACH (Priority) | — |
| DC-DE (Slot 99) | Datacenter | semi_conservative | POL, UKR, LAT, LIT, EST, CZE, SVK, FID | Europe/Berlin |
| DC-IN (Slot 100) | Datacenter | semi_conservative | IND, IRI | Asia/Kolkata |
| DC-UK (Slot 101) | Datacenter | semi_conservative | ENG, SCO, WLS, IRL, NIR, DEN, NOR, SWE, FIN, ISL | Europe/London |
| DC-US (Slot 102) | Datacenter | semi_conservative | USA, CAN | America/New_York |
| DC-HK (Slot 103) | Datacenter | semi_conservative | CHN, VIE + Ozeanien | Asia/Hong_Kong |
| DC-ES (Slot 104) | Datacenter | semi_conservative | ESP, ITA, POR, AND, GIB | Europe/Madrid |
| DC-MX (Slot 105) | Datacenter | semi_conservative | FRA, BEL, NED, LUX | America/Mexico_City |
| DC-AE (Slot 106) | Datacenter | semi_conservative | SRB, CRO, BIH, MKD, MNE, SLO, KOS, ALB, GRE, TUR | Asia/Dubai |

### Residential Queue-Strategie (T1/T2)

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

**Zwischenziel:** GER 1800+ komplett bis 2022 ✓ / AUT+SUI komplett bis 2020–2021 ✓
→ ~666 Gruppen bis Zwischenziel

### DC Queue-Strategie

Jeder DC-Thread hat eigenen Pool (`thread_affinity`), Prio: 2026→2009, Jahr DESC / ELO DESC

| DC-Thread | Gruppen pending | Jahresbereich |
|-----------|----------------:|---------------|
| DC-DE | ~1.968 | 2009–2026 |
| DC-IN | ~2.344 | 2009–2026 |
| DC-UK | ~1.394 | 2009–2026 |
| DC-US | ~673 | 2009–2026 |
| DC-HK | ~530 | 2009–2026 |
| DC-ES | ~2.941 | 2009–2026 |
| DC-MX | ~3.079 | 2009–2026 |
| DC-AE | ~1.546 | 2009–2026 |

---

## Änderungen Session 2026-05-25

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2100_01–06** ✅ | Alle 6 Gruppen (395 Spielerinnen, ELO 2104–2199) abgeschlossen |
| **run_female_chain.sh** | Neues Script: kettet alle 49 female_XX-Gruppen vollautomatisch (seed → backfill → DB-update) |
| **female_2000_01** 🔄 | 66 Spielerinnen, ELO 2090–2103; läuft seit 13:38 Uhr, ETA ~20:00 |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **3 neue DC-Scraper** | DC-ES (ESP/ITA/POR/AND/GIB), DC-MX (FRA/BEL/NED/LUX), DC-AE (SRB/CRO/BIH/MKD/MNE/SLO/KOS/ALB/GRE/TUR) |
| **DC-UK Föds bereinigt** | NED/BEL/LUX zu DC-MX verschoben |
| **DC-US Föds bereinigt** | MEX zu DC-MX verschoben |
| **Queue-Affinities** | 7.530 Gruppen auf dc_es/dc_mx/dc_ae umgezogen (Bulk-UPDATE) |
| **Individuelle Von/Bis** | Kein globales DC-Zeitfenster mehr; jede Karte hat eigene Start/Endzeit |
| **Dashboard Standard-Tab** | App öffnet jetzt direkt auf Tab „Steuerung" |
| **DC-Karten Layout** | Alle 8 Karten in einer Zeile; alle Föderationen angezeigt; kein Max-Stunden-Feld |
| **docker-compose.yml** | DC-ES/MX/AE Credentials (`PROXYJET_DC_*`) als Env-Variablen in dashboard + worker |

---

## Änderungen Session 2026-05-23

### Mac Mini
| Was | Details |
|-----|---------|
| **female_2100_01** 🔄 | 65 Spielerinnen, ELO 2183–2199, läuft seit 09:45 Uhr |
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
| global_24b ✅ | 74 Spieler, ELO 2322–2324 |
| global_25a ✅ | 73 Spieler, ELO 2319–2321 |
| global_25b ✅ | 67 Spieler, ELO 2317–2318 |
| global_26a ✅ | 76 Spieler, ELO 2314–2316 |
| global_26b ✅ | 62 Spieler, ELO 2312–2313 |
| global_27a ✅ | 70 Spieler, ELO 2310–2311 |
| global_27b ✅ | 114 Spieler, ELO 2306–2309 |
| global_28a ✅ | 79 Spieler, ELO 2303–2305 |
| global_28b ✅ | 68 Spieler, ELO 2300–2302 |
| **ELO ≥ 2300 complete** | **Alle 51 Gruppen fertig** — Chain-Script lief durch bis 19:17 Uhr |
| hist_-Gruppen | 5 historische Gruppen (2010–2012) in PostgreSQL angelegt, über VPS Residential gescrapt |

### VPS Orchestrator
| Was | Details |
|-----|---------|
| **5 DC-Threads** | DC-DE/IN/UK/US/HK — alle mit eigenem Host/Credentials/Timezone |
| **DC Auto-Modus** | Timezone-basiert 07–23 Uhr Ortszeit, Toggle im Dashboard |
| **DC Individuell-Modus** | Timezone ignoriert, Threads laufen 24/7 wenn enabled |
| **thread_affinity** | Jede Gruppe in SQLite-Queue hat DC-Thread-Zuweisung |
| **T1/T2 DACH-Fokus** | Nur DACH 2020–2026; andere Föd. auf P500000+ geschoben |
| **Historische Gruppen** | 21 Gruppen (2010–2012, ELO ≥ 2300) auf P13–P33 — läuft über T1/T2 |
| **Dashboard** | 5 Tabs, DC-Toggle/Modus/Zeitfenster, Residential-Karten mit Toggle |
| **Neustart-Alert** | Zeigt aktive Konfiguration beim Neustart-Klick |

---

## Ältere Änderungen

→ Sessions 2026-05-13 bis 2026-05-21: global_14b – global_24a abgeschlossen,
   Dashboard-Verbesserungen, VPS-Profil-Umstellung, Retry-After-Fix.
