# Scraping-Status

Stand: 2026-05-10 18:10 (Quelle: `groups`-Tabelle DB)

---

## Kern-Gruppen (Priorität 1–2)

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| female_top | 66 | 2400–2600 (F) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_control | 649 | 2400–2600 (M, age-matched) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| elite_2600 | 202 | ≥ 2600 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| swiss_2026 | 349 | — (SMM 2026) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| female_2200 | 321 | 2200–2399 (F) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_2200 | 170 | 2200–2399 (M, age-matched) | 2013-01 – 2026-04 | ✅ complete |

> Hinweis: 2008-04 bis 2008-12 (3 Quartalsperioden) für alle Kern-Gruppen noch nicht gescrapt.
> Vor 2008-04 keine Einzelpartien-Daten bei FIDE verfügbar.

---

## Global-Gruppen (Priorität 3) — abgeschlossen

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| global_02 | 147 | 2556–2603 | 2012-08 – 2026-04 | ✅ complete |
| global_03 | 144 | 2528–2555 | 2012-08 – 2026-04 | ✅ complete |
| global_04 | 135 | 2502–2527 | 2012-08 – 2026-04 | ✅ complete |
| global_05a | 83 | 2483–2492 | 2012-08 – 2026-04 | ✅ complete |
| global_05b | 72 | 2493–2501 | 2012-08 – 2026-04 | ✅ complete |
| global_06a | 67 | 2466–2474 | 2012-08 – 2026-04 | ✅ complete |
| global_06b | 70 | 2475–2482 | 2012-08 – 2026-04 | ✅ complete |
| global_07a | 79 | 2451–2458 | 2012-08 – 2026-04 | ✅ complete |
| global_07b | 72 | 2459–2465 | 2012-08 – 2026-04 | ✅ complete |
| global_08a | 64 | 2440–2445 | 2012-08 – 2026-04 | ✅ complete |
| global_08b | 60 | 2446–2450 | 2012-08 – 2026-04 | ✅ complete |
| global_09a | 87 | 2428–2433 | 2012-08 – 2026-04 | ✅ complete |
| global_09b | 58 | 2434–2439 | 2012-08 – 2026-03 | ✅ complete |
| global_10a | 83 | 2416–2421 | 2012-08 – 2026-03 | ✅ complete |
| global_10b | 74 | 2422–2427 | 2012-08 – 2026-04 | ✅ complete |
| global_11a | 73 | 2407–2411 | 2012-08 – 2026-04 | ✅ complete |
| global_11b | 62 | 2412–2415 | 2012-08 – 2026-04 | ✅ complete |

---

## Global-Gruppen (Priorität 3) — ausstehend

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| global_12a | — | 2399–2402 | 2012-08 – 2026-04 | ⬜ pending |
| global_12b | — | 2403–2406 | 2012-08 – 2026-04 | ⬜ pending |
| global_13a | — | 2391–2394 | 2012-08 – 2026-04 | ⬜ pending |
| global_13b | — | 2395–2398 | 2012-08 – 2026-04 | ⬜ pending |
| global_14a | — | 2383–2386 | 2012-08 – 2026-04 | ⬜ pending |
| global_14b | — | 2387–2390 | 2012-08 – 2026-04 | ⬜ pending |
| global_15a | — | 2376–2379 | 2012-08 – 2026-04 | ⬜ pending |
| global_15b | — | 2380–2382 | 2012-08 – 2026-04 | ⬜ pending |
| global_16a | — | 2369–2372 | 2012-08 – 2026-04 | ⬜ pending |
| global_16b | — | 2373–2375 | 2012-08 – 2026-04 | ⬜ pending |
| global_17a | — | 2363–2365 | 2012-08 – 2026-04 | ⬜ pending |
| global_17b | — | 2366–2368 | 2012-08 – 2026-04 | ⬜ pending |
| global_18a | — | 2357–2359 | 2012-08 – 2026-04 | ⬜ pending |
| global_18b | — | 2360–2362 | 2012-08 – 2026-04 | ⬜ pending |
| global_19a | — | 2351–2353 | 2012-08 – 2026-04 | ⬜ pending |
| global_19b | — | 2354–2356 | 2012-08 – 2026-04 | ⬜ pending |
| global_20a | — | 2345–2347 | 2012-08 – 2026-04 | ⬜ pending |
| global_20b | — | 2348–2350 | 2012-08 – 2026-04 | ⬜ pending |

---

## Durchsatz & Restlaufzeit

- Offene Global-Gruppen: 18 × ~70 Spieler × 165 Perioden ≈ **207.000 Combos**
- Global-Scraping ab global_12a via Orchestrator (VPS, ProxyJet)
- Gesamt-Partien in DB: **1.793.298** | Spieler mit Daten: **2.508**
