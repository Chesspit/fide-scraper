"""ELO-Einsichten — Dash multi-page app."""
import dash
import dash_bootstrap_components as dbc
from dash import html, dcc

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "ELO-Einsichten"

# Seiten-Gruppen (anhand order-Wert aus register_page)
# Aktiv: order 1–9  |  Test: order 10+  |  QC: order 20+
_AKTIV_PATHS = {"/c", "/dist", "/player-profile"}
_TEST_PATHS  = {"/games", "/titles"}
_QC_PATHS    = {"/qc", "/qc-corrections"}


def _nav_group(label: str, paths: set) -> html.Span:
    """Gibt eine Navbar-Gruppe mit Label + Links zurück."""
    pages = sorted(
        [p for p in dash.page_registry.values() if p["path"] in paths],
        key=lambda p: p.get("order", 99),
    )
    links = [
        dbc.NavItem(
            dcc.Link(p["name"], href=p["path"], className="nav-link")
        )
        for p in pages
    ]
    return html.Span(
        [
            html.Span(
                label,
                style={
                    "color": "rgba(255,255,255,0.45)",
                    "fontSize": "0.72rem",
                    "letterSpacing": "0.08em",
                    "textTransform": "uppercase",
                    "marginRight": "4px",
                    "marginLeft": "16px",
                    "whiteSpace": "nowrap",
                },
            ),
            dbc.Nav(links, navbar=True, style={"display": "inline-flex"}),
        ],
        style={"display": "inline-flex", "alignItems": "center"},
    )


app.layout = html.Div(
    [
        dcc.Location(id="url-redirect", refresh=True),
        # Navigation bar
        dbc.Navbar(
            dbc.Container(
                [
                    # Brand
                    html.Span(
                        [html.Span("ELO-Einsichten"), html.Span(" 🇩🇪", style={"fontSize": "1.1rem"})],
                        className="navbar-brand mb-0 h1",
                        style={"marginRight": "24px"},
                    ),
                    # Aktiv-Gruppe
                    _nav_group("Aktiv", _AKTIV_PATHS),
                    # Trennstrich
                    html.Span(
                        "|",
                        style={
                            "color": "rgba(255,255,255,0.25)",
                            "margin": "0 8px",
                            "fontSize": "1.1rem",
                        },
                    ),
                    # Test-Gruppe
                    _nav_group("Test", _TEST_PATHS),
                    # Trennstrich
                    html.Span(
                        "|",
                        style={
                            "color": "rgba(255,255,255,0.25)",
                            "margin": "0 8px",
                            "fontSize": "1.1rem",
                        },
                    ),
                    # QC-Gruppe
                    _nav_group("QC", _QC_PATHS),
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
        return "/player-profile"
    return pathname


if __name__ == "__main__":
    app.run(debug=True, port=8050)
