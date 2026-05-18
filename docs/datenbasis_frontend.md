# Datenbasis für Frontend-Entwicklung

Stand: 2026-05-18

---

## Überblick

Die Datenbank läuft als **TimescaleDB** (PostgreSQL 16) auf einem Hostinger VPS.
Verbindung über SSH-Tunnel: `scripts/tunnel.sh` → `localhost:5434`

| Kennzahl | Wert |
|----------|-----:|
| Gesamtpartien | ~2.440.000 |
| Davon mit aufgelöstem Gegner | ~97,5 % |
| Spieler mit Partiedaten | 14.043 |
| Gesamtspieler in DB | ~1.832.000 |
| Gescrapete Perioden | 2008-04 – 2026-04 |

---

## Tabellen

### `players` — Spielerstammdaten

| Spalte | Typ | Inhalt |
|--------|-----|--------|
| `fide_id` | INTEGER PK | FIDE-ID |
| `name` | TEXT | Vollständiger Name |
| `federation` | CHAR(3) | z.B. `SUI`, `GER`, `CHN` |
| `sex` | CHAR(1) | `M` / `F` |
| `title` | TEXT | `GM`, `IM`, `FM`, `CM` oder NULL |
| `women_title` | TEXT | `WGM`, `WIM`, `WFM` oder NULL |
| `birth_year` | INTEGER | Geburtsjahr |
| `std_rating` | INTEGER | Aktuelles Standard-ELO (April 2026) |
| `analysis_group` | TEXT | Gruppenname oder NULL |
| `swiss_2026` | BOOLEAN | Spieler der SMM 2026 |
| `active` | BOOLEAN | Laut FIDE aktiv |

**Analysegruppen:**

| Gruppe | Spieler | Beschreibung |
|--------|--------:|--------------|
| `female_top` | 66 | Frauen ELO 2400–2600 |
| `female_2200` | 321 | Frauen ELO 2200–2399 |
| `male_control` | 649 | Männer ELO 2400–2600, age-matched |
| `elite_2600` | 202 | Alle Spieler ELO ≥ 2600 |
| `swiss_2026` | 349 | SMM 2026, als Boolean-Flag |
| `global_02–19b` | ~2.100 | Weltweit ELO 2351–2603 (33 complete) |
| `global_20a+` | 72+ | Weltweit ELO 2300–2350 (laufend/pending) |

> **Wichtig:** Immer nach `p.active = TRUE` filtern (21 inaktive female_top, 44 inaktive male_control).

---

### `game_results` — Einzelpartien

| Spalte | Typ | Inhalt |
|--------|-----|--------|
| `fide_id` | INTEGER | Spieler |
| `period` | DATE | Ratingperiode (immer 1. des Monats) |
| `opponent_name` | TEXT | Name des Gegners |
| `opponent_fide_id` | INTEGER | FIDE-ID des Gegners (~97,5% aufgelöst) |
| `opponent_rating` | INTEGER | ELO des Gegners zum Spielzeitpunkt |
| `opponent_federation` | CHAR(3) | Verband des Gegners |
| `opponent_sex` | CHAR(1) | `M` / `F` (98,1% befüllt) |
| `result` | TEXT | `1` / `0.5` / `0` |
| `rating_change` | NUMERIC | Ungewichtete Ratingänderung |
| `rating_change_weighted` | NUMERIC | K × rating_change |
| `color` | CHAR(1) | `W` (Weiss) / `B` (Schwarz) |
| `tournament_name` | TEXT | Turnierbezeichnung |
| `tournament_location` | TEXT | Ort |
| `tournament_start_date` | DATE | Turnierbeginn |
| `tournament_end_date` | DATE | Turnierende |
| `tournament_type` | TEXT | `open` / `women` / `team` / `women_team` / `closed` / `knockout` |
| `expected_score` | NUMERIC | Elo-Erwartungswert: 1/(1+10^((opp−own)/400)) |
| `over_performance` | NUMERIC | result − expected_score |
| `opponent_match_quality` | TEXT | `ok` / `wide_gap` / `unresolved` |
| `game_index` | INTEGER | Laufende Nummer innerhalb (fide_id, period) |

---

### `rating_history` — Monatliches Rating

| Spalte | Typ | Inhalt |
|--------|-----|--------|
| `fide_id` | INTEGER | Spieler |
| `period` | DATE | Ratingperiode |
| `std_rating` | INTEGER | Rating aus Calculations-Seite (Ro) |
| `published_rating` | INTEGER | Rating aus FIDE-TXT-Snapshot (Validierung) |
| `num_games` | INTEGER | Partien in dieser Periode |

---

### `scrape_periods` — Scraping-Status

| Spalte | Typ | Inhalt |
|--------|-----|--------|
| `fide_id` | INTEGER | Spieler |
| `period` | DATE | Periode |
| `status` | TEXT | `ok` / `no_data` / `error` |
| `k_factor` | INTEGER | K-Faktor (10 / 20 / 40) |
| `scraped_at` | TIMESTAMPTZ | Zeitpunkt des Scrapings |
| `no_data_reason` | TEXT | `system_gap` / `too_young` / `inactive` |

---

## Zeitliche Abdeckung

| Zeitraum | Abdeckung |
|----------|-----------|
| 2008-04 – 2026-04 | ✅ Alle Analysegruppen + global-Gruppen |
| 2008-01 – 2008-03 | ⚠️ Kern-Gruppen fehlen (3 Quartalsperioden) |

**Früheste Periode mit Daten:** 2008-04
**Neueste Periode:** 2026-04

---

## Nützliche Abfragen für das Frontend

### Rating-Verlauf eines Spielers
```sql
SELECT period, std_rating
FROM rating_history
WHERE fide_id = 2805677
ORDER BY period;
```

### Durchschnittliche Gegner-Stärke nach Gruppe und Jahr
```sql
SELECT p.analysis_group,
       EXTRACT(YEAR FROM gr.period) AS year,
       AVG(gr.opponent_rating) AS avg_opp_rating,
       AVG(gr.opponent_rating - rh.std_rating) AS avg_opp_diff
FROM game_results gr
JOIN players p USING (fide_id)
JOIN rating_history rh ON rh.fide_id = gr.fide_id AND rh.period = gr.period
WHERE p.analysis_group IS NOT NULL AND p.active = TRUE
GROUP BY 1, 2 ORDER BY 1, 2;
```

### Partien nach Gegner-Geschlecht (nur female_top)
```sql
SELECT opponent_sex, COUNT(*) AS games,
       AVG(CAST(result AS NUMERIC)) AS win_rate
FROM game_results gr
JOIN players p USING (fide_id)
WHERE p.analysis_group = 'female_top' AND p.active = TRUE
  AND opponent_sex IS NOT NULL
GROUP BY opponent_sex;
```

---

## Verbindung (lokal via SSH-Tunnel)

```bash
# Tunnel öffnen:
bash scripts/tunnel.sh

# Verbindungsstring:
DATABASE_URL=postgresql://fide:***@localhost:5434/fidedb
```

---

## Bekannte Einschränkungen

| Punkt | Details |
|-------|---------|
| Gegner-Auflösung | ~2,5% unaufgelöst — hauptsächlich indische Namen mit Schreibvarianten |
| Inaktive Spieler | 21 inaktive female_top, 44 inaktive male_control — immer `WHERE active=TRUE` filtern |
| 2008 Kern-Gruppen | 3 Quartalsperioden (Jan/Apr/Jul 2008) noch nicht gescrapt |
| global_20a+ | ELO 2300–2350 noch in Bearbeitung (laufend/pending) |
