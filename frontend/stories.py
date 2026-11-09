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
        "slug": "zone2400",
        "kicker": "Zone 2400",
        "title": "Durchgangsstation oder Endstation?",
        "teaser": "Wer die 2400 knackt, fällt meist wieder zurück. Was aus 2.187 Einsteigern "
                  "seit 2011 wurde — und warum das Alter fast alles entscheidet.",
        "href": "/zone-2400",
        "date": "2026-11-09",
        "editions": INTERN,
    },
    {
        "slug": "gm_peak",
        "kicker": "Mit 27 am stärksten",
        "title": "Wann ist ein Großmeister am stärksten?",
        "teaser": "Die Karrierekurve aller 1.899 Großmeister: steiler Anstieg, kurzes "
                  "Plateau, langsamer Abstieg — und warum es seit 2022 schneller bergab geht.",
        "href": "/gm-peak",
        "date": "2026-11-09",
        "editions": INTERN,
    },
]


def stories_for(edition: str) -> list[dict]:
    """Kacheln dieser Edition, neueste zuerst."""
    visible = [s for s in STORIES if edition in s["editions"]]
    return sorted(visible, key=lambda s: date.fromisoformat(s["date"]), reverse=True)
