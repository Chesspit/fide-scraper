# Scraping-Status

Stand: 2026-05-13 20:00 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | ~2.105.000 |
| Global-Gruppen complete | 24 (global_02 – global_15a) |

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

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| global_02–11b | 144–147 | 2412–2603 | ✅ complete |
| global_12a | 63 | 2399–2402 | ✅ complete |
| global_12b | 78 | 2403–2406 | ✅ complete |
| global_13a | 72 | 2391–2394 | ✅ complete |
| global_13b | 69 | 2395–2398 | ✅ complete |
| global_14a | 73 | 2383–2386 | ✅ complete |
| global_14b | 80 | 2387–2390 | ✅ complete (2026-05-13) |
| global_15a | 69 | 2376–2379 | ✅ complete (2026-05-13) |

---

## Global-Gruppen — ausstehend (Mac Mini, nächste Session)

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| **global_15b** | — | 2380–2382 | ⬜ **als nächstes starten** |
| global_16a | — | 2369–2372 | ⬜ pending |
| global_16b | — | 2373–2375 | ⬜ pending |
| global_17a–20b | — | 2345–2368 | ⬜ pending |

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| Land | 2026 | 2025 | 2024 |
|------|------|------|------|
| AUT | ✅ fertig | 🔄 Queue Prio 1–73 | 🔄 Queue Prio 1–73 |
| SUI | ✅ fertig | 🔄 Queue Prio 1–73 | 🔄 Queue Prio 1–73 |
| GER | ✅ fertig | 🔄 Queue Prio 74–93 | ⬜ später |

- Dashboard: **https://scelo.chesspit.net** (BasicAuth: peter / persönliches PW)
- Profile: normal 60% / semi_aggressive 20% / semi_conservative 20%
- Worker läuft dauerhaft (Docker, restart: unless-stopped)
