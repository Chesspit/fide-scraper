"""FIDE Dashboard — Dash multi-page app."""
import dash
import dash_bootstrap_components as dbc
from dash import html, dcc

app = dash.Dash(
    __name__,
    use_pages=True,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    suppress_callback_exceptions=True,
)
app.title = "FIDE Analytics"

app.layout = html.Div(
    [
        dcc.Location(id="url-redirect", refresh=True),
        # Navigation bar
        dbc.Navbar(
            dbc.Container(
                [
                    html.Span("FIDE Analytics", className="navbar-brand mb-0 h1"),
                    dbc.Nav(
                        [
                            dbc.NavItem(
                                dcc.Link(
                                    page["name"],
                                    href=page["path"],
                                    className="nav-link",
                                )
                            )
                            for page in dash.page_registry.values()
                        ],
                        navbar=True,
                    ),
                ],
                fluid=True,
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
