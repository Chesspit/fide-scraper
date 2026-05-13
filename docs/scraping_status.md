# Scraping-Status

Stand: 2026-05-13 19:30 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | 2.105.418 |
| Perioden OK | ~253.000 |
| Spieler gescrapt | ~14.700 |

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

---

## Global-Gruppen (Mac Mini Backfill) — abgeschlossen

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
| global_12a | 63 | 2399–2402 | 2012-08 – 2026-04 | ✅ complete |
| global_12b | 78 | 2403–2406 | 2012-08 – 2026-04 | ✅ complete |
| global_13a | 72 | 2391–2394 | 2012-08 – 2026-04 | ✅ complete |
| global_13b | 69 | 2395–2398 | 2012-08 – 2026-04 | ✅ complete |
| global_14a | 73 | 2383–2386 | 2012-08 – 2026-04 | ✅ complete |
| global_14b | 80 | 2387–2390 | 2012-08 – 2026-04 | ✅ complete (2026-05-13) |
| global_15a | 69 | 2376–2379 | 2012-08 – 2026-04 | 🔄 läuft (~84%, ETA 20:30) |

---

## Global-Gruppen — ausstehend (Mac Mini)

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| global_15b | — | 2380–2382 | ⬜ pending |
| global_16a | — | 2369–2372 | ⬜ pending |
| global_16b | — | 2373–2375 | ⬜ pending |
| global_17a–20b | — | 2345–2368 | ⬜ pending |

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| Land | 2026 | 2025 | Queue-Priorität |
|------|------|------|----------------|
| AUT | ✅ fertig | 🔄 Prio 1–105 | 1 |
| SUI | ✅ fertig | 🔄 Prio 1–105 | 1 |
| GER | ⚠️ 14/82 Gruppen | 🔄 Prio 106–187 | 2 |

- Queue gesamt: ~24.500 pending, 73 done
- Worker läuft dauerhaft auf VPS (Docker, restart: unless-stopped)
- Dashboard: **https://scelo.chesspit.net** (BasicAuth: peter / persönliches PW)
- Profile: semi_aggressive 60% / normal 20% / semi_conservative 20%

---

## Durchsatz

| Scraper | Rate |
|---------|------|
| Mac Mini (lokal) | ~27–30 Combos/Min |
| VPS Orchestrator | ~11–15 Combos/Min via ProxyJet |
