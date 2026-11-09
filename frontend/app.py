"""ELO-Einsichten — Dash multi-page app in zwei Editionen.

EDITION=public  nur öffentliche Seiten (elo.chesspit.net, ohne Passwort)
EDITION=intern  alle Seiten (internelo.chesspit.net, BasicAuth)

Sicherheitsprinzip (docs/architektur_vier_bloecke.md, Abschnitt 0): Interne
Seiten werden in der öffentlichen Edition gar nicht importiert. Dash-Callbacks
sind eigene HTTP-Endpunkte — Verstecken in der Navigation reichte nicht.
Deshalb pages_folder="" und explizite Importe statt Auto-Discovery.
Ohne EDITION gilt die öffentliche Edition (sicherer Default).
"""
import importlib
import os
# DATABASE_URL (+ ANTHROPIC_API_KEY) lädt core.config aus dem .env im Repo-Root,
# bevor die Seiten ihre Datenmodule importieren. Im Container kommen sie aus der
# Umgebung; load_dotenv überschreibt vorhandene Variablen nicht.
import core.config  # noqa: F401

import dash
import dash_bootstrap_components as dbc
from dash import html, dcc

EDITIONS = ("public", "intern")
# Seitenmodul → Editionen, in denen es registriert wird (Seitenaufteilung Abschnitt 0).
PAGES = {
    "pages.analysen":        {"public", "intern"},   # /analysen — Einstieg, Kacheln aus stories.py
    "pages.elo_c":           {"public", "intern"},   # /c
    "pages.elo_dist":        {"public", "intern"},   # /dist
    "pages.title_evolution": {"public", "intern"},   # /titles
    "pages.player_profile":  {"public", "intern"},   # /player-profile
    "pages.gm_peak":         {"intern"},             # /gm-peak — Story E1, öffentlich erst nach OK
    "pages.game_detail":     {"intern"},             # /games
    "pages.arpad":           {"intern"},             # /arpad — kostet Anthropic-Guthaben
}


def get_edition() -> str:
    edition = os.environ.get("EDITION", "public").strip().lower()
    if edition not in EDITIONS:
        raise RuntimeError(f"EDITION={edition!r} unbekannt, erlaubt: {EDITIONS}")
    return edition


EDITION = get_edition()

app = dash.Dash(
    __name__,
    use_pages=True,
    pages_folder="",
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "ELO-Einsichten"
server = app.server  # WSGI-Einstieg für gunicorn (frontend/Dockerfile)

for _name in (m for m, editions in PAGES.items() if EDITION in editions):
    _module = importlib.import_module(_name)
    # Bei Auto-Discovery übernimmt Dash das Modul-Layout selbst; mit
    # pages_folder="" muss es hier eingetragen werden (sonst NoLayoutException).
    dash.page_registry[_name]["layout"] = _module.layout

# Header-Navigation: Gruppen in fester Reihenfolge, "Analysen" = Kachelseite.
# Seiten erscheinen nur, wenn sie in dieser Edition registriert sind; eine Gruppe
# ohne registrierte Seite entfällt. „Premium" ist nur eine Beschriftung, kein Zugangsschutz.
# Die QC-Seiten liegen seit Phase 3b im Orchestrator-Dashboard (Tab „QC").
_NAV_GROUPS = [
    ("Interaktiv", ["/c", "/dist", "/titles"]),
    (None,         ["/analysen"]),   # Kachelseite, eigener Eintrag ohne Gruppenlabel
    ("Premium",    ["/player-profile", "/games", "/arpad"]),
]

_GROUP_LABEL_STYLE = {
    "color": "rgba(255,255,255,0.45)",
    "fontSize": "0.72rem",
    "letterSpacing": "0.08em",
    "textTransform": "uppercase",
    "marginRight": "2px",
    "whiteSpace": "nowrap",
}


def _nav_group(label: str | None, paths: list[str], first: bool) -> html.Span | None:
    """Gruppenlabel (optional) + Links der registrierten Seiten, None wenn keine registriert."""
    by_path = {p["path"]: p for p in dash.page_registry.values()}
    links = [
        dbc.NavItem(dcc.Link(
            by_path[path]["name"], href=path,
            className="nav-link fw-semibold" if path == "/analysen" else "nav-link",
        ))
        for path in paths
        if path in by_path
    ]
    if not links:
        return None
    return html.Span(
        [
            *([html.Span(label, style=_GROUP_LABEL_STYLE)] if label else []),
            dbc.Nav(links, navbar=True, style={"display": "inline-flex"}),
        ],
        style={"display": "inline-flex", "alignItems": "center",
               "marginLeft": "0" if first else "14px"},
    )


def _nav_groups() -> list[html.Span]:
    groups = [_nav_group(label, paths, first=(i == 0))
              for i, (label, paths) in enumerate(_NAV_GROUPS)]
    return [g for g in groups if g is not None]


app.layout = html.Div(
    [
        dcc.Location(id="url-redirect", refresh=True),
        # Navigation bar
        dbc.Navbar(
            dbc.Container(
                [
                    # Brand
                    html.Span(
                        [html.Span("ELO-Einsichten"), html.Span(" 🇩🇪", style={"fontSize": "1.1rem"})]
                        + ([dbc.Badge("intern", color="warning", className="ms-2")]
                           if EDITION == "intern" else []),
                        className="navbar-brand mb-0 h1",
                        style={"marginRight": "12px"},
                    ),
                    # Navigation: Interaktiv · Analysen (Kachelseite) · Premium
                    *_nav_groups(),
                ],
                fluid=True,
                style={"display": "flex", "alignItems": "center", "flexWrap": "wrap"},
            ),
            color="dark",
            dark=True,
            className="mb-2",
        ),

        # Page content
        dash.page_container,
    ]
)

from dash import Input, Output

@app.callback(Output("url-redirect", "pathname"), Input("url-redirect", "pathname"))
def redirect_root(pathname):
    if pathname == "/":
        return "/analysen"
    return pathname


if __name__ == "__main__":
    app.run(debug=True, port=8050)
