"""Partien-Detail — alle gescrapten Partien eines Spielers."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
import psycopg2
from dash import Input, Output, State, callback, dash_table, dcc, html

dash.register_page(
    __name__,
    path="/games",
    name="Partien-Detail",
    title="ELO-Einsichten | Partien-Detail",
    order=10,
)

DEFAULT_FIDE_ID = 46616543  # Gukesh D (Weltmeister 2024)

# ---------------------------------------------------------------------------
# Farben (konsistent mit player_profile)
# ---------------------------------------------------------------------------
C_BG     = "#F5F5F5"
C_CARD   = "#FFFFFF"
C_BORDER = "#E0E0E0"
C_TEXT   = "#333333"
C_MUTED  = "#888888"
C_POS    = "#7BC67E"
C_NEG    = "#E88080"
C_DRAW   = "#BDBDBD"

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def _db():
    return psycopg2.connect(
        os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb")
    )


def _default_player_option() -> list[dict]:
    """Gibt den Default-Spieler als initiale Dropdown-Option zurück."""
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fide_id, name, federation, std_rating FROM players WHERE fide_id = %s",
                (DEFAULT_FIDE_ID,),
            )
            row = cur.fetchone()
        conn.close()
        if row:
            return [{"label": f"{row[1]} ({row[2]}, {row[3]})", "value": row[0]}]
    except Exception:
        pass
    return []


def search_players(q: str) -> list[dict]:
    if not q or len(q) < 2:
        return []
    try:
        conn = _db()
        with conn.cursor() as cur:
            if q.strip().lstrip("-").isdigit():
                cur.execute(
                    "SELECT fide_id, name, federation, std_rating FROM players "
                    "WHERE fide_id = %s LIMIT 1",
                    (int(q.strip()),),
                )
            else:
                cur.execute(
                    "SELECT fide_id, name, federation, std_rating FROM players "
                    "WHERE name ILIKE %s AND active = TRUE "
                    "ORDER BY std_rating DESC NULLS LAST LIMIT 25",
                    (f"%{q}%",),
                )
            rows = cur.fetchall()
        conn.close()
        return [{"label": f"{r[1]} ({r[2]}, {r[3]})", "value": r[0]} for r in rows]
    except Exception:
        return []


def load_games(fide_id: int) -> list[dict]:
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT
                    g.period,
                    rh.std_rating                           AS eigenes_rating,
                    g.color,
                    g.opponent_name,
                    g.opponent_title,
                    g.opponent_rating,
                    g.opponent_federation,
                    g.opponent_sex,
                    g.result,
                    g.rating_change,
                    g.rating_change_weighted,
                    g.expected_score,
                    g.over_performance,
                    g.tournament_name,
                    g.game_index
                FROM game_results g
                LEFT JOIN rating_history rh
                    ON rh.fide_id = g.fide_id AND rh.period = g.period
                WHERE g.fide_id = %s
                ORDER BY g.period DESC, g.game_index ASC
                """,
                (fide_id,),
            )
            cols = [d[0] for d in cur.description]
            rows = cur.fetchall()
        conn.close()
        return [dict(zip(cols, r)) for r in rows]
    except Exception:
        return []


def fmt_result(r) -> str:
    if r in ("1", "1.0", 1, 1.0):
        return "Sieg"
    if r in ("0.5", 0.5):
        return "Remis"
    if r in ("0", "0.0", 0, 0.0):
        return "Niederlage"
    return str(r)


def fmt_color(c) -> str:
    return "Weiß" if str(c).strip().upper() == "W" else "Schwarz"


def fmt_change(v) -> str:
    if v is None:
        return "–"
    try:
        f = float(v)
        return f"+{f:.2f}" if f > 0 else f"{f:.2f}"
    except Exception:
        return str(v)


def prepare_rows(games: list[dict]) -> list[dict]:
    out = []
    for g in games:
        result_str = fmt_result(g["result"])
        change = float(g["rating_change_weighted"]) if g["rating_change_weighted"] is not None else None
        sex = str(g["opponent_sex"]).strip() if g["opponent_sex"] else ""
        out.append({
            "Periode":        str(g["period"])[:7] if g["period"] else "–",
            "Eig. Rating":    g["eigenes_rating"] or "–",
            "Farbe":          fmt_color(g["color"]),
            "Gegner":         g["opponent_name"] or "–",
            "Titel":          g["opponent_title"] or "",          # leer wenn kein Titel
            "G.":             "F" if sex == "F" else ("M" if sex == "M" else ""),
            "Gegner-Rating":  g["opponent_rating"] or "–",
            "Föd.":           str(g["opponent_federation"]).strip() if g["opponent_federation"] else "–",
            "Ergebnis":       result_str,
            "Δ Rating":       fmt_change(g["rating_change_weighted"]),
            "Erw. Score":     f"{float(g['expected_score']):.3f}" if g["expected_score"] is not None else "–",
            "Turnier":        g["tournament_name"] or "–",
            "_change_raw":    change,
            "_result_raw":    result_str,
        })
    return out


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
layout = dbc.Container(fluid=True, style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "20px"}, children=[

    # ── Filter-Zeile: Dropdowns (links) + Zeitraum-Box (rechts) ─────────
    dbc.Row([
        # Linke Box: Spieler + Ergebnis + Farbe
        dbc.Col(
            html.Div(style=CARD, children=[
                dbc.Row([
                    dbc.Col([
                        html.Label("Spieler", style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "4px"}),
                        dcc.Dropdown(
                            id="gd-player-dd",
                            placeholder="Name oder FIDE-ID…",
                            options=_default_player_option(),
                            value=DEFAULT_FIDE_ID,
                            clearable=True,
                            searchable=True,
                            style={"fontSize": "0.9rem"},
                        ),
                    ], md=8),
                    dbc.Col([
                        html.Label("Ergebnis", style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "4px"}),
                        dcc.Dropdown(
                            id="gd-result-filter",
                            options=[
                                {"label": "Alle",        "value": "alle"},
                                {"label": "Sieg",        "value": "Sieg"},
                                {"label": "Remis",       "value": "Remis"},
                                {"label": "Niederlage",  "value": "Niederlage"},
                            ],
                            value="alle", clearable=False,
                            style={"fontSize": "0.9rem"},
                        ),
                    ], md=2),
                    dbc.Col([
                        html.Label("Farbe", style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "4px"}),
                        dcc.Dropdown(
                            id="gd-color-filter",
                            options=[
                                {"label": "Beide",    "value": "beide"},
                                {"label": "Weiß",     "value": "Weiß"},
                                {"label": "Schwarz",  "value": "Schwarz"},
                            ],
                            value="beide", clearable=False,
                            style={"fontSize": "0.9rem"},
                        ),
                    ], md=2),
                ], className="g-2 align-items-end"),
            ]),
        md=6),
        # Rechte Box: Zeitraum-Slider (eigene Karte)
        dbc.Col(
            html.Div(style=CARD, children=[
                html.Div("Zeitraum", style={
                    "fontSize": "0.85rem", "fontWeight": "600",
                    "color": C_MUTED, "marginBottom": "10px",
                }),
                dcc.Slider(
                    id="gd-year-slider",
                    min=2008, max=2026, step=1,
                    value=2008,
                    marks={
                        2008: "2008",
                        2010: "2010",
                        2015: "2015",
                        2020: "2020",
                        2025: "2025",
                        2026: "2026",
                    },
                    tooltip={"always_visible": False},
                    included=True,
                ),
            ]),
        md=6),
    ], className="g-3 mb-0"),

    # Kennzahlen
    html.Div(id="gd-stats-row", style={"marginBottom": "12px"}),

    # Tabelle
    html.Div(id="gd-table-container", children=[
        html.P("Bitte Spieler auswählen.", style={"color": C_MUTED, "fontSize": "0.9rem"}),
    ]),

    # Store
    dcc.Store(id="gd-search-store"),
])


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------
@callback(
    Output("gd-player-dd", "options"),
    Input("gd-player-dd", "search_value"),
)
def update_player_options(search):
    if not search or len(search) < 2:
        return dash.no_update
    return search_players(search)


@callback(
    Output("gd-stats-row",       "children"),
    Output("gd-table-container", "children"),
    Input("gd-player-dd",     "value"),
    Input("gd-result-filter", "value"),
    Input("gd-color-filter",  "value"),
    Input("gd-year-slider",   "value"),
    prevent_initial_call=False,
)
def update_table(fide_id, result_filter, color_filter, year_from):
    if not fide_id:
        fide_id = DEFAULT_FIDE_ID

    games = load_games(int(fide_id))
    if not games:
        return html.Div(), html.P("Keine Partien gefunden.", style={"color": C_MUTED})

    rows = prepare_rows(games)

    # Filter
    y_from = int(year_from) if year_from else 2008
    rows = [r for r in rows if int(r["Periode"][:4]) >= y_from]
    if result_filter != "alle":
        rows = [r for r in rows if r["Ergebnis"] == result_filter]
    if color_filter != "beide":
        rows = [r for r in rows if r["Farbe"] == color_filter]

    # Kennzahlen
    n = len(rows)
    wins   = sum(1 for r in rows if r["_result_raw"] == "Sieg")
    draws  = sum(1 for r in rows if r["_result_raw"] == "Remis")
    losses = sum(1 for r in rows if r["_result_raw"] == "Niederlage")
    changes = [r["_change_raw"] for r in rows if r["_change_raw"] is not None]
    avg_chg = sum(changes) / len(changes) if changes else 0
    total_chg = sum(changes) if changes else 0

    def stat_card(label, value, color=C_TEXT):
        return dbc.Col(html.Div(style={**CARD, "textAlign": "center", "padding": "10px"}, children=[
            html.Div(str(value), style={"fontSize": "1.4rem", "fontWeight": "bold", "color": color}),
            html.Div(label, style={"fontSize": "0.72rem", "color": C_MUTED, "lineHeight": "1.3"}),
        ]), xs=6, md=2)

    stats = dbc.Row([
        stat_card("Partien",     n),
        stat_card("Siege",       wins,   "#4CAF50"),
        stat_card("Remis",       draws,  "#888"),
        stat_card("Niederlagen", losses, "#E24B4A"),
        stat_card("Δ Rating pro Partie (im Durchschnitt)",
                  f"{avg_chg:+.2f}", "#4CAF50" if avg_chg >= 0 else "#E24B4A"),
        stat_card("Δ Rating über den Zeitraum (Summe)",
                  f"{total_chg:+.1f}", "#4CAF50" if total_chg >= 0 else "#E24B4A"),
    ], className="g-2")

    # Tabelle vorbereiten (interne Felder entfernen)
    display_cols = ["Periode", "Eig. Rating", "Farbe", "Gegner", "Titel", "G.",
                    "Gegner-Rating", "Föd.", "Ergebnis", "Δ Rating", "Erw. Score", "Turnier"]
    table_rows = [{k: r[k] for k in display_cols} for r in rows]

    # 2-zeilige Spaltenköpfe — Zeile 1 = Kontext, Zeile 2 = Hauptname
    # Keine Sortierung
    columns_def = [
        {"name": ["",          "Monat"],    "id": "Periode"},
        {"name": ["",          "Rating"],   "id": "Eig. Rating"},
        {"name": ["",          "Farbe"],    "id": "Farbe"},
        {"name": ["",          "Gegner"],   "id": "Gegner"},
        {"name": ["",          "Titel"],    "id": "Titel"},
        {"name": ["",          "Sex"],      "id": "G."},
        {"name": ["Rating",    "Gegner"],   "id": "Gegner-Rating"},
        {"name": ["",          "Föd."],     "id": "Föd."},
        {"name": ["",          "Ergebnis"], "id": "Ergebnis"},
        {"name": ["",          "Δ Rating"], "id": "Δ Rating"},
        {"name": ["Erwartete", "Score"],    "id": "Erw. Score"},
        {"name": ["",          "Turnier"],  "id": "Turnier"},
    ]

    table = dash_table.DataTable(
        data=table_rows,
        columns=columns_def,
        merge_duplicate_headers=False,
        page_size=50,
        page_action="native",
        sort_action="none",
        filter_action="none",
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#F0F0F0",
            "fontWeight": "bold",
            "fontSize": "0.80rem",
            "borderBottom": f"1px solid {C_BORDER}",
            "whiteSpace": "normal",
            "height": "auto",
            "textAlign": "center",
            "padding": "3px 8px",
        },
        style_cell={
            "fontSize": "0.82rem",
            "padding": "5px 10px",
            "textAlign": "left",
            "color": C_TEXT,
        },
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#FAFAFA"},
            {"if": {"filter_query": '{Ergebnis} = "Sieg"',        "column_id": "Ergebnis"}, "color": "#2E7D32", "fontWeight": "bold"},
            {"if": {"filter_query": '{Ergebnis} = "Niederlage"',  "column_id": "Ergebnis"}, "color": "#C62828", "fontWeight": "bold"},
            {"if": {"filter_query": '{Ergebnis} = "Remis"',       "column_id": "Ergebnis"}, "color": "#888"},
            {"if": {"filter_query": '{Farbe} = "Weiß"',           "column_id": "Farbe"},    "backgroundColor": "#FFFDE7"},
            {"if": {"filter_query": '{Farbe} = "Schwarz"',        "column_id": "Farbe"},    "backgroundColor": "#ECEFF1"},
        ],
        style_as_list_view=True,
    )

    return stats, html.Div(style=CARD, children=[table])
