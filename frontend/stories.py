"""Geschichten der Analysen-Übersicht (/analysen) — eine Kachel je Eintrag.

Neue Analyse aufnehmen: Eintrag oben einfügen (neueste zuerst). Felder:

    slug      eindeutig; Kachelbild unter assets/analysen/<slug>.png (optional,
              sonst Farbfläche). Bilder erzeugt build_story_covers.py.
    kicker    Schlagzeile auf dem Bild, kurz und in Versalien (wie ein Thumbnail)
    title     Überschrift unter dem Bild
    teaser    ein bis zwei Sätze
    href      Zielseite (muss in app.PAGES für die Edition registriert sein)
    date      Erscheinungsdatum (ISO)
    editions  in welchen Editionen die Kachel erscheint
"""
from datetime import date

PUBLIC_AND_INTERN = frozenset({"public", "intern"})
INTERN = frozenset({"intern"})

STORIES = [
    {
        "slug": "player",
        "kicker": "Steckbrief",
        "title": "Ein Spieler, eine Karriere",
        "teaser": "Rating-Verlauf, Gegner und Ergebnisse eines einzelnen Spielers — "
                  "von der ersten Liste bis heute.",
        "href": "/player-profile",
        "date": "2026-05-11",
        "editions": PUBLIC_AND_INTERN,
    },
    {
        "slug": "top100",
        "kicker": "Top 100",
        "title": "Wer sich an der Weltspitze hält",
        "teaser": "Elo-Verläufe aller Spieler, die je 2700 erreicht haben, vor dem "
                  "Hintergrund der Top-100-Weltrangliste seit 2009.",
        "href": "/c",
        "date": "2026-05-10",
        "editions": PUBLIC_AND_INTERN,
    },
    {
        "slug": "dist",
        "kicker": "Perzentile",
        "title": "Wo stehe ich in der Verteilung?",
        "teaser": "Perzentil-Bänder aller FIDE-Spieler je Monat — weltweit, für Europa "
                  "und DACH, mit eigenen Spielern zum Einblenden.",
        "href": "/dist",
        "date": "2026-05-10",
        "editions": PUBLIC_AND_INTERN,
    },
    {
        "slug": "titles",
        "kicker": "GM · IM",
        "title": "Die Landkarte der Großmeister",
        "teaser": "Wie sich GM- und IM-Titel von 2009 bis 2026 auf Länder und Kontinente "
                  "verteilen — absolut und je Einwohner.",
        "href": "/titles",
        "date": "2026-05-10",
        "editions": PUBLIC_AND_INTERN,
    },
    {
        "slug": "games",
        "kicker": "Partien",
        "title": "Jede Partie im Detail",
        "teaser": "Alle gescrapten Partien eines Spielers mit Gegner, Ergebnis und "
                  "Rating-Änderung.",
        "href": "/games",
        "date": "2026-05-13",
        "editions": INTERN,
    },
    {
        "slug": "arpad",
        "kicker": "Frag ARPAD",
        "title": "Fragen an die Daten",
        "teaser": "Der Chatbot beantwortet Fragen zu Ratings und Partien in normaler "
                  "Sprache.",
        "href": "/arpad",
        "date": "2026-07-24",
        "editions": INTERN,
    },
]


def stories_for(edition: str) -> list[dict]:
    """Kacheln dieser Edition, neueste zuerst."""
    visible = [s for s in STORIES if edition in s["editions"]]
    return sorted(visible, key=lambda s: date.fromisoformat(s["date"]), reverse=True)
