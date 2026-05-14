# Scraping-Status

Stand: 2026-05-14 22:15 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | ~2.208.000 |
| Spieler mit ok-Daten | 13.402 |
| Global-Gruppen complete | 26 (global_02 – global_16a) |

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
| global_15b | 65 | 2380–2382 | ✅ complete (2026-05-14) |
| global_16a | 95 | 2369–2372 | ✅ complete (2026-05-14) |

---

## Global-Gruppen — ausstehend (Mac Mini, nächste Session)

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| **global_16b** | — | 2373–2375 | ⬜ **als nächstes starten** |
| global_17a–20b | — | 2345–2368 | ⬜ pending |

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| Land | 2026 | 2025 | 2024 |
|------|------|------|------|
| AUT | ✅ fertig | 🔄 läuft | 🔄 Queue |
| SUI | ✅ fertig | 🔄 Queue | 🔄 Queue |
| GER | ✅ fertig | 🔄 Queue | ⬜ später |

- Dashboard: **https://scelo.chesspit.net** (BasicAuth: peter / persönliches PW)
- **Profile: semi_aggressive 60% / normal 30% / semi_conservative 10%** (geändert 2026-05-14)
- Worker läuft dauerhaft (Docker, restart: unless-stopped)
- Gruppen done: 94 | pending: 23.377 | skipped: 1.116 (GER <2000)

---

## Änderungen Session 2026-05-14

| Was | Details |
|-----|---------|
| global_15b ✅ | 65 Spieler, ELO 2380–2382, 5,9h, 0 Errors |
| global_16a ✅ | 95 Spieler, ELO 2369–2372, 8,3h, 1 Error |
| Profil-Gewichte VPS | 20/60/20 → **60/30/10** (semi_aggressive dominiert) |
| Retry-After Header | worker.py liest jetzt FIDEs Retry-After aus 429-Response |
| SRB/2026 failed | Circuit Breaker → manuell auf pending zurückgesetzt |
| Worker-Restart-Bug | ProfileManager liest fuzzy_weights nur beim Start → Restart nötig bei Änderungen |
