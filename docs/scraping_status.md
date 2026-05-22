# Scraping-Status

Stand: 2026-05-22 Abend (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **3.094.551** |
| Spieler mit ok-Daten | **20.943** |
| DB-Größe | **~6,8 GB** |
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

## Orchestrator (VPS) — föderationsbasiertes Scraping

| | |
|---|---|
| Dashboard | **https://scelo.chesspit.net** (BasicAuth) |
| Modus | **7 Threads (2 Residential + 5 DC)** |
| DC-Modus | Automatisch (Timezone-basiert, 07:00–23:00 Ortszeit) |

### Aktive Threads

| Thread | Typ | Profil | Föderationen | Timezone |
|--------|-----|--------|--------------|----------|
| T1 | Residential | semi_aggressive | DACH (Priority) | — |
| T2 | Residential | normal | DACH (Priority) | — |
| DC-DE (Slot 99) | Datacenter | semi_conservative | POL, UKR, LAT, LIT, EST, CZE, SVK, FID | Europe/Berlin |
| DC-IN (Slot 100) | Datacenter | semi_conservative | IND, IRI | Asia/Kolkata |
| DC-UK (Slot 101) | Datacenter | semi_conservative | ENG, SCO, WLS, IRL, NIR, DEN, NOR, SWE, FIN, ISL, NED, BEL, LUX | Europe/London |
| DC-US (Slot 102) | Datacenter | semi_conservative | USA, CAN, MEX | America/New_York |
| DC-HK (Slot 103) | Datacenter | semi_conservative | CHN, VIE + Ozeanien | Asia/Hong_Kong |

### Residential Queue-Strategie (T1/T2)

**Nur DACH-Region**, Ziel: 2020–2026 rückwärts

| Priorität | Inhalt |
|-----------|--------|
| P1–P12 | GER 2026 ELO 1904–2002 (neu aktiviert) |
| P13–P33 | Historische Gruppen 2010–2012, ELO ≥ 2300 (USA, POL, GER, ITA, ESP, RUS) |
| P1001–P3017 | DACH 2025–2023 (GER 1900+, AUT+SUI alle ELO) |
| P4001–P6069 | DACH 2022–2020 |
| P100000+ | DACH vor 2020 (deprioritisiert) |
| P500000+ | alle anderen Föderationen |

### DC Queue-Strategie

Jeder DC-Thread hat eigenen Pool (`thread_affinity`), Prio: 2026→2009, Jahr DESC / ELO DESC

| DC-Thread | Gruppen pending | Jahresbereich |
|-----------|----------------:|---------------|
| DC-DE | ~2.045 | 2009–2026 |
| DC-IN | ~2.482 | 2009–2026 |
| DC-UK | ~2.281 | 2009–2026 |
| DC-US | ~1.005 | 2009–2026 |
| DC-HK | ~557 | 2009–2026 |

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
| hist_-Gruppen | 5 historische Gruppen (2010–2012) in PostgreSQL angelegt, aber über VPS Residential gescrapt |

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

## Änderungen Session 2026-05-21

| Was | Details |
|-----|---------|
| global_23b ✅ | 77 Spieler, ELO 2328–2330, 4.981/4.982 erfolgreich |
| global_24a ✅ | 90 Spieler, ELO 2325–2327 |
| **Parallel-Modus** | VPS: 2 Residential Threads + DC-Thread |
| **DC-Thread (ProxyJet DE)** | Slot 99, semi_conservative, proxy-jet.io |
| Dashboard-Redesign | Tab-Struktur umgebaut, Thread-Karten eingeführt |

---

## Ältere Änderungen

→ Sessions 2026-05-13 bis 2026-05-20: global_14b – global_23a abgeschlossen,
   Dashboard-Verbesserungen, VPS-Profil-Umstellung, Retry-After-Fix.
