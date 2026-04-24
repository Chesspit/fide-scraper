# Ideen für Verbesserungen

Stand: 24. April 2026

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

Mit 97,7 % Auflösung kann für fast jede Partie das Geschlecht des Gegners per JOIN
auf `players.sex` bestimmt werden. Eine materialisierte Spalte `opponent_sex` in
`game_results` — oder eine View — würde Notebook 07 robuster machen und Fragen
ermöglichen wie: Spielen Frauen bei gleicher ELO öfter gegen andere Frauen als
Männer gegen andere Männer?

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

### B3. No-data-Rate als eigene Analysedimension *(klein)*

`status='no_data'` in `scrape_periods` bedeutet: In dieser Periode wurden keine
FIDE-gewerteten Partien gespielt. Die No-data-Rate pro Gruppe und Zeitraum ist
selbst ein Indikator für Turnierfrequenz und -verfügbarkeit — besonders relevant
für 2010–2014 und den COVID-Einbruch 2020.

### B4. Dynamische Gruppenzugehörigkeit *(gross, methodisch wichtig)*

Die aktuelle Logik friert den ELO-Stand April 2026 ein. Ein Spieler mit heutigem
Rating 2420 ist in `male_control`, war aber 2012 vielleicht bei 1900. Sauberer wäre
eine Periode-für-Periode-Zuordnung:

```sql
-- War dieser Spieler in dieser Periode zwischen 2400 und 2600?
SELECT fide_id, period
FROM rating_history
WHERE published_rating BETWEEN 2400 AND 2600
```

Die Datenbasis dafür existiert bereits in der DB — es wäre ein Schema-Umbau,
kein neues Scraping.

### B5. TXT-Snapshots 2010–2014 beschaffen *(mittel)*

Für die Perioden 2010–2014 fehlen QC-Referenzwerte und period-accurate Ratings für
den Resolver. Die FIDE-Archive haben diese Dateien
(z.B. `standard_jan13frl.zip`). Damit liessen sich:
- QC auf die vollen 196 Perioden ausdehnen
- Resolver für ältere Gegner verbessern
- Survivorship-Bias für 2010–2014 quantifizieren

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

### B8. Survivorship-Bias quantifizieren

Alle 1.094 Spieler existieren im April-2026-Snapshot. Spieler, die zwischen 2010
und 2025 die 2400-Grenze temporär überschritten haben, aber 2026 nicht mehr dort
sind, fehlen komplett. Eine Abfrage auf `rating_history` nach historischen Peaks
könnte diese Population identifizieren und den Bias abschätzen.

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

## Zusammenfassung nach Impact

| Massnahme | Aufwand | Analysegewinn | Priorität |
|---|---|---|---|
| Gegner-Sex materialisieren (B1) | klein | direkt für NB07 | Hoch |
| Wide-gap-Matches flaggen (B2) | klein | Datenqualität NB07 | Hoch |
| Performance vs. Erwartung (A2) | klein | neue Kerndimension | Hoch |
| Open/Frauenturnier-Split (A1) | mittel | zentral für Interpretation | Hoch |
| No-data-Rate analysieren (B3) | klein | neue Dimension | Mittel |
| Online vs. OTB (B7) | mittel | COVID-Bereinigung | Mittel |
| TXT-Snapshots 2010–2014 (B5) | mittel | QC + Resolver | Mittel |
| Regressionsmodell (A6) | mittel | statistischer Haupttest | Mittel |
| Karriere-Persistenz (A3) | mittel | neue Fragestellung | Mittel |
| Turnierkategorie scrapen (B6) | gross | ermöglicht A1 vollständig | Mittel |
| Dynamische Gruppenzugehörigkeit (B4) | gross | methodisch sauber | Niedrig |
| Survivorship-Bias (B8) | mittel | Validierung | Niedrig |
| Regionale Analyse (A4) | mittel | Kontextualisierung | Niedrig |
| Dashboard (C2) | gross | Reichweite | Niedrig |
| Publikation (C1) | gross | Sichtbarkeit | Niedrig |
