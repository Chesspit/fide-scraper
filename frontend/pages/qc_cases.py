"""QC Fälle — Fall-Explorer für Warn/Error-Fenster mit Spieler-Drill-Down."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
from dash import Input, Output, State, callback, dash_table, dcc, html

import data_qc

dash.register_page(
    __name__,
    path="/qc-cases",
    name="QC Fälle",
    title="ELO-Einsichten | QC Fälle",
    order=22,
)

C_BG     = "#F5F5F5"
C_CARD   = "#FFFFFF"
C_BORDER = "#E0E0E0"
C_TEXT   = "#333333"
C_MUTED  = "#888888"
C_NEG    = "#E88080"

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

FLAG_COLORS = [
    {"if": {"filter_query": '{Flag} = "error"'}, "backgroundColor": "#FFF0F0", "color": "#C62828"},
    {"if": {"filter_query": '{Flag} = "warn"'},  "backgroundColor": "#FFFDE7", "color": "#E65100"},
]

CATEGORY_BADGE_COLORS = {
    "struktur_2008":     "#90A4AE",
    "fehlende_perioden": "#7986CB",
    "spiegel_delta":     "#4A7AB5",
    "korrektur_rest":    "#9575CD",
    "k40_verdacht":      "#F9A825",
    "unerklaert":        "#C62828",
}

_TABLE_STYLE = dict(
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
)


def _filter_card(label: str, component) -> dbc.Col:
    return dbc.Col(
        html.Div(style=CARD, children=[
            html.Div(label, style={"fontSize": "0.78rem", "color": C_MUTED,
                                   "marginBottom": "4px"}),
            component,
        ]),
        md=2,
    )


_YEAR_OPTIONS = [{"label": "Alle", "value": 0}] + [
    {"label": str(y), "value": y} for y in range(2026, 2005, -1)
]
_CATEGORY_OPTIONS = [{"label": "Alle", "value": ""}] + [
    {"label": lbl, "value": cat} for cat, lbl in data_qc.CATEGORY_LABELS.items()
]
_FLAG_OPTIONS = [
    {"label": "Warn + Error", "value": ""},
    {"label": "Nur Error", "value": "error"},
    {"label": "Nur Warn", "value": "warn"},
]

_DD_STYLE = {"fontSize": "0.85rem"}

layout = dbc.Container(
    fluid=True,
    style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "20px"},
    children=[
        html.H4("QC Fälle — Warn/Error-Fenster im Detail",
                style={"marginBottom": "16px", "color": C_TEXT}),

        # Filterzeile
        dbc.Row([
            _filter_card("Jahr", dcc.Dropdown(
                id="qcc-year-dd", options=_YEAR_OPTIONS, value=0,
                clearable=False, style=_DD_STYLE)),
            _filter_card("Kategorie", dcc.Dropdown(
                id="qcc-category-dd", options=_CATEGORY_OPTIONS, value="",
                clearable=False, style=_DD_STYLE)),
            _filter_card("Flag", dcc.Dropdown(
                id="qcc-flag-dd", options=_FLAG_OPTIONS, value="",
                clearable=False, style=_DD_STYLE)),
            _filter_card("Gruppe", dcc.Dropdown(
                id="qcc-group-dd", options=data_qc.get_group_options(), value="all",
                clearable=False, style=_DD_STYLE)),
            _filter_card("Föderation", dcc.Dropdown(
                id="qcc-fed-dd", options=data_qc.get_case_federation_options(), value="",
                clearable=False, style=_DD_STYLE)),
            _filter_card("Name", dcc.Input(
                id="qcc-name-input", type="text", debounce=True, placeholder="Suche…",
                style={"width": "100%", "fontSize": "0.85rem", "padding": "6px",
                       "border": f"1px solid {C_BORDER}", "borderRadius": "4px"})),
        ], className="g-2"),

        # Fall-Tabelle
        html.Div(style=CARD, children=[
            html.Div(id="qcc-cases-count",
                     style={"fontSize": "0.85rem", "color": C_MUTED, "marginBottom": "8px"}),
            html.Div(id="qcc-cases-table"),
        ]),

        # Spieler-Detail
        html.Div(
            id="qcc-detail-section",
            style={"display": "none"},
            children=[
                html.Div(style=CARD, children=[
                    html.Div(id="qcc-detail-header", style={"marginBottom": "10px"}),
                    html.Div(id="qcc-detail-explanation",
                             style={"fontSize": "0.85rem", "color": C_MUTED,
                                    "marginBottom": "10px"}),
                    html.Div(id="qcc-detail-table"),
                    html.Div(id="qcc-detail-checksum", style={"marginTop": "12px"}),
                ]),
            ],
        ),
    ],
)


# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------

_CASE_COLUMNS = [
    {"name": "Spieler",   "id": "Spieler"},
    {"name": "Föd",       "id": "Föd"},
    {"name": "Gruppe",    "id": "Gruppe"},
    {"name": "Fenster",   "id": "Fenster"},
    {"name": "Von-ELO",   "id": "Von-ELO"},
    {"name": "Nach-ELO",  "id": "Nach-ELO"},
    {"name": "Erwartet",  "id": "Erwartet"},
    {"name": "Gescrapt",  "id": "Gescrapt"},
    {"name": "Korr",      "id": "Korr"},
    {"name": "Δadj",      "id": "Δadj"},
    {"name": "Fehlend",   "id": "Fehlend"},
    {"name": "Flag",      "id": "Flag"},
    {"name": "Kategorie", "id": "Kategorie"},
]


@callback(
    Output("qcc-cases-table", "children"),
    Output("qcc-cases-count", "children"),
    Input("qcc-year-dd",     "value"),
    Input("qcc-category-dd", "value"),
    Input("qcc-flag-dd",     "value"),
    Input("qcc-group-dd",    "value"),
    Input("qcc-fed-dd",      "value"),
    Input("qcc-name-input",  "value"),
)
def update_cases(year, category, flag, group, federation, name_filter):
    try:
        df = data_qc.load_cases(
            year=int(year) if year else None,
            category=category or "",
            flag=flag or "",
            group=group or "all",
            federation=federation or "",
            name_filter=name_filter or "",
        )
        if df.empty:
            return html.Div("Keine Fälle für diese Filter.", style={"color": C_MUTED}), ""

        df = df.rename(columns={
            "fide_id": "FIDE-ID", "name": "Spieler", "fed": "Föd", "gruppe": "Gruppe",
            "fenster": "Fenster", "published_start": "Von-ELO", "published_end": "Nach-ELO",
            "expected_change": "Erwartet", "scraped_change": "Gescrapt",
            "correction": "Korr", "delta_adj": "Δadj", "missing_periods": "Fehlend",
            "flag": "Flag", "category": "Kategorie",
        })
        df["Kategorie"] = df["Kategorie"].map(data_qc.CATEGORY_LABELS).fillna("–")

        count_txt = (f"{len(df)} Fälle (max. 500, sortiert nach |Δadj|) — "
                     "Zeile klicken für Spieler-Monatszerlegung")
        table = dash_table.DataTable(
            id="qcc-cases-datatable",
            data=df.to_dict("records"),
            columns=_CASE_COLUMNS,
            row_selectable="single",
            page_size=25,
            page_action="native",
            style_cell_conditional=[
                {"if": {"column_id": c}, "textAlign": "left"}
                for c in ("Spieler", "Föd", "Gruppe", "Fenster", "Flag", "Kategorie")
            ],
            style_data_conditional=FLAG_COLORS + [
                {"if": {"state": "selected"},
                 "backgroundColor": "#E3F2FD", "border": "1px solid #90CAF9"},
            ],
            **_TABLE_STYLE,
        )
        return table, count_txt
    except Exception as e:
        return html.Div(f"Fehler: {e}", style={"color": C_NEG}), ""


@callback(
    Output("qcc-detail-section",     "style"),
    Output("qcc-detail-header",      "children"),
    Output("qcc-detail-explanation", "children"),
    Output("qcc-detail-table",       "children"),
    Output("qcc-detail-checksum",    "children"),
    Input("qcc-cases-datatable", "selected_rows"),
    State("qcc-cases-datatable", "data"),
)
def update_player_detail(selected_rows, table_data):
    hidden  = {"display": "none"}
    visible = {"display": "block"}

    if not selected_rows or not table_data:
        return hidden, "", "", "", ""

    row = table_data[selected_rows[0]]
    fide_id = row.get("FIDE-ID")
    fenster = row.get("Fenster", "")
    if not fide_id or "→" not in fenster:
        return hidden, "", "", "", ""
    year = int(fenster.split("→")[1].strip()[:4])

    try:
        detail = data_qc.load_player_qc_detail(int(fide_id), year)
        info = detail["info"]

        # Kategorie-Badge (Rohwert über Label zurückfinden)
        label_to_cat = {v: k for k, v in data_qc.CATEGORY_LABELS.items()}
        cat = label_to_cat.get(row.get("Kategorie", ""), "")
        badge = html.Span(
            row.get("Kategorie", "–"),
            style={"backgroundColor": CATEGORY_BADGE_COLORS.get(cat, C_MUTED),
                   "color": "#FFFFFF", "borderRadius": "4px", "padding": "2px 10px",
                   "fontSize": "0.8rem", "fontWeight": "600", "marginLeft": "12px"},
        )
        header = html.Div([
            html.Span(f"{info.get('name', '?')} ", style={"fontSize": "1.05rem",
                                                          "fontWeight": "700"}),
            html.Span(f"(FIDE-ID {fide_id}, {info.get('federation') or '–'}, "
                      f"Gruppe: {info.get('gruppe') or '–'}) — Jahr {year}",
                      style={"color": C_MUTED, "fontSize": "0.9rem"}),
            badge,
        ])
        explanation = data_qc.CATEGORY_EXPLANATIONS.get(cat, "")

        if not detail["columns"]:
            return (visible, header, explanation,
                    html.Div("Zu wenige Rating-Snapshots für eine Zerlegung.",
                             style={"color": C_MUTED}), "")

        columns = [{"name": "", "id": "Zeile"}] + [
            {"name": c, "id": c} for c in detail["columns"]
        ]
        # Auffällige Zellen: |Unerklärtes Δ| > 5 rot
        conditional = [
            {"if": {"column_id": "Zeile"},
             "textAlign": "left", "fontWeight": "600", "backgroundColor": "#F7F7F7"},
        ]
        unexp_row = detail["rows"][3]
        for c in detail["columns"]:
            v = unexp_row.get(c)
            if isinstance(v, (int, float)) and abs(v) > 5:
                conditional.append({
                    "if": {"column_id": c, "filter_query": '{Zeile} = "Unerklärtes Δ"'},
                    "backgroundColor": "#FFF0F0", "color": "#C62828", "fontWeight": "600",
                })

        detail_table = dash_table.DataTable(
            data=detail["rows"],
            columns=columns,
            style_data_conditional=conditional,
            page_action="none",
            **_TABLE_STYLE,
        )

        cs = detail["checksum"]
        if cs is not None:
            ok = abs(cs) <= 3
            bg, border = ("#E8F5E9", "#A5D6A7") if ok else ("#FFF0F0", "#FFCDD2")
            checksum_banner = html.Div(
                style={"backgroundColor": bg, "borderRadius": "6px",
                       "border": f"1px solid {border}", "padding": "10px 14px"},
                children=[
                    html.Span("Jahres-Prüfsumme ", style={"fontWeight": "600"}),
                    html.Span(f"{detail['from_label']} → {detail['to_label']}: "
                              f"{detail['pub_start']} + Partien + Korrekturen − "
                              f"{detail['pub_end']} = "),
                    html.Span(f"{cs:+.1f}", style={"fontWeight": "700"}),
                    html.Span("  (|Δ| ≤ 3 = OK)", style={"color": C_MUTED,
                                                         "marginLeft": "8px"}),
                ],
            )
        else:
            checksum_banner = ""

        return visible, header, explanation, detail_table, checksum_banner

    except Exception as e:
        return visible, "", "", html.Div(f"Fehler: {e}", style={"color": C_NEG}), ""
