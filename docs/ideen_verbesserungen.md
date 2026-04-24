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

## Zusammenfassung nach Impact

### Analytische Verbesserungen & Datenbasis (A–C)

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
