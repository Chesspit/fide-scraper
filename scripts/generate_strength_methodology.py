"""Methodik-Anhang für die Stärke-Bucket-Auswertung (siehe
scripts/generate_strength_aggregate_export.py) — erklärt in Prosa, wer zur
Kohorte zählt, ab wann Partien einer Spielerin zählen, und mit welchem Rating
genau die ±50-Elo-Gegnerstärke-Buckets gebildet werden.

Wird als zusätzliche Seite(n) an die Auswertungs-Tabelle angehängt
(page-break-before, siehe .appendix-Klasse) — kein eigenständiges Dokument,
sondern ein Fragment zum Anhängen; `build_methodology_fragment()` wird direkt
von generate_strength_aggregate_export.py importiert.
"""

from __future__ import annotations


def build_methodology_fragment(cohort_size: int, n_players: int) -> str:
    return f"""
  <section class="appendix page-break">
    <div class="eyebrow">Methodik</div>
    <h1 class="appendix-h1">Wie diese Auswertung funktioniert</h1>

    <h2>1. Wer gehört zur Kohorte?</h2>
    <p>
      Datenquelle ist <code>rating_history.published_rating</code> — das offizielle
      FIDE-Rating aus den monatlichen/vierteljährlichen TXT-Snapshot-Listen (nicht ein
      vom Scraper aus einzelnen Partien berechneter Wert). Kriterium: Rang ≤
      {cohort_size} unter allen Frauen (<code>players.sex = 'F'</code>) nach
      <code>published_rating</code>, getrennt ermittelt für jedes der zehn Jahresenden
      (<code>YYYY-12-01</code>, 2016–2025). Eine Spielerin zählt zur Kohorte, sobald sie
      in <b>mindestens einem</b> dieser zehn Jahre zu den Top&nbsp;{cohort_size} gehörte
      — Union über die Jahre, kein Durchschnitt. Zusätzlich nur aktuell <b>aktive</b>
      Spielerinnen (<code>players.active = TRUE</code>): inzwischen aus dem FIDE-System
      ausgeschiedene Spielerinnen werden komplett herausgefiltert, auch aus den Jahren,
      in denen sie noch aktiv Top-{cohort_size} waren. Ergebnis: {n_players} Spielerinnen
      (siehe Roster-Liste).
    </p>

    <h2>2. Ab wann zählen die Partien einer Spielerin?</h2>
    <p>
      Für jede Spielerin wird ihr <b>Schwellenjahr</b> bestimmt: das erste Jahr
      (2016–2025), in dem ihre Dezember-<code>published_rating</code> ≥ 2400 lag. Ab dem
      1.&nbsp;Januar <b>dieses selben Schwellenjahres</b> bis Ende 2025 fließen
      <b>alle</b> Partien dieser Spielerin in die Auswertung — dauerhaft, auch wenn ihr
      Rating später wieder unter 2400 fällt. Vor dem Schwellenjahr wird nichts
      berücksichtigt. Das Zeitfenster ist dadurch pro Spielerin unterschiedlich lang
      (z.&nbsp;B. eine 2016er-Spitzenspielerin fließt mit zehn Jahren ein, eine 2025 neu
      über 2400 gestiegene Spielerin nur mit einem).
    </p>
    <p>
      Wichtig: Da die 2400er-Schwelle erst an der <b>Dezember</b>-Momentaufnahme
      festgestellt wird, zählt der Einschluss <b>rückwirkend ab Januar desselben
      Jahres</b> — nicht erst ab dem Folgejahr. Das Startdatum in der Abfrage ist
      explizit der 1.&nbsp;Januar des Schwellenjahres selbst, nicht der 1.&nbsp;Januar
      des Folgejahres. Praktisch heißt das: Auch Partien aus dem Frühjahr des
      Schwellenjahres zählen
      schon mit, obwohl die Spielerin zu diesem Zeitpunkt ihr eigenes Rating
      möglicherweise noch unterhalb von 2400 hatte und erst im Laufe des Jahres darüber
      gestiegen ist — die 2400-Schwelle ist ein <b>Jahres-Etikett</b>, kein exaktes
      Partie-für-Partie-Kriterium.
    </p>

    <h2>3. Wie wird „gleich starke" Gegnerin/Gegner (±50 Elo) bestimmt?</h2>
    <p>
      Hier kommen bewusst <b>zwei unterschiedliche FIDE-Rating-Konzepte</b> zum Einsatz
      — nicht dasselbe Rating wie in Abschnitt 1:
    </p>
    <ul>
      <li>
        <b>own_rating</b>: das <code>std_rating</code> aus <code>rating_history</code> —
        <b>nicht</b> die Jahresend-<code>published_rating</code>, sondern das Rating der
        Spielerin für den <b>FIDE-Rating-Zeitraum (<code>period</code>), in dem die
        konkrete Partie liegt</b> (monatlich seit August 2012, davor vierteljährlich).
      </li>
      <li>
        <b>opponent_rating</b>: das Rating des Gegners/der Gegnerin, wie es auf der
        FIDE-Berechnungsseite für genau diese Partie ausgewiesen ist — ebenfalls bezogen
        auf den Rating-Zeitraum der Partie, nicht auf das exakte Kalenderdatum des
        Turniers.
      </li>
    </ul>
    <p>
      Beide Werte sind also <b>Periodenwerte</b>: Alle Partien einer Spielerin im
      selben Rating-Zeitraum teilen sich denselben <code>own_rating</code>-Wert,
      unabhängig vom genauen Turniertag innerhalb des Monats/Quartals — es wird nicht
      auf den exakten Partietag interpoliert, weil FIDE selbst Ratings nur periodenweise
      veröffentlicht.
    </p>
    <p>
      Einteilung: <code>diff = opponent_rating − own_rating</code>;
      <code>diff &gt; 50</code> → <b>stärker</b>, <code>diff &lt; -50</code> →
      <b>schwächer</b>, <code>-50 ≤ diff ≤ 50</code> (beide Grenzen eingeschlossen) →
      <b>gleich stark</b>.
    </p>
    <p>
      Warum zwei verschiedene Ratings — <code>published_rating</code> für die
      Kohorten-Zugehörigkeit, <code>std_rating</code> für die Partie-Einstufung? Die
      Kohorte soll den offiziellen Jahresend-Status abbilden (das ist, was öffentlich
      als „Rang X der Frauenweltrangliste" zählt), während die Gegnerstärke-Einstufung
      so nah wie möglich am tatsächlichen Rating-Stand beider Spielenden <b>zum
      Zeitpunkt der jeweiligen Partie</b> sein soll — beide Konzepte sind bewusst
      getrennt gehalten, nicht vermischt.
    </p>

    <h2>4. Kennzahlen je Bucket × Gegner-Geschlecht</h2>
    <p>
      Partien-Anzahl, Sieg/Remis/Niederlage, Punktequote (Ø <code>result</code>: 1 /
      0,5 / 0), Ø-Gegner-Elo (<code>opponent_rating</code>) und Ø-Elo-Δ
      (<code>rating_change_weighted</code> — die von FIDE tatsächlich gutgeschriebene,
      K-Faktor-gewichtete Rating-Änderung). Jeder Bucket wird zusätzlich nach
      Gegner-Geschlecht aufgeschlüsselt: Gesamt / nur gegen Frauen / nur gegen Männer
      — Partien mit unbekanntem Gegner-Geschlecht fließen nur in die Gesamt-Zeile ein,
      nicht in F oder M.
    </p>
  </section>"""


APPENDIX_STYLE = """
  .appendix.page-break { break-before: page; page-break-before: always; padding-top: 0.3rem; }
  .appendix h1.appendix-h1 { font-family: Georgia, "Iowan Old Style", "Palatino Linotype", serif;
    font-weight: 500; font-size: 1.5rem; line-height: 1.2; margin: 0.3rem 0 1.3rem; letter-spacing: -0.01em; }
  .appendix h2 { font-size: 0.92rem; font-weight: 700; color: var(--ink); margin: 1.5rem 0 0.5rem; }
  .appendix h2:first-of-type { margin-top: 0; }
  .appendix p { max-width: 74ch; font-size: 0.85rem; line-height: 1.6; color: var(--muted); margin: 0 0 0.7rem; }
  .appendix p b { color: var(--ink); font-weight: 600; }
  .appendix p code, .appendix li code { background: var(--accent-soft); color: var(--accent);
    padding: 0.05rem 0.35rem; border-radius: 4px; font-size: 0.85em; }
  .appendix ul { max-width: 74ch; margin: 0 0 0.7rem; padding-left: 1.2rem; }
  .appendix li { font-size: 0.85rem; line-height: 1.6; color: var(--muted); margin-bottom: 0.4rem; }
  .appendix li b { color: var(--ink); font-weight: 600; }
"""
