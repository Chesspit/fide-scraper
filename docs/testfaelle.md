# Testfälle zur Datenqualität

Zwei wiederholbare Prüfungen gegen die Produktionsdaten. Beide lesen nur; sie rufen FIDE nicht ab
und ändern nichts. Grundlage sind die vorhandenen Werkzeuge `qc/audit.py` und `qc/integrity.py`.

| | Testfall A: Ist-Soll des Orchestrators | Testfall B: Reconciliation |
|---|---|---|
| Frage | Hat der Orchestrator für alle Soll-Kombinationen Arbeit angelegt, und liegt für alles, was er als erledigt meldet, wirklich ein Ergebnis vor? | Ist alles, was laut offizieller FIDE-Monatsliste gespielt wurde, auch gescrapt – mit richtiger Partienzahl und passender Rating-Differenz? |
| Massstab | Queue (`orchestrator.scrape_groups`) und Buchführung (`scrape_periods`) gegen Liste und Rohdaten | offizielle Liste (`rating_history.published_rating`, `num_games`) gegen `game_results` |

**Soll-Kombination:** ein Paar (Spieler, Periode), für das die offizielle Liste mindestens eine
gewertete Partie ausweist (`num_games > 0`). Spieler, die in der neuesten Liste nicht mehr mit
Rating stehen (von FIDE zurückgezogen), zählen nicht zum Soll, sondern werden als Information geführt.

## Ausführung

Auf dem Server, im Worker-Container (keine Tunnel-Abbrüche, die Datenbank liegt nebenan).
`scripts/` und `qc/` werden aus dem Checkout eingehängt, ein Image-Build ist nicht nötig.
Ein Jahr dauert etwa 35 bis 80 Minuten; `--store` legt das Ergebnis in `qc_audit_runs` ab
(Dashboard-Tab «QC»).

```bash
cd /opt/fide-scraper/deploy/infomaniak
nohup docker compose run --rm -T \
  -v /opt/fide-scraper/scripts:/app/scripts:ro -v /opt/fide-scraper/qc:/app/qc:ro \
  worker sh -c '
    for y in 2020 2021 2022 2023 2024 2025 2026; do
      python scripts/audit_data.py --since $y-01 --until $y-12 --store --source abnahme --summary-only
    done
    python scripts/verify_scrape_integrity.py --limit 20
  ' > ~/abnahme_$(date +%Y%m%d).log 2>&1 &
```

Fortschritt: `tail -f ~/abnahme_*.log`. Das Audit endet mit Exit-Code 1, sobald ein harter Befund
vorliegt; die Schleife läuft trotzdem weiter. Das laufende Jahr wird auf den Vormonat gedeckelt.

Einzelnes Jahr oder Land (schnell):

```bash
python scripts/audit_data.py --since 2026-01 --federation LIE --summary-only
```

## Testfall A: Ist-Soll des Orchestrators

Voraussetzung für A1: die Queue ist leer (keine Gruppe `pending` oder `running`), sonst misst A1
offene Arbeit statt Lücken:

```sql
SELECT status, count(*) FROM orchestrator.scrape_groups
WHERE status IN ('pending','running','failed') GROUP BY 1;
```

| Prüfung | Kennzahl (Check-Id) | Bestanden, wenn | Abnahmelauf 15.11.2026 |
|---|---|---|---|
| A1 Plan gegen Liste | Soll-Kombinationen ohne Scrape-Status (`periods_missing`) | ≤ 0,1 % der Soll-Kombos je Jahr | 0,01–0,06 % – bestanden |
| A1 | Spieler mit Partien laut Liste, im Zeitraum nie gescrapt (`players_never_scraped`) | ≤ 0,1 % der Soll-Spieler je Jahr | 2020–2025: 0,01–0,03 % – bestanden; 2026: 0,14 % – **nicht bestanden** (November lief noch) |
| A2 Buchführung gegen Daten | Status `ok` ohne Partien (`ok_without_games`) | 0 | 0 |
| A2 | Status `no_data` mit Partien (`no_data_with_games`) | 0 | 0 |
| A2 | Partien ohne Status-Zeile (`orphan_games`) | 0 | 0 |
| A2 | Liegengebliebene Fehler-Zeilen (`blocked_error_rows`) | 0 | 0 |

A2 sind harte Kriterien: jede Zeile ist ein Widerspruch in der Buchführung. A1 hat eine Schwelle,
weil FIDE Listen nachträglich ändert und einzelne Neuzugänge erst mit der nächsten Liste sichtbar
werden.

**Kein Kriterium mehr: «Gruppe `done`, aber Soll-Kombos fehlen»** (`done_groups_missing_combos`).
Der Integritätslauf führt die Prüfung weiter aus, ihr Ergebnis zählt aber nicht zur Abnahme. Sie
bestimmt das Soll aus den *heute* aktiven Spielern mit ihrem *heutigen* Rating und erwartet für
jeden alle Perioden des Gruppenjahres; wer seither das Elo-Band gewechselt hat oder neu bewertet
wurde, erscheint als fehlend. Am 15.11.2026 meldete sie 6.748 von 10.789 Gruppen. Die Auswertung
der vollständigen Liste ergab keinen Hinweis auf echte Lücken: Die auffälligsten Gruppen sind
solche mit Elo-Band ab 0 (dort zählt die Prüfung unbewertete Spieler mit) und die Bänder
1400–1799 mit den meisten Neuzugängen; ab Elo 2000 gab es keinen harten Fall. Dieselbe Frage
beantwortet A1 gegen die offizielle Liste zuverlässiger.

## Testfall B: Reconciliation mit den Monatslisten

| Stufe | Kennzahl (Check-Id) | Bestanden, wenn | Abnahmelauf 15.11.2026 |
|---|---|---|---|
| B1 Kombination gescrapt | Liste sagt Partien, Scrape sagt «keine Daten» (`periods_listed_no_data`) | ≤ 0,1 % der versuchten Kombos je Jahr | 0,03–0,08 % – bestanden |
| B2 Partienzahl | gefundene Partien ÷ gelistete Partien (`pct_games_found`) | ≥ 96 % je Jahr | 96,3–98,2 % – bestanden |
| B3 Rechnung | Δ je Partie = Ergebnis − Erwartungswert (`game_formula`) | ≤ 0,1 % Abweichungen | 0,00 % – bestanden (81 Partien, siehe unten) |
| B3 | K × Δ = gewichtete Änderung (`k_times_change`); Wertebereiche (`value_ranges`) | 0 | 0 |

**Bekannte Ausnahme bei der Formel:** Die 81 Abweichungen liegen alle ab Periode 2025-11 und
betreffen Spieler mit Rating ab 2650 gegen mehr als 400 Punkte schwächere Gegner. Unsere Formel
kappt die Differenz dort bei 400, FIDE offenbar nicht mehr (vermutete Regeländerung ab Oktober
2025, noch nicht nachgeschlagen). Bis die Formel angepasst ist, sind diese Fälle kein Datenfehler.

Nur zur Information, kein Kriterium:

- **Weniger Partien als in der Liste und Rating-Differenz geht nicht auf**
  (`game_count_fewer_open`): Beobachtungszahl ohne Schwelle, am 15.11.2026 je Jahr 0,10–0,96 %
  der gescrapten Kombos. Warnsignal wäre ein sprunghafter Anstieg. Einordnung im Abschnitt unten.
- **Weniger bzw. mehr Partien als in der Liste** (`game_count_fewer`, `game_count_more`): 0,1–1,2 %
  je Jahr, in beide Richtungen fast gleich gross. Das spricht für Unterschiede zwischen Liste und
  Berechnungsseite (nachträgliche Wertungen), nicht für fehlende Daten.
- **Kette Liste[P] − Liste[vorher] = Σ K×Δ** (`chain_matches_list`): FIDE korrigiert Ratings nach
  Erscheinen der Liste rückwirkend. Die Kette misst damit FIDE-Revisionen, nicht die Datenqualität.
- **Erstbewertungen:** Die Liste zählt dort auch Partien früherer Perioden; «weniger Partien bei
  Erstbewertung» (2–3 %) ist erwartbar.

### Was `game_count_fewer_open` aussagt

Bricht eine Antwort mitten in der Übertragung ab, fehlen Partien und mit ihnen ihr Rating-Beitrag:
die Rating-Differenz zwischen zwei Listen geht dann nicht mehr auf. Die Kennzahl zählt Kombos, bei
denen beides zutrifft. Sie ist eine **Obergrenze für unvollständig gespeicherte Antworten**, kein
Beweis: auch eine nachträgliche FIDE-Korrektur erzeugt dieses Bild, und eine fehlende Partie mit
weniger als einem Punkt Rating-Beitrag fällt durch das Raster. Gewissheit gäbe nur ein erneuter
Abruf der betroffenen Kombos.

Der Abnahmelauf vom 15.11.2026 zeigte, dass die Kennzahl kaum trennt: Rund 95 % aller Kombos mit
zu wenig Partien fallen hinein, und «mehr Partien als in der Liste» ist fast gleich häufig – das
können abgebrochene Antworten nicht erzeugen. Deshalb gilt keine Bestehens-Schwelle mehr (zuvor
≤ 0,5 %); die Zahl wird im Verlauf beobachtet.

Seit dem 15.11.2026 wiederholt der Worker Antworten mit nicht geschlossener Tabelle, statt sie zu
speichern. Neue Fälle sollten danach nicht mehr entstehen; die Kennzahl misst den Altbestand.

## Ergebnis lesen

```sql
SELECT id, finished_at, since_period, until_period, has_hard, headline
FROM qc_audit_runs WHERE source = 'abnahme' ORDER BY id;
```

Die Einzelwerte je Check stehen in der Spalte `checks` (JSON), die Jahreswerte in `by_year`.
Der Integritätslauf schreibt seine Zusammenfassung ans Ende der Log-Datei.
