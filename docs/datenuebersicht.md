# Datenübersicht

Stand: 10. Mai 2026

---

## 1. Spielergruppen

| Gruppe | Beschreibung | Spieler | Gescrapt |
|---|---|---|---|
| `female_top` | ELO 2400–2600, F | 66 | 2009-01 – 2026-04 |
| `male_control` | ELO 2400–2600, M, age-matched | 649 | 2009-01 – 2026-04 |
| `elite_2600` | ELO ≥ 2600 | 202 | 2009-01 – 2026-04 |
| `swiss_2026` | SMM 2026 NLA+NLB (Boolean-Flag) | 349 exkl. | 2009-01 – 2026-04 |
| `female_2200` | ELO 2200–2399, F | 321 | 2009-01 – 2026-04 |
| `male_2200` | ELO 2200–2399, M, age-matched | 170 | 2013-01 – 2026-04 |
| `global_02–11b` | Weltweit ELO 2407–2603 | 1.331 | 2012-08 – 2026-04 |
| `global_12a` | Weltweit ELO 2399–2402 | 63 | ⏳ läuft (lokal) |
| **Total gescrapt** | | **2.508** | |

> 159 Gruppen total in `groups`-Tabelle definiert. Globale Gruppen ab global_12b via Orchestrator (VPS + ProxyJet).
> Früheste echte Daten: **2008-04** (FIDE hatte vor diesem Datum kein monatliches Rating-System).

> `swiss_2026` ist ein Boolean-Flag in `players`, kein `analysis_group`-Wert.
> 13 Spieler sind gleichzeitig in `swiss_2026` und einer anderen Gruppe.
> 4 Spielerinnen aus `female_2200` haben `swiss_2026 = TRUE`.

---

## 2. Scraping-Abdeckung

| Gruppe | Spieler mit Daten | Perioden (ok) | Perioden (no_data) | Zeitraum |
|---|---|---|---|---|
| `female_top` | 58 / 66 | 4.415 | 8.064 | 2010-01 – 2026-03 |
| `male_control` | 470 / 479 | 37.046 | 56.358 | 2010-01 – 2026-03 |
| `elite_2600` | 197 / 202 | 17.668 | 21.722 | 2010-01 – 2026-03 |
| `swiss_2026` | 345 / 349 | 25.001 | 42.274 | 2010-01 – 2026-03 |
| `female_2200` | 4 / 321 ⏳ | 400 | 380 | 2010-01 – 2026-03 |

- **Beobachtungszeitraum:** 196 Monate (2010-01-01 bis 2026-03-01)
- **`no_data`** = Spieler hatte in diesem Monat keine FIDE-gewerteten Partien
- **Fehlerrate:** 0 Errors in allen Gruppen
- `female_2200`: ✅ Backfill abgeschlossen 2026-04-26
- **2009**: Backfill läuft ⏳ (~16.956 Perioden, ETA heute Abend)

---

## 3. Partien-Übersicht

| Gruppe | Partien | Gegner aufgelöst |
|---|---|---|
| `female_top` | 50.714 | ~97 % |
| `male_control` | 526.121 | ~98 % |
| `elite_2600` | 202.246 | ~98 % |
| `swiss_2026` | — (Boolean-Flag) | — |
| `female_2200` | 258.696 | ~97 % |
| `male_2200` | ~neu | ~97 % |
| `global_02` | ~neu | ~97 % |
| **Gesamt** | **~1.793.298** | **~97,5 %** |

> `female_top` spielt 65,4 % ihrer Partien gegen weibliche Gegnerinnen — weit mehr als die
> Männergruppen (< 6 %). Das ist ein direktes Abbild des Frauenturnier-Anteils (37,5 % aller
> `female_top`-Partien sind `women` oder `women_team`).

---

## 4. Datenbankschema

### 4.1 Tabelle `players`
Enthält alle ~1,83 Mio. Spieler aus der FIDE-TXT-Datei (April 2026).

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER PK | FIDE-ID |
| `name` | TEXT | Nachname, Vorname |
| `federation` | CHAR(3) | FIDE-Föderationscode (z.B. `GER`, `CHN`) |
| `title` | TEXT | Offener Titel: `GM`, `IM`, `FM`, `CM` oder NULL |
| `women_title` | TEXT | Frauentitel: `WGM`, `WIM`, `WFM` oder NULL |
| `sex` | CHAR(1) | `M` oder `F` — aus TXT-Datei, vollständig für alle 1,83 Mio. |
| `birth_year` | INTEGER | Geburtsjahr |
| `std_rating` | INTEGER | Standard-Rating (Stand April 2026) |
| `analysis_group` | TEXT | `female_top` \| `male_control` \| `elite_2600` \| `female_2200` \| NULL |
| `swiss_2026` | BOOLEAN | TRUE = Spieler in SMM 2026 |
| `active` | BOOLEAN | FIDE-Aktivitätsstatus (April 2026) |
| `created_at` | TIMESTAMPTZ | Zeitpunkt des Imports |
| `updated_at` | TIMESTAMPTZ | Letzte Aktualisierung |

---

### 4.2 Tabelle `game_results`
Eine Zeile = eine Einzelpartie. Kernquelle aller Analysen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `id` | BIGSERIAL PK | Interne ID |
| `fide_id` | INTEGER FK | Analyse-Spieler (Referenz auf `players`) |
| `period` | DATE | Ratingperiode (erster des Monats) |
| `game_index` | INTEGER | Laufende Nummer innerhalb (fide_id, period) — UNIQUE-Constraint |
| `opponent_name` | TEXT | Name des Gegners (direkt gescrapt) |
| `opponent_fide_id` | INTEGER | FIDE-ID des Gegners (per `resolve_opponents` aufgelöst; 97,7 % befüllt) |
| `opponent_title` | TEXT | Offener Titel des Gegners: `g`=GM, `m`=IM, `f`=FM, `c`=CM |
| `opponent_women_title` | TEXT | Frauentitel des Gegners: `wg`=WGM, `wm`=WIM, `wf`=WFM |
| `opponent_rating` | INTEGER | Rating des Gegners zur Spielzeit |
| `opponent_federation` | CHAR(3) | Förderationscode des Gegners |
| `opponent_sex` | CHAR(1) | Geschlecht des Gegners: `M` oder `F` (98,1 % befüllt) |
| `result` | TEXT | `1` = Sieg, `0.5` = Remis, `0` = Niederlage |
| `rating_change` | NUMERIC(5,2) | Ungewichtete Ratingänderung (Rohwert) |
| `rating_change_weighted` | NUMERIC(5,2) | K-Faktor × rating_change (tatsächliche Punktänderung) |
| `color` | CHAR(1) | `W` = Weiss, `B` = Schwarz |
| `tournament_name` | TEXT | Turniername (direkt gescrapt) |
| `tournament_type` | TEXT | `open` \| `women` \| `team` \| `women_team` (regelbasiert klassifiziert) |
| `tournament_location` | TEXT | Ort (z.B. `Moscow RUS`) |
| `tournament_start_date` | DATE | Turnierbeginn |
| `tournament_end_date` | DATE | Turnierended |
| `created_at` | TIMESTAMPTZ | Zeitpunkt des Imports |

**Turniertyp-Klassifikation (`tournament_type`):**

| Wert | Anteil | Beschreibung |
|---|---|---|
| `open` | 84,5 % | Einzelturnier ohne Geschlechts- oder Teamrestriktion |
| `team` | 11,8 % | Mannschaftswettkämpfe (Olympiade, Bundesliga, Club Cup) |
| `women` | 2,8 % | Frauen-Einzelturnier (z.B. Europameisterschaft Frauen) |
| `women_team` | 1,0 % | Frauen-Mannschaft (z.B. Frauen-Olympiade, Frauen-Bundesliga) |

---

### 4.3 Tabelle `scrape_periods`
Protokolliert den Scraping-Status pro (Spieler, Periode).

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Erster des Monats (z.B. `2025-01-01`) |
| `status` | TEXT | `ok` = Daten vorhanden, `no_data` = keine Partien, `error` = Fehler |
| `k_factor` | INTEGER | K-Faktor (10 / 20 / 40) dieser Periode |
| `scraped_at` | TIMESTAMPTZ | Zeitpunkt des Abrufs |
| `http_status` | INTEGER | HTTP-Code bei Fehler (429 = rate limited, 403 = blocked) |

---

### 4.4 Tabelle `rating_history`
Monatliches Rating pro Spieler aus zwei unabhängigen Quellen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Erster des Monats |
| `std_rating` | INTEGER | Rating aus FIDE-Calculations (`Ro` = Startwert der Periode) |
| `published_rating` | INTEGER | Rating aus historischem FIDE-TXT-Snapshot (Validierungsquelle) |
| `num_games` | INTEGER | Anzahl Partien in dieser Periode (aus Calculations) |

> `std_rating` und `published_rating` sollten identisch sein. Abweichungen > ±5 weisen auf
> Parser-Fehler oder FIDE-Nachkorrekturen hin. Beide Quellen decken alle 1,83 Mio. Spieler ab.
>
> **TXT-Snapshot-Coverage:** 195 Perioden in `data/` — Jan 2006 bis Apr 2026.
> Quartalsweise 2006–2008, zunehmend häufiger bis vollständig monatlich ab 2013.

---

### 4.5 Tabelle `rating_corrections`
Bekannte nicht-spielbasierte FIDE-Ratinganpassungen.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period` | DATE PK | Periode, in der die Korrektur wirksam wurde |
| `amount` | INTEGER | ELO-Punkte (positiv = Bonus) |
| `corr_type` | TEXT | Art der Korrektur (aktuell: `fide_one_off`) |
| `source` | TEXT | `snapshot_delta` (exakt berechnet) oder `formula` (Näherung) |

> **Aktuell befüllt:** FIDE-Einmalkorrektur März 2024 für alle Spieler mit Rating < 2000.
> Formel: `+0,4 × (2000 − Post-Game-Rating)`. 379.276 Einträge.

---

### 4.6 Tabelle `qc_rating_check`
Qualitätskontrolle: Vergleich erwartete vs. gescrapte Ratingänderung pro Zeitfenster.

| Attribut | Typ | Beschreibung |
|---|---|---|
| `fide_id` | INTEGER FK PK | Spieler-ID |
| `period_start` | DATE PK | Startperiode des Fensters (TXT-Snapshot) |
| `period_end` | DATE PK | Endperiode des Fensters (TXT-Snapshot) |
| `published_start` | INTEGER | `published_rating` zu Beginn des Fensters |
| `published_end` | INTEGER | `published_rating` am Ende des Fensters |
| `expected_change` | NUMERIC | `published_end − published_start` |
| `scraped_change` | NUMERIC | `SUM(rating_change_weighted)` für Perioden im Fenster |
| `delta` | NUMERIC | `expected_change − scraped_change` (Rohdifferenz) |
| `correction` | NUMERIC | Summe bekannter FIDE-Korrekturen im Fenster |
| `missing_periods` | INTEGER | Anzahl Monate ohne Scrape-Daten im Fenster |
| `flag` | TEXT | `ok` (\|Δ−corr\| ≤ 5) \| `warn` (≤ 15) \| `error` (> 15) |
| `checked_at` | TIMESTAMPTZ | Zeitpunkt des QC-Laufs |

> **Aktueller Stand:** 242.028 Fenster — OK 96,6 % | Warn 1,7 % | Error 1,7 %
> (2010–2026: 99,8–100 % OK; 2006–2009: 44–60 % — kein Scraping für diese Jahre)

---

## 5. Abgeleitete Felder und Views

### SQL-Views (in `migrations/002_analysis_views.sql`)

| View | Basiert auf | Beschreibung |
|---|---|---|
| `v_opponent_strength` | `game_results`, `players`, `rating_history` | Ø Gegner-Rating vs. eigenes Rating pro (Spieler, Periode) |
| `v_rating_volatility` | `game_results`, `players`, `scrape_periods` | Mittlere absolute Ratingänderung, normalisiert nach K-Faktor |
| `v_tournament_frequency` | `game_results`, `players` | Partien und Turniere pro (Spieler, Periode) |
| `v_rating_progression` | `rating_history`, `players` | Ratingverlauf mit Delta zum Startwert |

### Abgeleitete Kennzahlen (berechenbar per SQL)

| Kennzahl | Formel | Verwendung |
|---|---|---|
| `expected_score` | `1 / (1 + 10^((opp_rating − own_rating) / 400))` | Elo-erwartetes Ergebnis |
| `over_performance` | `result − expected_score` | Tatsächlich vs. erwartet |
| `avg_opponent_diff` | `AVG(opponent_rating − own_rating)` | Gegner-Stärke relativ zum eigenen Rating |
| `normalized_volatility` | `AVG(ABS(rating_change)) / k_factor` | K-Faktor-bereinigte Volatilität |
| `no_data_rate` | `no_data / (ok + no_data)` | Inaktivitätsquote pro Spieler/Periode |
