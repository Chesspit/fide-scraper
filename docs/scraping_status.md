# Scraping-Status

Stand: 2026-05-19 Abend (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **2.590.233** |
| Spieler mit ok-Daten | **14.420** |
| Global-Gruppen complete | **37** (global_02 – global_21b) |

---

## Kern-Gruppen (Priorität 1–2)

| Gruppe | Spieler | ELO-Range | Zeitraum | Status |
|--------|--------:|-----------|----------|--------|
| female_top | 66 | 2400–2600 (F) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_control | 649 | 2400–2600 (M, age-matched) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| elite_2600 | 202 | ≥ 2600 | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| swiss_2026 | 349 | — (SMM 2026) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| female_2200 | 321 | 2200–2399 (F) | 2009-01 – 2026-04 | ⚠️ partial (2008 fehlt) |
| male_2200 | 170 | 2200–2399 (M, age-matched) | — | ⛔ skipped (gestrichen 2026-05-18) |

---

## Global-Gruppen (Mac Mini Backfill) — abgeschlossen

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| global_02–11b | 60–147 | 2412–2603 | ✅ complete |
| global_12a–19b | 57–95 | 2351–2411 | ✅ complete |

---

## Global-Gruppen — laufend / ausstehend (Mac Mini)

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| global_20a | 72 | 2345–2347 | ✅ complete (2026-05-18) |
| global_20b | 72 | 2348–2350 | ✅ complete (2026-05-18) |
| global_21a | 78 | 2342–2344 | ✅ complete (2026-05-19, 6h 54min) |
| global_21b | 73 | 2339–2341 | ✅ complete (2026-05-19, 5h 02min) |
| global_22a | 56 | 2337–2338 | ⬜ **NÄCHSTE** (geseeded, bereit zum Start) |
| global_21b | — | 2339–2341 | ⬜ pending |
| global_22a–28b | — | 2300–2338 | ⬜ pending (14 Gruppen, neu 2026-05-18) |

Nach global_28b ist ELO ≥ 2300 weltweit vollständig abgedeckt (~815 weitere Spieler).

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| Land | Status |
|------|--------|
| SUI, AUT, GER, alle anderen | 🔄 läuft (aktuell Mai–Apr 2026) |

- Dashboard: **https://scelo.chesspit.net** (BasicAuth: peter / persönliches PW)
- **Profile: semi_aggressive 60% / normal 40%** (geändert 2026-05-18, semi_conservative auf 0%)
- Worker läuft dauerhaft als Python-Prozess auf VPS (root)

---

## Änderungen Session 2026-05-19

| Was | Details |
|-----|---------|
| global_21a ✅ | 78 Spieler, ELO 2342–2344, 6h 54min, 75 Errors |
| global_21b ✅ | 73 Spieler, ELO 2339–2341, 5h 02min, 0 Errors |
| global_22a | 56 Spieler geseeded, bereit zum Start |
| Pre-Filter | Implementiert + deployed (Mac Mini + VPS); ~55% skip-Rate |
| num_games | 165 TXT-Snapshots importiert (2012-08 bis 2026-04) |
| VPS Queue | 104 Gruppen (Prio ≤129) zufällig gemischt |
| Dashboard | Infobar letzte Gruppe, Partien/Spieler, Größe (MB) |

---

## Änderungen Session 2026-05-18

| Was | Details |
|-----|---------|
| global_20a ✅ | 72 Spieler, ELO 2345–2347, 5h 51min, 0 Errors |
| global_20b ✅ | 72 Spieler, ELO 2348–2350, 5h 54min, 1 Error |
| global_21a | 78 Spieler geseeded, bereit zum Start |
| male_2200 ⛔ | Gruppe aus Pipeline gestrichen, DB-Status auf `skipped` gesetzt |
| global_21a–28b | 16 neue Gruppen angelegt (ELO 2300–2344, pending) |
| female_2000 + female_1800 | In DB angelegt (1.068 + 2.884 Spielerinnen, pending) |
| Profil-Gewichte | semi_conservative 0%, normal 40% (war 30%) |
| VPS Queue | GER 2024/2025 in 9er-Blöcke mit SUI/AUT gemischt |
| Dashboard | Queue-Seite auf LIMIT 500 begrenzt (war unbegrenzt → Browser-Absturz) |

---

## Änderungen Session 2026-05-17

| Was | Details |
|-----|---------|
| global_19a ✅ | 71 Spieler, ELO 2351–2353, 5h 55min, 0 Errors |
| global_19b ✅ | 75 Spieler, ELO 2354–2356, 6h 24min, 0 Errors |
| Dashboard Fix | Spieler-Steckbrief: Dropdown-Value wurde nach Auswahl gelöscht → `no_update` Fix |

---

## Änderungen Session 2026-05-16

| Was | Details |
|-----|---------|
| global_18a ✅ | 57 Spieler, ELO 2357–2359 — VPS hatte bereits alles, sofort complete |
| global_18b ✅ | 75 Spieler, ELO 2360–2362, ~4h Laufzeit |

---

## Änderungen Session 2026-05-14

| Was | Details |
|-----|---------|
| global_15b ✅ | 65 Spieler, ELO 2380–2382, 5,9h, 0 Errors |
| global_16a ✅ | 95 Spieler, ELO 2369–2372, 8,3h, 1 Error |
| Profil-Gewichte VPS | 20/60/20 → 60/30/10 (semi_aggressive dominiert) |
| Retry-After Header | worker.py liest jetzt FIDEs Retry-After aus 429-Response |
