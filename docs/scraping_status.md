# Scraping-Status

Stand: 2026-05-21 (Quelle: `groups`-Tabelle DB + Orchestrator SQLite)

---

## Gesamtstand DB

| Kennzahl | Wert |
|----------|------|
| Partien gesamt | **2.783.269** |
| Spieler mit ok-Daten | **15.208** |
| Global-Gruppen complete | **41** (global_02 – global_23b) |

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

| Gruppen | ELO-Range | Spieler | Status |
|---------|-----------|--------:|--------|
| global_02–11b (12 Gruppen) | 2412–2603 | 60–147 | ✅ complete |
| global_12a–19b (16 Gruppen) | 2351–2411 | 57–95 | ✅ complete |
| global_20a | 2345–2347 | 72 | ✅ complete (2026-05-18) |
| global_20b | 2348–2350 | 72 | ✅ complete (2026-05-18) |
| global_21a | 2342–2344 | 78 | ✅ complete (2026-05-19) |
| global_21b | 2339–2341 | 73 | ✅ complete (2026-05-19) |
| global_22a | 2337–2338 | 56 | ✅ complete (2026-05-21) |
| global_22b | 2334–2336 | 70 | ✅ complete (2026-05-21) |
| global_23a | 2331–2333 | 71 | ✅ complete (2026-05-21) |
| global_23b | 2328–2330 | 77 | ✅ complete (2026-05-21, ~5h) |

---

## Global-Gruppen — laufend / ausstehend (Mac Mini)

| Gruppe | Spieler | ELO-Range | Status |
|--------|--------:|-----------|--------|
| global_24a | 90 | 2325–2327 | 🔄 läuft (seit 13:33 Uhr, ~6h ETA) |
| global_24b | — | 2322–2324 | ⬜ pending (noch nicht geseeded) |
| global_25a – global_28b (8 Gruppen) | — | 2300–2321 | ⬜ pending |

Nach global_28b ist ELO ≥ 2300 weltweit vollständig abgedeckt.

---

## Orchestrator (VPS) — föderationsbasiertes Scraping

| | |
|---|---|
| Dashboard | **https://scelo.chesspit.net** (BasicAuth) |
| Modus | **Parallel: 2 Threads** (T0=semi_aggressive, T1=normal) |
| Aktuell | GER 2024/2025-Gruppen (~2000–2200 ELO) |

- Worker läuft als Docker-Container (restart: unless-stopped)
- Thread-Profile: T0 semi_aggressive / T1 normal / T2 semi_conservative (bereit) / T3 semi_aggressive (bereit)
- Sonntagserinnerung gesetzt: ggf. auf 3 Threads hochschalten (2026-05-24 09:00)

---

## Änderungen Session 2026-05-21

| Was | Details |
|-----|---------|
| global_22a ✅ | 56 Spieler, ELO 2337–2338, bereits gescrapt (Status nachgezogen) |
| global_22b ✅ | 70 Spieler, ELO 2334–2336, bereits gescrapt (Status nachgezogen) |
| global_23a ✅ | 71 Spieler, ELO 2331–2333, bereits gescrapt (Status nachgezogen) |
| global_23b ✅ | 77 Spieler, ELO 2328–2330, 4.981/4.982 erfolgreich, ~5h |
| global_24a 🔄 | 90 Spieler geseeded, Backfill gestartet 13:33 Uhr |
| **Parallel-Modus** | VPS-Worker läuft jetzt mit 2 Threads gleichzeitig |
| Thread-Profile | T0=semi_aggressive, T1=normal, T2=semi_conservative, T3=semi_aggressive |
| Threads-Dropdown | Dashboard: 1×–4× einstellbar, persistiert in profiles.yaml |
| 🔄 Neustart-Button | Worker exitiert sauber, Docker startet mit neuer Config neu |
| Thread-Spalte Queue | Ersetzt Profil-Spalte; zeigt T0/T1 für laufende Gruppen (blau) |
| Status-Anzeige | Pro Thread ein Block mit Badge + Fortschritt (kein MB mehr) |
| ProxyJet | threading.Lock → thread-sicher für parallele Requests |
| docker-compose | profiles.yaml für Dashboard-Container schreibbar (war :ro) |
| Push auf GitHub | Commits e69a844–3a36dae gepusht |

---

## Änderungen Session 2026-05-19

| Was | Details |
|-----|---------|
| global_21a ✅ | 78 Spieler, ELO 2342–2344, 6h 54min, 75 Errors |
| global_21b ✅ | 73 Spieler, ELO 2339–2341, 5h 02min, 0 Errors |
| global_22a | 56 Spieler geseeded |
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
| global_21a | 78 Spieler geseeded |
| male_2200 ⛔ | Gruppe aus Pipeline gestrichen |
| global_21a–28b | 16 neue Gruppen angelegt (ELO 2300–2344) |
| Profil-Gewichte | semi_conservative 0%, normal 40% |

---

## Ältere Änderungen

→ Sessions 2026-05-13 bis 2026-05-17: global_14b – global_19b abgeschlossen,
   Dashboard-Verbesserungen, Retry-After-Fix, VPS-Profil-Umstellung.
