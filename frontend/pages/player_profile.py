"""Spieler-Steckbrief — interaktiver Steckbrief für einen FIDE-Spieler."""
import os
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import dash_bootstrap_components as dbc
import pandas as pd
import plotly.graph_objects as go
import psycopg2
from dash import Input, Output, State, callback, dcc, html

dash.register_page(
    __name__,
    path="/player-profile",
    name="Spieler-Steckbrief",
    title="FIDE | Spieler-Steckbrief",
)

# ---------------------------------------------------------------------------
# Farben
# ---------------------------------------------------------------------------
C_BG      = "#F5F5F5"
C_CARD    = "#FFFFFF"
C_BORDER  = "#E0E0E0"
C_GRID    = "#EEEEEE"
C_PLOTBG  = "#FAFAFA"
C_LINE    = "#3A5F80"
C_BAR_Q   = "#8AAEC4"
C_MALE    = "#5B8DB8"
C_FEMALE  = "#E8A598"
C_POS     = "#7BC67E"
C_NEG     = "#E88080"
C_TEXT    = "#333333"
C_MUTED   = "#888888"
C_BOX_HDR = "#EEF3F7"

PLOTLY_BASE = dict(
    plot_bgcolor=C_PLOTBG,
    paper_bgcolor=C_CARD,
    font=dict(color=C_TEXT, size=11),
    margin=dict(l=44, r=12, t=28, b=36),
    xaxis=dict(gridcolor=C_GRID, linecolor=C_BORDER, zeroline=False),
    yaxis=dict(gridcolor=C_GRID, linecolor=C_BORDER, zeroline=False),
    legend=dict(bgcolor="rgba(0,0,0,0)", font=dict(size=10)),
)

CARD = {
    "backgroundColor": C_CARD,
    "border": f"1px solid {C_BORDER}",
    "borderRadius": "6px",
    "padding": "12px 16px",
    "marginBottom": "12px",
}

# ---------------------------------------------------------------------------
# Buckets
# ---------------------------------------------------------------------------
STR_BINS   = [-700, -200, -100, -30, 30, 100, 200, 700]
STR_LABELS = ["< −200", "−200…−100", "−100…−30", "±30", "+30…+100", "+100…+200", "> +200"]
AGE_BINS   = [0, 20, 30, 40, 50, 110]
AGE_LABELS = ["< 20", "20–30", "30–40", "40–50", "> 50"]
CLR_LABELS = ["Weiß", "Schwarz"]

# ---------------------------------------------------------------------------
# DB
# ---------------------------------------------------------------------------
def _db():
    return psycopg2.connect(os.getenv("DATABASE_URL", "postgresql://fide:nimzo194.@localhost:5434/fidedb"))


def search_players(q: str) -> list[dict]:
    """Return up to 25 matching players by name or FIDE-ID."""
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
        return [
            {"label": f"{r[1]} ({r[2]}, {r[3]})", "value": r[0]}
            for r in rows
        ]
    except Exception:
        return []


def load_player(fide_id: int) -> dict | None:
    try:
        conn = _db()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT fide_id, name, federation, sex, title, birth_year, std_rating "
                "FROM players WHERE fide_id = %s",
                (fide_id,),
            )
            row = cur.fetchone()
        conn.close()
        if not row:
            return None
        return dict(zip(["fide_id", "name", "federation", "sex", "title", "birth_year", "std_rating"], row))
    except Exception:
        return None


def load_rating_history(fide_id: int) -> pd.DataFrame:
    try:
        import warnings; warnings.filterwarnings("ignore")
        conn = _db()
        df = pd.read_sql(
            "SELECT period, std_rating::numeric AS rating FROM rating_history "
            "WHERE fide_id = %s ORDER BY period",
            conn, params=(fide_id,),
        )
        conn.close()
        df["period"] = pd.to_datetime(df["period"])
        return df
    except Exception:
        return pd.DataFrame(columns=["period", "rating"])


def load_games(fide_id: int) -> pd.DataFrame:
    try:
        import warnings; warnings.filterwarnings("ignore")
        conn = _db()
        df = pd.read_sql(
            """SELECT g.period, g.color,
                      g.result::numeric                 AS result,
                      g.rating_change_weighted::numeric AS delta,
                      g.opponent_rating::numeric        AS opp_rating,
                      COALESCE(g.opponent_sex, p.sex)   AS opp_sex,
                      p.birth_year                      AS opp_birth_year
               FROM game_results g
               LEFT JOIN players p ON p.fide_id = g.opponent_fide_id
               WHERE g.fide_id = %(id)s""",
            conn, params={"id": fide_id},
        )
        conn.close()
        df["period"] = pd.to_datetime(df["period"])
        df["year"]   = df["period"].dt.year
        return df
    except Exception:
        return pd.DataFrame()


# ---------------------------------------------------------------------------
# Chart helpers
# ---------------------------------------------------------------------------

def _fig(title="") -> go.Figure:
    fig = go.Figure()
    fig.update_layout(**PLOTLY_BASE, title=dict(text=title, font=dict(size=12, color=C_TEXT)))
    return fig


def _empty(msg="Keine Daten") -> go.Figure:
    fig = _fig()
    fig.add_annotation(text=msg, x=0.5, y=0.5, xref="paper", yref="paper",
                       showarrow=False, font=dict(color=C_MUTED, size=12))
    return fig


def make_count_bars(df, dim_col, labels) -> go.Figure:
    if df.empty:
        return _empty()
    fig = _fig()
    for sex, color, name in [("M", C_MALE, "Männer"), ("F", C_FEMALE, "Frauen")]:
        sub = df[df["opp_sex"] == sex] if sex in df["opp_sex"].values else pd.DataFrame()
        y = sub.groupby(dim_col, observed=True).size().reindex(labels, fill_value=0)
        fig.add_trace(go.Bar(name=name, x=labels, y=y.values,
                             marker_color=color, opacity=0.88))
    fig.update_layout(barmode="stack", yaxis_title="Partien",
                      legend=dict(orientation="h", y=1.12, x=0))
    return fig


def make_score_bars(df, dim_col, labels) -> go.Figure:
    if df.empty:
        return _empty()
    fig = _fig()
    for sex, color, name in [("M", C_MALE, "Männer"), ("F", C_FEMALE, "Frauen")]:
        sub = df[df["opp_sex"] == sex] if sex in df["opp_sex"].values else pd.DataFrame()
        y = sub.groupby(dim_col, observed=True)["result"].mean().reindex(labels) * 100
        fig.add_trace(go.Bar(name=name, x=labels, y=y.values,
                             marker_color=color, opacity=0.88))
    fig.add_hline(y=50, line_dash="dot", line_color=C_MUTED, line_width=1)
    fig.update_layout(barmode="group", yaxis_title="Score %",
                      yaxis=dict(range=[0, 105], gridcolor=C_GRID),
                      legend=dict(orientation="h", y=1.12, x=0))
    return fig


def make_delta_bars(df, dim_col, labels) -> go.Figure:
    if df.empty:
        return _empty()
    fig = _fig()
    for sex, name, opacity in [("M", "Männer", 0.88), ("F", "Frauen", 0.55)]:
        sub = df[df["opp_sex"] == sex] if sex in df["opp_sex"].values else pd.DataFrame()
        y = sub.groupby(dim_col, observed=True)["delta"].sum().reindex(labels, fill_value=0)
        fig.add_trace(go.Bar(
            name=name, x=labels, y=y.values,
            marker_color=[C_POS if v >= 0 else C_NEG for v in y.values],
            opacity=opacity,
        ))
    fig.add_hline(y=0, line_color=C_TEXT, line_width=0.8)
    fig.update_layout(barmode="group", yaxis_title="Σ Δ Elo",
                      legend=dict(orientation="h", y=1.12, x=0))
    return fig


def compute_buckets(df: pd.DataFrame, own_rating: float) -> pd.DataFrame:
    df = df.copy()
    df["rating_diff"] = df["opp_rating"] - own_rating
    df["str_bucket"]  = pd.cut(df["rating_diff"], bins=STR_BINS, labels=STR_LABELS, right=False)
    df["opp_age"]     = df["period"].dt.year - df["opp_birth_year"]
    df["age_bucket"]  = pd.cut(df["opp_age"], bins=AGE_BINS, labels=AGE_LABELS, right=False)
    df["color_label"] = df["color"].map({"W": "Weiß", "B": "Schwarz"})
    return df


def apply_filters(df, year_range, color_val, opp_elo, elo_dev, gender_val, age_range) -> pd.DataFrame:
    mask = (
        df["year"].between(year_range[0], year_range[1])
        & df["opp_rating"].between(opp_elo[0], opp_elo[1])
    )
    if "rating_diff" in df.columns:
        mask &= df["rating_diff"].between(elo_dev[0], elo_dev[1])
    if color_val != "Beide":
        mask &= df["color"] == ("W" if color_val == "Weiß" else "B")
    if gender_val != "Alle":
        mask &= df["opp_sex"] == ("M" if gender_val == "Männer" else "F")
    if "opp_age" in df.columns:
        mask &= df["opp_age"].between(age_range[0], age_range[1])
    return df[mask]


# ---------------------------------------------------------------------------
# Row builder for 3×3 grid (dimension = row, metric = column)
# ---------------------------------------------------------------------------

def _col_header(text):
    return html.Div(text, style={
        "textAlign": "center", "fontWeight": "600",
        "color": C_MUTED, "fontSize": "11px",
        "textTransform": "uppercase", "letterSpacing": "0.04em",
    })


def _metric_row(title, id_cnt, id_sc, id_dl):
    return html.Div([
        # Zeilen-Überschrift
        html.Div(title, style={
            "backgroundColor": C_BOX_HDR,
            "borderBottom": f"1px solid {C_BORDER}",
            "padding": "6px 12px",
            "fontWeight": "600",
            "fontSize": "12px",
            "color": C_TEXT,
            "letterSpacing": "0.03em",
        }),
        dbc.Row([
            dbc.Col(dcc.Graph(id=id_cnt, config={"displayModeBar": False},
                              style={"height": "200px"}), width=4),
            dbc.Col(dcc.Graph(id=id_sc,  config={"displayModeBar": False},
                              style={"height": "200px"}), width=4),
            dbc.Col(dcc.Graph(id=id_dl,  config={"displayModeBar": False},
                              style={"height": "200px"}), width=4),
        ], className="g-0"),
    ], style={
        "border": f"1px solid {C_BORDER}",
        "borderRadius": "6px",
        "marginBottom": "10px",
        "overflow": "hidden",
    })


# ---------------------------------------------------------------------------
# KPI helper
# ---------------------------------------------------------------------------

def _kpi(label, value):
    return html.Div([
        html.Div(value, style={"fontSize": "17px", "fontWeight": "700", "color": C_TEXT,
                               "lineHeight": "1.1"}),
        html.Div(label, style={"fontSize": "9px", "color": C_MUTED,
                               "textTransform": "uppercase", "letterSpacing": "0.06em"}),
    ], style={"textAlign": "center", "minWidth": "52px"})


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------
layout = html.Div(
    style={"backgroundColor": C_BG, "minHeight": "100vh", "padding": "16px"},
    children=[
        dcc.Store(id="pp-games-store"),
        dcc.Store(id="pp-player-store"),

        # ── 0. Suche + Header inline ──────────────────────────────────────
        html.Div([
            dbc.Row([
                # Suche
                dbc.Col([
                    dcc.Dropdown(
                        id="pp-search-dd",
                        placeholder="Name oder FIDE-ID (min. 2 Zeichen)…",
                        searchable=True,
                        options=[],
                        value=None,
                        clearable=True,
                        style={"fontSize": "13px", "minWidth": "340px"},
                    ),
                ], width="auto"),
                dbc.Col(
                    dbc.Button("Laden", id="pp-load-btn", color="secondary",
                               size="sm", style={"whiteSpace": "nowrap"}),
                    width="auto",
                ),
                # Header-Info — erscheint nach Laden
                dbc.Col(html.Div(id="pp-header-inline"), width=True),
                dbc.Col(html.Div(id="pp-load-error",
                                 style={"color": C_NEG, "fontSize": "12px",
                                        "paddingTop": "6px"}),
                        width="auto"),
            ], align="center", className="g-2"),
        ], style={**CARD, "padding": "10px 14px"}),

        # ── 2. Rating-Verlauf ─────────────────────────────────────────────
        html.Div(
            dcc.Graph(id="pp-rating-chart", config={"displayModeBar": False},
                      style={"height": "240px"}),
            style=CARD,
        ),

        # ── 3. Partien pro Quartal + Jahrestotal ─────────────────────────
        html.Div(
            dcc.Graph(id="pp-quarterly-chart", config={"displayModeBar": False},
                      style={"height": "220px"}),
            style=CARD,
        ),

        # ── 4. Filter-Leiste (3 Gruppen à 2 Filter) ──────────────────────
        html.Div([
            html.Div("Filter", style={
                "fontWeight": "600", "color": C_MUTED, "fontSize": "10px",
                "textTransform": "uppercase", "letterSpacing": "0.05em",
                "marginBottom": "8px",
            }),
            dbc.Row([
                # Gruppe 1: Jahr + Alter
                dbc.Col([
                    html.Div("Zeitraum & Alter", style={"fontSize": "10px", "color": C_MUTED,
                             "marginBottom": "6px", "fontWeight": "600"}),
                    html.Div([
                        html.Label("Jahrbereich", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.RangeSlider(
                            id="pp-year-slider", min=2008, max=2026, step=1,
                            value=[2008, 2026],
                            marks={y: str(y) for y in range(2008, 2027, 4)},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ], style={"marginBottom": "8px"}),
                    html.Div([
                        html.Label("Gegner-Alter", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.RangeSlider(
                            id="pp-age-slider", min=10, max=70, step=5,
                            value=[10, 70],
                            marks={v: str(v) for v in [10, 20, 30, 40, 50, 60, 70]},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ]),
                ], width=4, style={"borderRight": f"1px solid {C_BORDER}", "paddingRight": "20px"}),

                # Gruppe 2: Farbe + Geschlecht
                dbc.Col([
                    html.Div("Farbe & Geschlecht", style={"fontSize": "10px", "color": C_MUTED,
                             "marginBottom": "6px", "fontWeight": "600"}),
                    html.Div([
                        html.Label("Eigene Farbe", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.Dropdown(
                            id="pp-color-filter",
                            options=[{"label": l, "value": l} for l in ["Beide", "Weiß", "Schwarz"]],
                            value="Beide", clearable=False, style={"fontSize": "12px"},
                        ),
                    ], style={"marginBottom": "8px"}),
                    html.Div([
                        html.Label("Gegner-Geschlecht", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.Dropdown(
                            id="pp-gender-filter",
                            options=[{"label": l, "value": l} for l in ["Alle", "Männer", "Frauen"]],
                            value="Alle", clearable=False, style={"fontSize": "12px"},
                        ),
                    ]),
                ], width=2, style={"borderRight": f"1px solid {C_BORDER}",
                                   "paddingLeft": "20px", "paddingRight": "20px"}),

                # Gruppe 3: Gegner-ELO + ELO-Abweichung
                dbc.Col([
                    html.Div("Gegner-ELO", style={"fontSize": "10px", "color": C_MUTED,
                             "marginBottom": "6px", "fontWeight": "600"}),
                    html.Div([
                        html.Label("Gegner-Elo (absolut)", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.RangeSlider(
                            id="pp-opp-elo-slider", min=1600, max=2700, step=50,
                            value=[1600, 2700],
                            marks={v: str(v) for v in [1600, 1800, 2000, 2200, 2400, 2700]},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ], style={"marginBottom": "8px"}),
                    html.Div([
                        html.Label("Abw. zum eigenen Elo", style={"fontSize": "11px", "color": C_MUTED}),
                        dcc.RangeSlider(
                            id="pp-elo-dev-slider", min=-400, max=400, step=50,
                            value=[-400, 400],
                            marks={v: str(v) for v in [-400, -200, 0, 200, 400]},
                            tooltip={"placement": "bottom", "always_visible": False},
                        ),
                    ]),
                ], width=6, style={"paddingLeft": "20px"}),
            ], className="g-0"),
        ], style={**CARD, "backgroundColor": "#EEF3F7", "padding": "14px 16px"}),

        # ── 5–7. Ergebnis-Charts 3×3 (Zeile=Dimension, Spalte=Metrik) ────
        html.Div([
            # Spalten-Header
            dbc.Row([
                dbc.Col(html.Div(), width={"size": 0}),
                dbc.Col(_col_header("Anzahl Partien"), width=4),
                dbc.Col(_col_header("Score %"),        width=4),
                dbc.Col(_col_header("Σ Δ Elo"),        width=4),
            ], className="mb-1"),

            _metric_row("Nach Spielstärke",
                        "pp-cnt-str", "pp-sc-str", "pp-dl-str"),
            _metric_row("Nach Altersklasse",
                        "pp-cnt-age", "pp-sc-age", "pp-dl-age"),
            _metric_row("Nach Farbe",
                        "pp-cnt-clr", "pp-sc-clr", "pp-dl-clr"),
        ], style={**CARD, "padding": "14px 16px"}),
    ],
)


# ---------------------------------------------------------------------------
# Callback: Live-Suche
# ---------------------------------------------------------------------------

@callback(
    Output("pp-search-dd", "options"),
    Input("pp-search-dd", "search_value"),
)
def update_search_options(q):
    return search_players(q or "")


# ---------------------------------------------------------------------------
# Callback: Spieler laden
# ---------------------------------------------------------------------------

@callback(
    Output("pp-header-inline",   "children"),
    Output("pp-rating-chart",    "figure"),
    Output("pp-quarterly-chart", "figure"),
    Output("pp-games-store",     "data"),
    Output("pp-player-store",    "data"),
    Output("pp-load-error",      "children"),
    Output("pp-year-slider",     "min"),
    Output("pp-year-slider",     "max"),
    Output("pp-year-slider",     "value"),
    Input("pp-load-btn", "n_clicks"),
    State("pp-search-dd", "value"),
    prevent_initial_call=False,
)
def load_player_data(n_clicks, fide_id_val):
    fide_id = int(fide_id_val) if fide_id_val else 4631234

    player = load_player(fide_id)
    if not player:
        return (
            html.Span("Nicht gefunden", style={"color": C_NEG}),
            _empty("Kein Spieler"),
            _empty(),
            None, None,
            f"FIDE-ID {fide_id} nicht gefunden.",
            2008, 2026, [2008, 2026],
        )

    rh    = load_rating_history(fide_id)
    games = load_games(fide_id)

    # ── Header ────────────────────────────────────────────────────────────
    age       = date.today().year - int(player["birth_year"]) if player["birth_year"] else "?"
    title_str = player["title"] or ""
    header = html.Div([
        html.Span(player["name"],
                  style={"fontSize": "16px", "fontWeight": "700",
                         "color": C_TEXT, "marginRight": "12px"}),
        html.Span(title_str,
                  style={"fontSize": "12px", "color": C_MUTED, "marginRight": "16px"}),
        _kpi("Elo",    str(player["std_rating"] or "–")),
        html.Span("·", style={"color": C_BORDER, "margin": "0 8px", "fontSize": "18px"}),
        _kpi("Alter",  str(age)),
        html.Span("·", style={"color": C_BORDER, "margin": "0 8px", "fontSize": "18px"}),
        _kpi("Föd.",   player["federation"] or "–"),
        html.Span("·", style={"color": C_BORDER, "margin": "0 8px", "fontSize": "18px"}),
        _kpi("Partien (DB)", f"{len(games):,}"),
    ], style={"display": "flex", "alignItems": "center", "flexWrap": "wrap", "gap": "4px"})

    # ── Rating-Verlauf: forward-fill auf monatlichem Raster ───────────────
    fig_r = _fig()
    if not rh.empty:
        # Vollständigen Monatsraster aufspannen
        full_idx = pd.date_range(rh["period"].min(), rh["period"].max(), freq="MS")
        rh_full  = rh.set_index("period").reindex(full_idx)
        rh_full["rating_ffill"] = rh_full["rating"].ffill()
        # Originaldaten-Punkte markieren (für spätere Tooltip-Unterscheidung)
        rh_full["is_actual"] = rh_full["rating"].notna()
        # Linie über forward-gefüllte Werte
        fig_r.add_trace(go.Scatter(
            x=rh_full.index, y=rh_full["rating_ffill"],
            mode="lines",
            line=dict(color=C_LINE, width=2),
            name="Elo-Rating",
        ))
        yr_min = rh["period"].dt.year.min()
        yr_max = rh["period"].dt.year.max()
        fig_r.update_xaxes(
            tickvals=[pd.Timestamp(f"{y}-01-01") for y in range(yr_min, yr_max + 1)],
            ticktext=[str(y) for y in range(yr_min, yr_max + 1)],
            tickangle=-45,
        )
    fig_r.update_layout(yaxis_title="Elo", showlegend=False,
                        margin=dict(l=44, r=12, t=20, b=48))

    # ── Partien: Q1/Q2/Q3/Q4 + Jahrestotal nach Q4 ───────────────────────
    fig_q = _fig()
    if not games.empty:
        games["q"] = games["period"].dt.quarter
        qy = games.groupby(["year", "q"]).size().reset_index(name="n")
        yearly = games.groupby("year").size().reset_index(name="n")

        x_labels, x_tick, y_vals, bar_colors, text_vals = [], [], [], [], []
        q_names = {1: "Q1", 2: "Q2", 3: "Q3", 4: "Q4"}

        for yr in sorted(games["year"].unique()):
            for q in [1, 2, 3, 4]:
                key = f"{yr}-Q{q}"
                n = qy[(qy["year"] == yr) & (qy["q"] == q)]["n"].sum()
                x_labels.append(key)
                x_tick.append(q_names[q])
                y_vals.append(int(n))
                bar_colors.append(C_BAR_Q)
                text_vals.append(str(int(n)) if n > 0 else "")
            # Jahrestotal nach Q4
            yr_total = int(yearly[yearly["year"] == yr]["n"].values[0]) if yr in yearly["year"].values else 0
            x_labels.append(f"{yr}-Y")
            x_tick.append(str(yr))
            y_vals.append(yr_total)
            bar_colors.append("#5A7FA0")   # dunklerer Blauton für Jahresbalken
            text_vals.append(str(yr_total))

        fig_q.add_trace(go.Bar(
            x=x_labels, y=y_vals,
            marker_color=bar_colors,
            text=text_vals,
            textposition="outside",
            textfont=dict(size=8),
            showlegend=False,
        ))
        fig_q.update_xaxes(
            tickvals=x_labels,
            ticktext=x_tick,
            tickangle=-45,
            tickfont=dict(size=9),
        )
    fig_q.update_layout(
        yaxis_title="Partien", showlegend=False,
        margin=dict(l=44, r=12, t=24, b=52),
        title=dict(text="Partien pro Quartal  (dunkler Balken = Jahrestotal)", font=dict(size=11)),
        bargap=0.15,
    )

    # Jahres-Slider-Grenzen
    yr_lo = int(games["year"].min()) if not games.empty else 2008
    yr_hi = int(games["year"].max()) if not games.empty else 2026

    # Games serialisieren
    g = games[["period", "year", "color", "result", "delta",
               "opp_rating", "opp_sex", "opp_birth_year"]].copy()
    g["period"] = g["period"].dt.strftime("%Y-%m-%d")
    g = g.where(g.notna(), None)

    return (
        header,
        fig_r, fig_q,
        g.to_dict("records"),
        player,
        "",
        yr_lo, yr_hi, [yr_lo, yr_hi],
    )


# ---------------------------------------------------------------------------
# Callback: Ergebnis-Charts
# ---------------------------------------------------------------------------

@callback(
    Output("pp-cnt-str", "figure"),
    Output("pp-cnt-age", "figure"),
    Output("pp-cnt-clr", "figure"),
    Output("pp-sc-str",  "figure"),
    Output("pp-sc-age",  "figure"),
    Output("pp-sc-clr",  "figure"),
    Output("pp-dl-str",  "figure"),
    Output("pp-dl-age",  "figure"),
    Output("pp-dl-clr",  "figure"),
    Input("pp-games-store",    "data"),
    Input("pp-player-store",   "data"),
    Input("pp-year-slider",    "value"),
    Input("pp-color-filter",   "value"),
    Input("pp-opp-elo-slider", "value"),
    Input("pp-elo-dev-slider", "value"),
    Input("pp-gender-filter",  "value"),
    Input("pp-age-slider",     "value"),
)
def update_charts(games_data, player_data, year_range, color_val,
                  opp_elo, elo_dev, gender_val, age_range):
    e9 = tuple(_empty() for _ in range(9))
    if not games_data or not player_data:
        return e9

    df = pd.DataFrame(games_data)
    if df.empty:
        return e9

    df["period"] = pd.to_datetime(df["period"])
    df["year"]   = df["period"].dt.year
    df["opp_birth_year"] = pd.to_numeric(df["opp_birth_year"], errors="coerce")

    own = float(player_data.get("std_rating") or 2000)
    df  = compute_buckets(df, own)
    df  = apply_filters(df, year_range, color_val, opp_elo, elo_dev, gender_val, age_range)

    if df.empty:
        return e9

    return (
        make_count_bars(df, "str_bucket",  STR_LABELS),
        make_count_bars(df, "age_bucket",  AGE_LABELS),
        make_count_bars(df, "color_label", CLR_LABELS),
        make_score_bars(df, "str_bucket",  STR_LABELS),
        make_score_bars(df, "age_bucket",  AGE_LABELS),
        make_score_bars(df, "color_label", CLR_LABELS),
        make_delta_bars(df, "str_bucket",  STR_LABELS),
        make_delta_bars(df, "age_bucket",  AGE_LABELS),
        make_delta_bars(df, "color_label", CLR_LABELS),
    )
