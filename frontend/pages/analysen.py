"""Analysen — Einstiegsseite mit einer Kachel je Geschichte (Inhalte: stories.py)."""
import os
from pathlib import Path

import dash
from dash import dcc, html

from stories import stories_for

dash.register_page(
    __name__,
    path="/analysen",
    name="Analysen",
    title="ELO-Einsichten | Analysen",
    order=0,
)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli",
           "August", "September", "Oktober", "November", "Dezember"]


def _datum(iso: str) -> str:
    j, m, t = (int(x) for x in iso.split("-"))
    return f"{t}. {_MONATE[m - 1]} {j}"


def _cover(story: dict) -> html.Div:
    bild = f"analysen/{story['slug']}.png"
    style = ({"backgroundImage": f"url({dash.get_asset_url(bild)})"}
             if (_ASSETS / bild).exists() else {})
    return html.Div(
        html.Span(story["kicker"], className="story-kicker"),
        className="story-cover",
        style=style,
    )


def _kachel(story: dict) -> dcc.Link:
    return dcc.Link(
        [
            _cover(story),
            html.Div(
                [
                    html.Div(_datum(story["date"]), className="story-date"),
                    html.H3(story["title"], className="story-title"),
                    html.P(story["teaser"], className="story-teaser"),
                ],
                className="story-body",
            ),
        ],
        href=story["href"],
        className="story-card",
    )


def layout(**_):
    # EDITION wie in app.py (Default public); hier gelesen statt importiert,
    # weil app.py die Seiten selbst importiert.
    edition = os.environ.get("EDITION", "public").strip().lower()
    stories = stories_for(edition)
    return html.Div(
        [
            html.H2("Analysen", className="page-title"),
            html.P(
                "Geschichten aus über 20 Millionen FIDE-Partien und allen Ratinglisten "
                "seit 2009. Jede Kachel führt zu einer Auswertung.",
                className="story-intro",
            ),
            html.Div([_kachel(s) for s in stories]
                     or [html.P("Die ersten Analysen erscheinen hier in Kürze.", className="story-intro")],
                     className="story-grid"),
        ],
        className="story-page",
    )
