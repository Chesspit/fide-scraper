# Ideen für Verbesserungen

Stand: 25. April 2026

---

## A — Analytische Erweiterungen

### A1. Open vs. Frauenturnier trennen *(hohe Priorität)*

Das ist die wichtigste blinde Stelle der aktuellen Analyse. Spielerinnen, die
hauptsächlich Frauenturniere bestreiten, haben strukturell andere Gegner als solche,
die im Open-Bereich spielen. `tournament_name` enthält diese Information — ein Filter
auf Begriffe wie „Women", „Girl", „Ladies", „Female" würde die Partien trennen.
Ohne diese Unterscheidung misst man nicht wirklich Gleichberechtigung, sondern
Turnierwahl.

### A2. Performance vs. Erwartung *(hohe Priorität)*

Statt nur die Gegner-Stärke zu messen: War das Ergebnis besser oder schlechter als
die Elo-Formel vorhersagt?

```
expected_score = 1 / (1 + 10^((opp_rating - own_rating) / 400))
over_performance = actual_result - expected_score
```

Die Differenz pro Partie ist ein fairerer Leistungsindikator als Rohpunkte. Tritt
bei Frauen mehr Über- oder Unterperformance auf?

### A3. Karriere-Persistenz

Wie lange bleiben Spieler in der 2400–2600-Range? Wann verlassen sie sie — durch
Aufstieg oder Abstieg? Aus `rating_history` direkt berechenbar. Zeigt, ob die
Gruppe bei einer der Geschlechtergruppen strukturell instabiler ist.

### A4. Regionale Unterschiede

Asiatische Spielerinnen dominieren `female_top` (China, Indien, Kasachstan).
Turniersysteme und Gegnerfelder in Asien sind strukturell anders als in Europa.
Eine Clusteranalyse nach `federation` zeigt, ob die Muster global konsistent sind
oder regional getrieben werden.

### A5. Temporale Trends

Über den Beobachtungszeitraum 2010–2026: Schliessen sich die Lücken zwischen den
Gruppen? Ein Rolling-Window-Vergleich würde zeigen, ob strukturelle Unterschiede
zu- oder abnehmen.

### A6. Regressionsmodell

Die Notebook-Analysen sind deskriptiv. Ein lineares Modell

```
avg_opponent_diff ~ sex + own_rating + birth_year + period + k_factor
```

würde den Geschlechtereffekt *unter Kontrolle* der anderen Variablen isolieren —
der eigentliche statistische Test für die Kernthese.

---

## B — Datenseitige Verbesserungen

### B1. Gegner-Geschlecht materialisieren *(klein, hoher Gewinn)*

Das Gegner-Geschlecht ist bereits zu 98,1 % bestimmbar — kein fehlendes Datum,
sondern ein JOIN-Aufwand:
- 97,7 % via `opponent_fide_id → players.sex` (TXT-Datei enthält `SEX` für alle
  1,83 Mio. Spieler)
- 0,3 % zusätzlich via `opponent_women_title IS NOT NULL` (Frauentitel im
  gescrapten HTML)
- Nur 1,9 % wirklich unbekannt (ungelöst + kein Frauentitel, überwiegend Männer)

Eine materialisierte Spalte `opponent_sex` in `game_results` würde Notebook 07
und alle Folgeanalysen deutlich vereinfachen — statt JOIN bei jeder Abfrage.

### B2. Wide-gap-Matches flaggen *(klein, Datenqualität)*

Der Resolver hat Matches mit `rating_diff > 200` als „suspicious" markiert. Diese
sind in `opponent_fide_id` eingetragen, aber möglicherweise falsch zugeordnet
(Namens-Kollision). Für Analysen auf Gegner-Eigenschaften (Geschlecht, Titel) sollte
man diese mit einer `diff > 200`-Maske kennzeichnen oder ausschliessen.

Identifikations-Query:
```sql
SELECT gr.opponent_name, gr.opponent_federation,
       gr.opponent_rating, p.std_rating,
       ABS(gr.opponent_rating - p.std_rating) AS diff
FROM game_results gr
JOIN players p ON p.fide_id = gr.opponent_fide_id
WHERE ABS(gr.opponent_rating - p.std_rating) > 200
ORDER BY diff DESC;
```

### B3. No-data-Ursachen unterscheiden *(mittel, methodisch wichtig)*

`status='no_data'` in `scrape_periods` hat aktuell **drei unterschiedliche Bedeutungen**,
die nicht unterschieden werden:

| Ursache | Beispiel | Analytisch |
|---|---|---|
| **Inaktiv** | Spieler hat diesen Monat nicht gespielt | bewusste Wahl, aussagekräftig |
| **Noch nicht registriert** | Spielerin geb. 2008, hat 2010 keine FIDE-ID | strukturell, kein Aussagewert |
| **System-Limitation** | Feb 2009 existiert nicht (bi-monatlich) | technisch, kein Aussagewert |

**Problem:** Wird `no_data` als Proxy für Inaktivität oder Turnierfrequenz genutzt
(z.B. in NB03), vermischen sich diese drei Gruppen. Eine junge Spielerin mit
`no_data` in 2010 ist nicht „inaktiv" — sie existierte schlicht noch nicht im System.

**FIDE-Rhythmus-Historie:**
- Vor ca. 2009-07: bi-monatliche Listen (Jan, Mär, Mai, Jul, Sep, Nov)
- 2009-07 bis ca. 2012: monatlich, aber lückenhaft
- Ab 2013: vollständig monatlich

**Lösungsvorschlag:** Neue Spalte `no_data_reason` in `scrape_periods` oder
eine View die die Ursache ableitet:

```sql
-- Ableitung der no_data-Ursache
SELECT sp.fide_id, sp.period,
    CASE
        -- System: Periode existiert strukturell nicht (vor monatlichem FIDE-System)
        WHEN sp.period < '2009-07-01'
         AND EXTRACT(MONTH FROM sp.period) NOT IN (1,3,5,7,9,11)
        THEN 'system_gap'

        -- Noch nicht registriert: Spieler war in dieser Periode noch sehr jung
        WHEN p.birth_year IS NOT NULL
         AND EXTRACT(YEAR FROM sp.period) - p.birth_year < 10
        THEN 'too_young'

        -- Kein TXT-Snapshot für diese Periode vorhanden
        WHEN NOT EXISTS (
            SELECT 1 FROM rating_history rh
            WHERE rh.fide_id = sp.fide_id AND rh.period = sp.period
              AND rh.published_rating IS NOT NULL
        )
        THEN 'no_snapshot'

        -- Echter no_data: Spieler existierte, hat aber nicht gespielt
        ELSE 'inactive'
    END AS reason
FROM scrape_periods sp
JOIN players p USING(fide_id)
WHERE sp.status = 'no_data';
```

Damit wird `no_data_reason = 'inactive'` zum echten Indikator für Turnierfrequenz.
Die Rate pro Gruppe und Zeitraum ist besonders relevant für den COVID-Einbruch 2020
und den Vergleich female_top vs. male_control.

### B4. Dynamische Gruppenzugehörigkeit *(gross, methodisch wichtig — jetzt direkt umsetzbar)*

Die aktuelle Logik friert den ELO-Stand April 2026 ein. Ein Spieler mit heutigem
Rating 2420 ist in `male_control`, war aber 2012 vielleicht bei 1900. Sauberer wäre
eine Periode-für-Periode-Zuordnung:

```sql
-- War dieser Spieler in dieser Periode zwischen 2400 und 2600?
SELECT fide_id, period
FROM rating_history
WHERE published_rating BETWEEN 2400 AND 2600
```

**Stand 2026-04-25:** Mit 164 monatlichen Snapshots (Sep 2012 – Apr 2026, ab 2026 bis 2006
erweitert) ist die Datenbasis vollständig vorhanden. Kein neues Scraping nötig —
nur ein Schema-Umbau.

### B5. TXT-Snapshots *(teilweise erledigt)*

- ✅ **Sep 2012 – Apr 2026: vollständig monatlich** (164 Dateien, importiert 2026-04-25)
- ⏳ **Jan–Aug 2012:** Dateien vorhanden, Format-Test ausstehend
- ⏳ **2006–2011:** Dateien vorhanden (schrittweise Format-Prüfung), ab 2026 importierbar

### B6. Turnierkategorie nachscrapen *(gross, zentral)*

Die FIDE-Datenbank klassifiziert Turniere nach Kategorie (I–XXI) und Format
(Round Robin / Swiss / Team). Die Event-IDs sind bereits in den gescrapten Daten
enthalten (aus dem HTML-Link `<a href=/report.phtml?event=406623>`). Ein separater
Scraper könnte Turnierformat, Kategorie und Teilnehmerzahl nachladen und in einer
neuen Tabelle `tournaments` speichern. Das würde den Open/Frauenturnier-Split
(A1) und die Formatanalyse (A4) erst richtig ermöglichen.

### B7. Online vs. OTB klassifizieren *(mittel)*

Seit 2020 gibt es viele Online-Turniere mit anderer Paarungs- und Ratingdynamik.
Eine regelbasierte Klassifikation auf `tournament_name` (Keywords: „Online",
„Titled Tuesday", „Lichess", „Chess.com") würde den COVID-Ausreisser in den Daten
sauber erklären und eine separate Auswertung ermöglichen.

### B8. Survivorship-Bias quantifizieren *(jetzt direkt umsetzbar)*

Alle 1.094 Spieler existieren im April-2026-Snapshot. Spieler, die zwischen 2010
und 2025 die 2400-Grenze temporär überschritten haben, aber 2026 nicht mehr dort
sind, fehlen komplett.

**Stand 2026-04-25:** Mit monatlicher ELO-Historie seit Sep 2012 ist diese Abfrage
direkt möglich — ohne neues Scraping:

```sql
-- Alle Frauen, die jemals ≥ 2400 hatten (historische female_top-Population)
SELECT DISTINCT rh.fide_id, p.name, MAX(rh.published_rating) AS peak_rating
FROM rating_history rh JOIN players p USING (fide_id)
WHERE p.sex = 'F' AND rh.published_rating >= 2400
GROUP BY rh.fide_id, p.name
ORDER BY peak_rating DESC;
```

Ergibt die "wahre" female_top-Population 2012–2026, nicht nur den 2026-Snapshot.

---

## C — Ausgabe und Reichweite

### C1. Wissenschaftliche Publikation

Datensatz, Methodik und Fragestellung sind publikationsreif. Mögliche Zielkanäle:
- *Journal of Sports Sciences*
- *ICGA Journal* (Computerschach/Spielforschung)
- arXiv (cs.SI — Social and Information Networks)

Die FIDE-Calculations-Seite als strukturierte Datenquelle für Längsschnittanalysen
ist bisher wissenschaftlich kaum genutzt worden.

### C2. Interaktives Dashboard

Ein Streamlit-App auf dem VPS würde die Ergebnisse ohne Jupyter zugänglich machen —
nützlich für Feedback von Dritten und als Begleitmedium zu einer Publikation.

---

## D — Erweiterung der weiblichen Population

### D1. Frauen ELO 2200–2400 *(Priorität 1)*

Die natürlichste Erweiterung der Kernfrage. Aktuell ist `female_top` auf 2400–2600
beschränkt — eine relativ willkürliche Grenze. Mit ~120 aktiven Spielerinnen in diesem
Band liesse sich zeigen, ob die beobachteten Muster spezifisch für die Spitze sind
oder über alle Leistungsebenen gelten. Ergänzt durch age-matched `male_control`
für dieselbe ELO-Range.

### D2. Frauen ELO 2000–2200 *(Priorität 2)*

Deutlich grössere Gruppe (~250 Spielerinnen), die den Massenbereich des
Frauenschachs abdeckt. Mehr statistische Power, direkter Vergleich mit der
sub-elite-Ebene.

### D3. Inaktive Spitzenspielerinnen mit historisch hohem Rating *(Priorität 2)*

Judit Polgar (aktiv bis 2014, Peak ~2735), Susan Polgar, Xie Jun, Chiburdanidze —
FIDE-inaktiv, aber zwischen 2010 und 2014 noch gescrapt werden können. Adressiert
den Survivorship-Bias: ohne sie sieht man nur die aktuelle Generation.

### D4. Spielerinnen, die aus dem 2400-Fenster herausgefallen sind *(Priorität 3)*

Spielerinnen, die 2015–2020 bei 2400+ waren, heute aber nicht mehr. Identifizierbar
via `rating_history.published_rating`. Diese fehlen durch Survivorship-Bias — und
könnten genau die sein, bei denen strukturelle Benachteiligung wirksam war.

### D5. Women's Grand Prix Teilnehmerinnen *(Priorität 4)*

Die ~30–40 Spielerinnen des FIDE Women's Grand Prix sind definiert elite.
Grosse Überschneidung mit `female_top`, aber mit explizitem Turniersystem-Kontext.

---

## E — Allgemeine Spielergruppen jenseits der Geschlechterfrage

### E1. Alle aktiven GMs — Altern und Leistungspeak *(Priorität 1)*

**Forschungsfrage:** Wann erreicht ein Spieler sein Rating-Maximum, wie lange hält
es sich, und wie verläuft der Abstieg? Gibt es universelle Kurven oder unterscheiden
sich diese nach Nationalität, Titelklasse, K-Faktor-Geschichte?

~1.800 aktive GMs — die vollständige Profi-Population. Das Elo-System macht den
Leistungsabfall direkt quantifizierbar, für Schach bisher kaum auf
Einzelpartien-Ebene untersucht.

### E2. COVID und der Online-Schock *(Priorität 1)*

**Forschungsfrage:** Wie hat die Verlagerung zu Online-Turnieren 2020–2022
verschiedene Spielergruppen unterschiedlich getroffen? Wer profitierte (jüngere
Spieler?), wer verlor (ältere Spieler? Spieler aus Regionen ohne gute
Internetinfrastruktur)?

Kein neues Scraping nötig für die Hauptanalyse — Zeitreihe auf den bestehenden
696.820 Partien. Erweiterung: Spieler aus afrikanischen Föderationen oder
Zentralasien als Infrastruktur-Kontrollgruppe.

### E3. Alle aktiven IMs — IM als Durchgangsstation oder Endpunkt *(Priorität 2)*

**Forschungsfrage:** Wieviele IMs schaffen den Sprung zum GM, wann im
Karriereverlauf, und was unterscheidet die Erfolgreichen? Gibt es
Gegnerstruktur-Muster, die den Durchbruch vorhersagen?

~4.000 aktive IMs ohne GM-Titel. Klar definierbare Outcome-Variable (GM-Titel
ja/nein), lange Beobachtungsperiode vorhanden. Grösste hier vorgeschlagene
Scraping-Aufgabe.

### E4. Die 2300-Schwelle *(Priorität 2)*

**Forschungsfrage:** Um 2300 stagnieren aussergewöhnlich viele Spieler jahrelang.
Ist das ein strukturelles Phänomen — andere Gegnerfelder, andere Turniertypen,
andere Volatilität — oder reine Selektion?

Zwei Gruppen: Spieler, die seit ≥ 3 Jahren zwischen 2250 und 2350 stehen
(~1.000–2.000 weltweit), und Spieler, die 2300 in denselben Jahren überwunden
haben. Der Übergang Hobby→Profi passiert genau hier.

### E5. Talententwicklung — Jugenddurchbruch *(Priorität 2)*

**Forschungsfrage:** Welche Spieler, die mit 14 Jahren 2000+ hatten, erreichen
später 2500+? Was unterscheidet ihre Spielergebnisse in der Aufstiegsphase?

Alle Spieler, die vor dem 18. Geburtstag 2000+ erreicht haben — identifizierbar
via `birth_year` + `rating_history`. Ergänzt durch eine Stagnations-Kontrollgruppe
(seit 5+ Jahren auf demselben Rating-Level).

### E6. Nationale Schachsysteme im Vergleich *(Priorität 3)*

**Forschungsfrage:** Produzieren Länder mit staatlich geförderten Programmen
(Russland, China, Usbekistan, Aserbaidschan) messbar anders strukturierte
Karriereverläufe als Länder ohne (Schweiz, Deutschland, USA)?

~100–300 Spieler pro Zielföderation, 6–8 Länder. `swiss_2026` liefert bereits
eine Schweizer Baseline.

### E7. Langlebigkeit in den Top 100 *(Priorität 3)*

**Forschungsfrage:** Wie lange bleiben Top-100-Spieler in den Top 100? Gibt es
Vorläufer-Signale für den Abstieg in den Spielergebnissen?

Alle Spieler, die zwischen 2010 und 2026 irgendwann in der FIDE-Top-100 waren
(~150–200 Spieler, stark überlappend mit `elite_2600`). Kleiner Aufwand,
da grösster Teil bereits gescrapt.

---

## F — Analysen auf Basis der monatlichen ELO-Historie

> **Kernidee:** `rating_history.published_rating` ist mit 164 Monats-Snapshots
> (Sep 2012 – Apr 2026, ab 2026 bis 2006 erweitert) für ~1,8 Mio. Spieler vorhanden.
> Das ist ein **eigenständiger Analysedatensatz** — unabhängig vom Scraping der
> Einzelpartien. Die folgenden Analysen sind rein SQL-basiert auf `rating_history`
> und `players`, kein neues Scraping nötig.

---

### F1. Alters-Rating-Kurven für die Gesamtpopulation

**Forschungsfrage:** Wann erreicht ein Schachspieler typischerweise seinen
Leistungspeak, wie lange hält er an, und wie verläuft der Abstieg? Unterscheiden
sich diese Kurven nach Geschlecht, Nationalität, Titelklasse?

Mit 20 Jahren monatlicher Daten für 1,8 Mio. Spieler lassen sich für jeden Spieler
Alters-Rating-Kurven ableiten. Das ist die beste Datenbasis, die für diese Frage je
verfügbar war — und für Schach bisher nicht systematisch ausgewertet wurde.

```sql
-- Rating nach Alter pro Spieler (Basis für Kurvenanpassung)
SELECT p.fide_id, p.sex, p.federation,
       EXTRACT(YEAR FROM rh.period) - p.birth_year AS age,
       MAX(rh.published_rating)                     AS rating_at_age
FROM rating_history rh JOIN players p USING(fide_id)
WHERE p.birth_year > 0 AND rh.published_rating IS NOT NULL
GROUP BY 1, 2, 3, 4
```

**Kein neues Scraping nötig. Direkt umsetzbar.**

---

### F2. Historische weibliche Population — die "wahre female_top"

**Problem:** `female_top` bildet nur Spielerinnen ab, die 2026 noch aktiv und im
Rating-Band 2400–2600 sind. Spielerinnen, die dort 2013–2020 waren und danach
abstiegen oder aufhörten, sind komplett unsichtbar.

**Lösung:** `rating_history` liefert für jede Frau mit bekannter FIDE-ID ihren
monatlichen ELO-Verlauf. Damit kann die vollständige historische Population
der Frauen ≥ 2400 identifiziert werden:

- Wie viele Frauen waren in jedem Monat seit 2012 im 2400-Band?
- Wie lange blieben sie dort?
- Wer hat die 2400-Grenze überschritten und wo ist sie heute?

Diese Population ist für den Geschlechtervergleich methodisch sauberer als der
April-2026-Snapshot. **Kein Scraping nötig — nur `rating_history` + `players`.**

---

### F3. Rating-Mobilität — wer steigt auf, wer steigt ab?

**Forschungsfrage:** Wie hoch ist die Fluktuation in verschiedenen ELO-Bändern?
Welcher Anteil der Spieler in einem Band ist im nächsten Monat noch dort?
Unterscheidet sich die Mobilität nach Geschlecht oder Nationalität?

```sql
-- Monatliche Ein-/Austritte im Band 2400-2600 nach Geschlecht
SELECT period, p.sex,
       SUM(CASE WHEN rh.published_rating BETWEEN 2400 AND 2600 THEN 1 ELSE 0 END) AS im_band
FROM rating_history rh JOIN players p USING(fide_id)
WHERE rh.published_rating IS NOT NULL
GROUP BY 1, 2
```

Mobilitätsanalyse auf 20 Jahren Monatsdaten ist methodisch robust genug für
eine **wissenschaftliche Publikation** (Abschnitt C1).

---

### F4. Frauenanteil nach ELO-Band über Zeit — der "Glass Ceiling"-Trend

**Forschungsfrage:** Wie hat sich der Frauenanteil in verschiedenen ELO-Bändern
von 2006 bis 2026 entwickelt? Ist die "gläserne Decke" bei bestimmten Ratings
stabiler, dünner oder dicker geworden?

```sql
SELECT period,
       CASE WHEN published_rating >= 2600 THEN '2600+'
            WHEN published_rating >= 2400 THEN '2400-2599'
            WHEN published_rating >= 2200 THEN '2200-2399'
            WHEN published_rating >= 2000 THEN '2000-2199'
       END AS band,
       AVG(CASE WHEN p.sex='F' THEN 1.0 ELSE 0.0 END) AS frauenanteil
FROM rating_history rh JOIN players p USING(fide_id)
WHERE published_rating >= 2000
GROUP BY 1, 2 ORDER BY 1, 2
```

Reine `rating_history`-Abfrage. Ergibt 20-Jahres-Zeitreihe ohne neues Scraping.

---

### F5. Nationale Entwicklungswellen

**Forschungsfrage:** Welche Länder haben seit 2006 den grössten Zuwachs an
Spielern über bestimmten ELO-Schwellen (2000, 2200, 2400) erzielt? Welche
Länder "verlieren" Spitzenspieler durch Emigration oder Inaktivität?

Direkter Indikator für die Effektivität nationaler Förderprogramme — ohne
Einzelpartien-Scraping auswertbar. Besonders interessant für den Vergleich
China vs. Indien (beide mit rasant wachsenden Spielerpopulationen).

---

### F6. ELO-Inflation über Generationen

**Forschungsfrage:** Ist ein Rating von 2500 heute dasselbe wie ein Rating von
2500 im Jahr 2006? Oder gibt es systematische Inflation durch die Ausweitung
der FIDE-Spielerbasis?

Methode: Vergleiche Kohorten von Spielern, die in verschiedenen Jahren erstmals
2000+ erreichten. Halten sie ihr Rating länger oder kürzer? Steigen sie im Mittel
höher? Eine Antwort auf diese Frage ist methodisch zentral für alle Zeitreihen-
vergleiche im Projekt.

---

### F7. Survivorship-freie weibliche Studiengruppe

Die Kombination aus F2 (historische Population) und vorhandenem Scraping ermöglicht:

1. Identifikation aller Frauen, die seit 2012 jemals 2400+ hatten (~150–200 Spielerinnen
   statt 66)
2. Für noch nicht gescrapte: gezieltes Nachscrapen der aktiven Perioden
3. Ergibt eine vollständige, survivorship-freie Studiengruppe

Das ist die methodisch stärkste Grundlage für den Geschlechtervergleich — und
erfordert deutlich weniger Scraping als eine komplette Neudefinition der Gruppen.

---

### Einordnung: Was die ELO-Historie ermöglicht vs. was Scraping braucht

| Analyse | Nur `rating_history` | Scraping nötig |
|---|---|---|
| Alters-Rating-Kurven (F1) | ✅ | — |
| Historische Population (F2, B8) | ✅ | — |
| Rating-Mobilität (F3) | ✅ | — |
| Frauenanteil-Trend (F4) | ✅ | — |
| Nationale Wellen (F5) | ✅ | — |
| ELO-Inflation (F6) | ✅ | — |
| Gegnerstruktur-Analyse | — | ✅ |
| Performance vs. Erwartung | — | ✅ |
| Turniertyp-Analyse | — | ✅ |
| Karriere-Persistenz mit Spielen | — | ✅ |

---

## Zusammenfassung nach Impact

### Analytische Verbesserungen & Datenbasis (A–C)

| Massnahme | Aufwand | Analysegewinn | Priorität | Status |
|---|---|---|---|---|
| Gegner-Sex materialisieren (B1) | klein | direkt für NB07 | Hoch | ✅ erledigt |
| Wide-gap-Matches flaggen (B2) | klein | Datenqualität NB07 | Hoch | ✅ erledigt |
| Performance vs. Erwartung (A2) | klein | neue Kerndimension | Hoch | ✅ erledigt |
| Open/Frauenturnier-Split (A1) | mittel | zentral für Interpretation | Hoch | ✅ erledigt |
| tournament_type closed/knockout | klein | Formatanalyse | Hoch | ✅ erledigt |
| No-data-Rate analysieren (B3) | klein | neue Dimension | Mittel | ⬜ |
| Online vs. OTB (B7) | mittel | COVID-Bereinigung | Mittel | ⬜ |
| TXT-Snapshots 2010–2014 (B5) | mittel | QC + Resolver | Mittel | ✅ Sep 2012+ |
| Regressionsmodell (A6) | mittel | statistischer Haupttest | Mittel | ⬜ |
| Karriere-Persistenz (A3) | mittel | neue Fragestellung | Mittel | ⬜ |
| Dynamische Gruppenzugehörigkeit (B4) | SQL | methodisch sauber | Mittel | ⬜ jetzt möglich |
| Survivorship-Bias (B8) | SQL | Validierung | Mittel | ⬜ jetzt möglich |
| Regionale Analyse (A4) | mittel | Kontextualisierung | Niedrig | ⬜ |
| Turnierkategorie scrapen (B6) | gross | vollständige Formatklassif. | Niedrig | ⬜ |
| Dashboard (C2) | gross | Reichweite | Niedrig | ⬜ |
| Publikation (C1) | gross | Sichtbarkeit | Niedrig | ⬜ |

### ELO-Historie-Analysen (F) — kein neues Scraping nötig

| Analyse | Datenbasis | Aufwand | Publikationsreif | Priorität |
|---|---|---|---|---|
| Alters-Rating-Kurven (F1) | rating_history + players | klein | **Ja** | **1** |
| Frauenanteil-Trend / Glass Ceiling (F4) | rating_history + players | klein | **Ja** | **1** |
| Historische female_top Population (F2) | rating_history + players | klein | Ja | **1** |
| Rating-Mobilität (F3) | rating_history + players | mittel | Ja | **2** |
| ELO-Inflation über Generationen (F6) | rating_history + players | mittel | **Ja** | **2** |
| Survivorship-freie Studiengruppe (F7) | rating_history + wenig Scraping | mittel | — | **2** |
| Nationale Entwicklungswellen (F5) | rating_history + players | mittel | Ja | **3** |

### Neue Spielergruppen (D–E)

| Gruppe | Spieler (ca.) | Scraping-Aufwand | Neue Fragestellung | Priorität |
|---|---|---|---|---|
| Frauen 2200–2400 + Kontrolle (D1) | ~240 | mittel | Muster über ELO-Bänder | **1** |
| Alle GMs — Altern (E1) | ~1.800 | gross | Leistungspeak & Abstieg | **1** |
| COVID-Analyse bestehend + Infra (E2) | ~200 neu | klein | Online vs. OTB Effekte | **1** |
| Frauen 2000–2200 + Kontrolle (D2) | ~500 | gross | Breite Masse | **2** |
| Alle IMs — Durchbruch (E3) | ~4.000 | sehr gross | IM→GM Konversion | **2** |
| 2300-Schwelle (E4) | ~2.000 | gross | Stagnationsphänomen | **2** |
| Jugenddurchbruch (E5) | ~500 | mittel | Talententwicklung | **2** |
| Inaktive Spitzenspielerinnen (D3) | ~15 | klein | Survivorship-Bias | **2** |
| Gefallene 2400er-Spielerinnen (D4) | ~40 | mittel | Selektionseffekte | **3** |
| Nationale Systeme (E6) | ~1.200 | gross | Förderprogramm-Effekt | **3** |
| Top-100 Langlebigkeit (E7) | ~200 | klein | Abstiegsmuster | **3** |
| Women's Grand Prix (D5) | ~35 | klein | Elite-Kontext | **4** |
