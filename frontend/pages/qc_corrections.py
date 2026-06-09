"""FIDE 2024 Korrekturen — Einmalige ELO-Anpassung vom März 2024."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, callback, dash_table, dcc, html

import data_qc

dash.register_page(
    __name__,
    path="/qc-corrections",
    name="FIDE 2024 Korrekturen",
    title="ELO-Einsichten | FIDE 2024 Korrekturen",
    order=21,
)

C_BG     = "#F5F5F5"
C_CARD   = "#FFFFFF"
C_BORDER = "#E0E0E0"
C_TEXT   = "#333333"
C_MUTED  = "#888888"
C_NEG    = "#E88080"
C_POS    = "#7BC67E"

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

LABEL_STYLE = {"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "4px"}

ERKLAERUNG = (
    "Im März 2024 führte die FIDE eine einmalige Ratingkorrektur für alle Spieler "
    "mit einem Rating unter 2000 durch. Hintergrund war eine systematische Unterbewertung "
    "schwächerer Spieler. "
    "Die Korrektur wurde auf das Post-Game-Rating des Monats März 2024 angewendet "
    "— also nach Berücksichtigung der im März gespielten Partien. "
    "Spieler, die durch schlechte Ergebnisse im März unter 2000 rutschten, "
    "erhielten ebenfalls eine Korrektur."
)

FORMEL = "+0,4 × (2000 − Post-Game-Rating März 2024)"

layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "20px"},
    children=[
        html.H4("FIDE 2024 — Einmalige ELO-Korrektur",
                style={"marginBottom": "12px", "color": C_TEXT}),

        # Erklärungsbox
        html.Div(style={**CARD, "borderLeft": "4px solid #4A7AB5"}, children=[
            html.P(ERKLAERUNG,
                   style={"fontSize": "0.9rem", "color": C_TEXT, "marginBottom": "8px"}),
            html.Div([
                html.Span("Formel: ", style={"fontWeight": "600", "fontSize": "0.9rem"}),
                html.Code(FORMEL, style={
                    "backgroundColor": "#F0F4FF",
                    "padding": "2px 8px",
                    "borderRadius": "4px",
                    "fontSize": "0.88rem",
                    "color": "#2A4A8A",
                }),
            ]),
        ]),

        # Suchfelder
        html.Div(style=CARD, children=[
            dbc.Row([
                dbc.Col([
                    html.Label("Spieler (Name)", style=LABEL_STYLE),
                    dbc.Input(
                        id="qcc-name-filter",
                        debounce=True,
                        style={"fontSize": "0.9rem"},
                    ),
                ], md=5),
                dbc.Col([
                    html.Label("Föderation", style=LABEL_STYLE),
                    dcc.Dropdown(
                        id="qcc-fed-filter",
                        options=data_qc.get_federation_options(),
                        value="GER",
                        clearable=False,
                        style={"fontSize": "0.9rem"},
                    ),
                ], md=2),
            ], className="g-2 align-items-end"),
        ]),

        # Tabelle
        html.Div(style=CARD, children=[
            html.Div([
                html.Span("Spielerliste (alphabetisch)",
                          style={"fontSize": "0.85rem", "color": C_MUTED}),
                html.Span(id="qcc-table-note",
                          style={"fontSize": "0.8rem", "color": C_MUTED, "marginLeft": "10px"}),
            ], style={"marginBottom": "8px"}),
            html.Div(id="qcc-player-table"),
        ]),
    ],
)


@callback(
    Output("qcc-player-table", "children"),
    Output("qcc-table-note",   "children"),
    Input("qcc-name-filter", "value"),
    Input("qcc-fed-filter",  "value"),
)
def update_table(name_filter, fed_filter):
    TABLE_LIMIT = 500
    name_filter = name_filter or ""
    fed_filter  = fed_filter  or ""

    try:
        df = data_qc.load_corrections_table(
            "all", name_filter=name_filter, fed_filter=fed_filter, limit=TABLE_LIMIT
        )
    except Exception as e:
        return html.Div(f"Fehler: {e}", style={"color": C_NEG}), ""

    if df.empty:
        return html.Div("Keine Spieler gefunden.", style={"color": C_MUTED}), ""

    total = data_qc.load_corrections_kpis("all").get("spieler", 0)
    note = (
        f"— {len(df):,} von {total:,} Spielern (gefiltert/begrenzt)"
        if len(df) >= TABLE_LIMIT or (name_filter or fed_filter)
        else f"— {len(df):,} Spieler"
    )

    # Spaltenköpfe: id für Daten, name für Anzeige (mit Zeilenumbruch)
    columns = [
        {"name": "Spieler",              "id": "spieler"},
        {"name": "Föd.",                 "id": "federation"},
        {"name": "ELO\nFebruar",         "id": "elo_feb"},
        {"name": "Δ Rating\nPartien",        "id": "delta_partien"},
        {"name": "Post-Game\nRating",            "id": "elo_nach"},
        {"name": "Korrektur\nFIDE",       "id": "korrektur"},
        {"name": "ELO\nMärz",            "id": "elo_maerz"},
    ]
    df.columns = [c["id"] for c in columns]

    table = dash_table.DataTable(
        data=df.to_dict("records"),
        columns=columns,
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#F0F0F0",
            "fontWeight": "600",
            "fontSize": "0.8rem",
            "color": C_TEXT,
            "textAlign": "center",
            "whiteSpace": "pre-wrap",
            "lineHeight": "1.3",
            "paddingTop": "6px",
            "paddingBottom": "6px",
        },
        style_cell={
            "fontSize": "0.85rem",
            "padding": "5px 8px",
            "textAlign": "center",
            "color": C_TEXT,
        },
        style_cell_conditional=[
            {"if": {"column_id": "spieler"},
             "textAlign": "left", "minWidth": "180px", "maxWidth": "260px"},
            {"if": {"column_id": "federation"}, "width": "55px"},
            {"if": {"column_id": "elo_feb"},    "width": "80px"},
            {"if": {"column_id": "delta_partien"}, "width": "80px"},
            {"if": {"column_id": "elo_nach"},   "width": "100px"},
            {"if": {"column_id": "korrektur"},  "width": "80px"},
            {"if": {"column_id": "elo_maerz"},  "width": "80px"},
        ],
        style_data_conditional=[
            {"if": {"filter_query": "{delta_partien} < -50"},
             "color": C_NEG},
            {"if": {"filter_query": "{delta_partien} > 50"},
             "color": C_POS},
        ],
        page_size=50,
        page_action="native",
        sort_action="none",
    )

    return table, note
