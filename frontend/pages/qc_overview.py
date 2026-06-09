"""QC Übersicht — Jahres- und Monatsdetail für Rating-Deltas."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
import pandas as pd
from dash import Input, Output, State, callback, dash_table, dcc, html

import data_qc

dash.register_page(
    __name__,
    path="/qc",
    name="QC Übersicht",
    title="ELO-Einsichten | QC Übersicht",
    order=20,
)

C_BG     = "#F5F5F5"
C_CARD   = "#FFFFFF"
C_BORDER = "#E0E0E0"
C_TEXT   = "#333333"
C_MUTED  = "#888888"
C_POS    = "#7BC67E"
C_NEG    = "#E88080"

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

FLAG_COLORS = [
    {"if": {"filter_query": '{flag} = "error"'}, "backgroundColor": "#FFF0F0", "color": "#C62828"},
    {"if": {"filter_query": '{flag} = "warn"'},  "backgroundColor": "#FFFDE7", "color": "#E65100"},
]


def _kpi_card(title: str, cid: str) -> dbc.Col:
    return dbc.Col(
        html.Div(style={**CARD, "textAlign": "center"}, children=[
            html.Div(title, style={"fontSize": "0.78rem", "color": C_MUTED, "marginBottom": "4px"}),
            html.Div("–", id=cid, style={"fontSize": "1.5rem", "fontWeight": "700", "color": C_TEXT}),
        ]),
        md=3,
    )


_ANNUAL_COLUMNS = [
    {"name": "Jahr",        "id": "Jahr"},
    {"name": "Spieler",    "id": "Spieler"},
    {"name": "Fenster",    "id": "Fenster"},
    {"name": "OK %",       "id": "OK %"},
    {"name": "Warn",       "id": "Warn"},
    {"name": "Error",      "id": "Error"},
    {"name": "Avg |Δadj|", "id": "Avg |Δadj|"},
]

# Jahresdaten beim Start laden; 2025 ist Index 1 (nach 2026)
try:
    _initial_df = data_qc.load_annual_table("all")
    _initial_df.columns = ["Jahr", "Spieler", "Fenster", "OK %", "Warn", "Error", "Avg |Δadj|"]
    _initial_data = _initial_df.to_dict("records")
    # Index von 2025 in der absteigend sortierten Liste finden
    _years = [r["Jahr"] for r in _initial_data]
    _default_row = _years.index(2025) if 2025 in _years else 0
except Exception:
    _initial_data = []
    _default_row = 0

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "20px"},
    children=[
        html.H4("QC Übersicht — Rating-Delta-Analyse",
                style={"marginBottom": "16px", "color": C_TEXT}),

        # KPI-Karten
        dbc.Row([
            _kpi_card("Fenster gesamt", "qc-kpi-total"),
            _kpi_card("OK %",           "qc-kpi-ok"),
            _kpi_card("Warn",           "qc-kpi-warn"),
            _kpi_card("Error",          "qc-kpi-error"),
        ], className="g-2 mb-2"),

        # Jahres-Tabelle
        html.Div(style=CARD, children=[
            html.Div("Jahresübersicht — Zeile klicken für Monatsdetail",
                     style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "8px"}),
            dash_table.DataTable(
                id="qc-annual-datatable",
                columns=_ANNUAL_COLUMNS,
                data=_initial_data,
                row_selectable="single",
                selected_rows=[_default_row],
                style_table={"overflowX": "auto"},
                style_header={
                    "backgroundColor": "#F0F0F0",
                    "fontWeight": "600",
                    "fontSize": "0.82rem",
                    "color": C_TEXT,
                    "borderBottom": f"2px solid {C_BORDER}",
                },
                style_cell={
                    "fontSize": "0.88rem",
                    "padding": "6px 10px",
                    "textAlign": "right",
                    "color": C_TEXT,
                },
                style_cell_conditional=[
                    {"if": {"column_id": "Jahr"}, "textAlign": "left", "fontWeight": "600"},
                ],
                style_data_conditional=[
                    {"if": {"filter_query": "{Error} > 0"},
                     "backgroundColor": "#FFF8F8"},
                    {"if": {"filter_query": "{Warn} > 0 && {Error} = 0"},
                     "backgroundColor": "#FFFDE7"},
                    {"if": {"state": "selected"},
                     "backgroundColor": "#E3F2FD", "border": "1px solid #90CAF9"},
                ],
                page_action="none",
            ),
        ]),

        # Monatsdetail
        html.Div(
            id="qc-detail-section",
            style={"display": "none"},
            children=[
                html.Div(id="qc-checksum-banner", style={"marginBottom": "12px"}),
                html.Div(style=CARD, children=[
                    html.Div(id="qc-detail-header",
                             style={"fontSize": "0.95rem", "fontWeight": "600",
                                    "color": C_TEXT, "marginBottom": "8px"}),
                    html.Div(id="qc-monthly-table"),
                ]),
                html.Div(style=CARD, children=[
                    html.Div("Top-10-Ausreißer (Warn + Error)",
                             style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "8px"}),
                    html.Div(id="qc-offenders-table"),
                ]),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

@callback(
    Output("qc-kpi-total", "children"),
    Output("qc-kpi-ok",    "children"),
    Output("qc-kpi-warn",  "children"),
    Output("qc-kpi-error", "children"),
    Input("qc-annual-datatable", "id"),   # einmaliger Trigger beim Laden
)
def update_kpis(_):
    try:
        kpi = data_qc.load_annual_kpis("all")
        return (
            f"{kpi['total_windows']:,}",
            f"{kpi['ok_pct']:.1f} %",
            str(kpi["total_warn"]),
            str(kpi["total_error"]),
        )
    except Exception as e:
        return "–", "–", "–", f"Err: {e}"


@callback(
    Output("qc-detail-section",  "style"),
    Output("qc-detail-header",   "children"),
    Output("qc-checksum-banner", "children"),
    Output("qc-monthly-table",   "children"),
    Output("qc-offenders-table", "children"),
    Input("qc-annual-datatable", "selected_rows"),
    State("qc-annual-datatable", "data"),
)
def update_monthly_detail(selected_rows, table_data):
    hidden  = {"display": "none"}
    visible = {"display": "block"}

    if not selected_rows or not table_data:
        return hidden, "", "", "", ""

    row = table_data[selected_rows[0]]
    year = row.get("Jahr")
    if not year:
        return hidden, "", "", "", ""

    try:
        # Monatstabelle
        df_m = data_qc.load_monthly_table(year, "all")

        # Zwei-Monats-Muster
        df_raw = data_qc.load_monthly_delta_adj(year, "all")
        patterns = data_qc.detect_two_month_patterns(df_raw)
        if not patterns.empty:
            patterns["period_end"] = pd.to_datetime(patterns["period_end"]).dt.strftime("%Y-%m")
            pattern_map = dict(zip(patterns["period_end"], patterns["pattern_players"]))
        else:
            pattern_map = {}

        if not df_m.empty:
            df_m["2-Monats-Muster"] = df_m["monat"].map(pattern_map).fillna(0).astype(int)
            col_map = {
                "monat":           "Monat",
                "fenster":         "Fenster",
                "ok_pct":          "OK %",
                "warn":            "Warn",
                "error":           "Error",
                "2-Monats-Muster": "2-Monats-Muster",
                "avg_delta_adj":   "Avg |Δadj|",
            }
            df_show = df_m[list(col_map.keys())].rename(columns=col_map)
            monthly_table = dash_table.DataTable(
                data=df_show.to_dict("records"),
                columns=[{"name": c, "id": c} for c in df_show.columns],
                style_table={"overflowX": "auto"},
                style_header={
                    "backgroundColor": "#F0F0F0",
                    "fontWeight": "600",
                    "fontSize": "0.82rem",
                    "color": C_TEXT,
                },
                style_cell={
                    "fontSize": "0.88rem",
                    "padding": "6px 10px",
                    "textAlign": "right",
                    "color": C_TEXT,
                },
                style_cell_conditional=[
                    {"if": {"column_id": "Monat"}, "textAlign": "left"},
                ],
                style_data_conditional=[
                    {"if": {"filter_query": "{Error} > 0"},
                     "backgroundColor": "#FFF0F0"},
                    {"if": {"filter_query": "{Warn} > 0 && {Error} = 0"},
                     "backgroundColor": "#FFFDE7"},
                    {"if": {"filter_query": "{2-Monats-Muster} > 0"},
                     "borderLeft": "3px solid #4A7AB5"},
                ],
                page_action="none",
            )
        else:
            monthly_table = html.Div("Keine Monatsdaten.", style={"color": C_MUTED})

        # Jahresprüfsumme
        cs = data_qc.load_annual_checksum(year, "all")
        ok_pct = round(100.0 * cs["ok"] / cs["total"], 1) if cs["total"] else 0
        if ok_pct >= 97:
            bg, border = "#E8F5E9", "#A5D6A7"
        elif ok_pct >= 90:
            bg, border = "#FFFDE7", "#FFE082"
        else:
            bg, border = "#FFF0F0", "#FFCDD2"
        checksum_banner = html.Div(
            style={"backgroundColor": bg, "borderRadius": "6px",
                   "border": f"1px solid {border}", "padding": "10px 14px"},
            children=[
                html.Span(f"Jahresprüfsumme {year} ", style={"fontWeight": "600"}),
                html.Span(f"(Dez {year-1} → Dez {year}): "),
                html.Span(f"{cs['ok']} / {cs['total']} OK ({ok_pct:.1f} %)",
                          style={"fontWeight": "600"}),
                html.Span(
                    f"  |  Warn: {cs['warn']}  Error: {cs['error']}  |  Avg |Δ|: {cs['avg_diff']}",
                    style={"color": C_MUTED, "marginLeft": "8px"},
                ),
            ],
        )

        # Top-10-Ausreißer
        df_o = data_qc.load_worst_offenders(year, "all")
        if not df_o.empty:
            df_o.columns = ["Spieler", "Gruppe", "Monat", "Von-ELO", "Nach-ELO", "Δadj", "Flag"]
            offenders_table = dash_table.DataTable(
                data=df_o.to_dict("records"),
                columns=[{"name": c, "id": c} for c in df_o.columns],
                style_table={"overflowX": "auto"},
                style_header={
                    "backgroundColor": "#F0F0F0",
                    "fontWeight": "600",
                    "fontSize": "0.82rem",
                    "color": C_TEXT,
                },
                style_cell={"fontSize": "0.88rem", "padding": "6px 10px", "color": C_TEXT},
                style_cell_conditional=[
                    {"if": {"column_id": "Spieler"}, "textAlign": "left"},
                    {"if": {"column_id": "Gruppe"},  "textAlign": "left"},
                ],
                style_data_conditional=FLAG_COLORS,
                page_action="none",
            )
        else:
            offenders_table = html.Div("Keine Ausreißer.", style={"color": C_MUTED})

        return visible, f"Monatsdetail {year}", checksum_banner, monthly_table, offenders_table

    except Exception as e:
        err = html.Div(f"Fehler: {e}", style={"color": C_NEG})
        return visible, f"Monatsdetail {year}", "", err, ""
