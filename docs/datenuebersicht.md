# Datenübersicht

Stand: 18. Mai 2026

---

## 1. Spielergruppen

| Gruppe | Beschreibung | Spieler | Gescrapt |
|---|---|---|---|
| `female_top` | ELO 2400–2600, F | 66 | 2009-01 – 2026-04 |
| `male_control` | ELO 2400–2600, M, age-matched | 649 | 2009-01 – 2026-04 |
| `elite_2600` | ELO ≥ 2600 | 202 | 2009-01 – 2026-04 |
| `swiss_2026` | SMM 2026 NLA+NLB (Boolean-Flag) | 349 exkl. | 2009-01 – 2026-04 |
| `female_2200` | ELO 2200–2399, F | 321 | 2009-01 – 2026-04 |
| `male_2200` | ELO 2200–2399, M, age-matched | 170 | ⛔ skipped |
| `global_02–19b` | Weltweit ELO 2351–2603 | ~2.100 | 2012-08 – 2026-04 ✅ |
| `global_20a` | Weltweit ELO 2345–2347 | 72 | 🔄 läuft |
| `global_20b–28b` | Weltweit ELO 2300–2350 | ~750 | ⬜ pending |
| **Total gescrapt** | | **14.043** | |

> 175 Gruppen total in `groups`-Tabelle definiert. Früheste echte Daten: **2008-04**.
> `swiss_2026` ist ein Boolean-Flag in `players`, kein `analysis_group`-Wert.

---

## 2. Partien-Übersicht

| Kennzahl | Wert |
|---|---|
| **Partien gesamt** | **~2.440.000** |
| **Spieler mit Partiedaten** | **14.043** |
| **Gegner aufgelöst** | **~97,5 %** |
| **Früheste Periode** | **2008-04** |
| **Neueste Periode** | **2026-04** |

> `female_top` spielt 65,4 % ihrer Partien gegen weibliche Gegnerinnen — weit mehr als die
> Männergruppen (< 6 %). Das ist ein direktes Abbild des Frauenturnier-Anteils (37,5 % aller
> `female_top`-Partien sind `women` oder `women_team`).

---

## 3. Datenbankschema

### 3.1 Tabelle `players`
Enthält alle ~1,83 Mio. Spieler aus der FIDE-TXT-Datei (April 2026).

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER PK | FIDE-ID |
| `name` | TEXT | Nachname, Vorname |
| `federation` | CHAR(3) | FIDE-Föderationscode (z.B. `GER`, `CHN`) |
| `title` | TEXT | Offener Titel: `GM`, `IM`, `FM`, `CM` oder NULL |
| `women_title` | TEXT | Frauentitel: `WGM`, `WIM`, `WFM` oder NULL |
| `sex` | CHAR(1) | `M` oder `F` |
| `birth_year` | INTEGER | Geburtsjahr |
| `std_rating` | INTEGER | Standard-Rating (Stand April 2026) |
| `analysis_group` | TEXT | `female_top` \| `male_control` \| `elite_2600` \| `female_2200` \| NULL |
| `swiss_2026` | BOOLEAN | TRUE = Spieler in SMM 2026 |
| `active` | BOOLEAN | FIDE-Aktivitätsstatus (April 2026) |

---

### 3.2 Tabelle `game_results`
Eine Zeile = eine Einzelpartie. Kernquelle aller Analysen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `id` | BIGSERIAL PK | Interne ID |
| `fide_id` | INTEGER FK | Analyse-Spieler |
| `period` | DATE | Ratingperiode (erster des Monats) |
| `game_index` | INTEGER | Laufende Nummer innerhalb (fide_id, period) — UNIQUE-Constraint |
| `opponent_name` | TEXT | Name des Gegners |
| `opponent_fide_id` | INTEGER | FIDE-ID des Gegners (per `resolve_opponents` aufgelöst; ~97,5 % befüllt) |
| `opponent_title` | TEXT | Offener Titel des Gegners |
| `opponent_women_title` | TEXT | Frauentitel des Gegners |
| `opponent_rating` | INTEGER | Rating des Gegners zur Spielzeit |
| `opponent_federation` | CHAR(3) | Föderationscode des Gegners |
| `opponent_sex` | CHAR(1) | `M` oder `F` (98,1 % befüllt) |
| `result` | TEXT | `1` = Sieg, `0.5` = Remis, `0` = Niederlage |
| `rating_change` | NUMERIC(5,2) | Ungewichtete Ratingänderung |
| `rating_change_weighted` | NUMERIC(5,2) | K-Faktor × rating_change |
| `color` | CHAR(1) | `W` = Weiss, `B` = Schwarz |
| `tournament_name` | TEXT | Turniername |
| `tournament_type` | TEXT | `open` \| `women` \| `team` \| `women_team` \| `closed` \| `knockout` |
| `tournament_location` | TEXT | Ort |
| `tournament_start_date` | DATE | Turnierbeginn |
| `tournament_end_date` | DATE | Turnierended |
| `expected_score` | NUMERIC | Elo-Erwartungswert |
| `over_performance` | NUMERIC | result − expected_score |
| `opponent_match_quality` | TEXT | `ok` \| `wide_gap` \| `unresolved` |

---

### 3.3 Tabelle `scrape_periods`
Protokolliert den Scraping-Status pro (Spieler, Periode).

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Erster des Monats |
| `status` | TEXT | `ok` \| `no_data` \| `error` |
| `k_factor` | INTEGER | K-Faktor (10 / 20 / 40) |
| `scraped_at` | TIMESTAMPTZ | Zeitpunkt des Abrufs |
| `http_status` | INTEGER | HTTP-Code bei Fehler |
| `no_data_reason` | TEXT | `system_gap` \| `too_young` \| `inactive` |

---

### 3.4 Tabelle `rating_history`
Monatliches Rating pro Spieler aus zwei unabhängigen Quellen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Erster des Monats |
| `std_rating` | INTEGER | Rating aus FIDE-Calculations (`Ro`) |
| `published_rating` | INTEGER | Rating aus FIDE-TXT-Snapshot (Validierung) |
| `num_games` | INTEGER | Anzahl Partien in dieser Periode |

---

### 3.5 Tabelle `rating_corrections`
Bekannte nicht-spielbasierte FIDE-Ratinganpassungen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Wirksam ab |
| `amount` | INTEGER | ELO-Punkte (positiv = Bonus) |
| `corr_type` | TEXT | `fide_one_off` |
| `source` | TEXT | `snapshot_delta` \| `formula` |

> **Befüllt:** FIDE-Einmalkorrektur März 2024 für Spieler mit Rating < 2000. 379.276 Einträge.

---

### 3.6 Views

| View | Beschreibung |
|---|---|
| `v_dynamic_membership` | Dynamische Gruppenzugehörigkeit pro (fide_id, period) basierend auf `published_rating` |

---

## 4. Abgeleitete Kennzahlen

| Kennzahl | Formel |
|---|---|
| `expected_score` | `1 / (1 + 10^((opp_rating − own_rating) / 400))` |
| `over_performance` | `result − expected_score` |
| `avg_opponent_diff` | `AVG(opponent_rating − own_rating)` |
| `normalized_volatility` | `AVG(ABS(rating_change)) / k_factor` |
| `no_data_rate` | `no_data / (ok + no_data)` |
