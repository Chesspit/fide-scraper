"""Page 5b: ELO-Verteilung Vergleich — zwei Länder/Kontinente."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import pandas as pd
from dash import Input, Output, callback, dcc, html
import plotly.graph_objects as go

from data_percentiles import SCOPES, load_percentiles

dash.register_page(__name__, path="/dist_b", name="ELO-Verteilung Vergleich", title="FIDE | ELO-Verteilung Vergleich")

# Filled bands for primary scope (grey)
_BANDS = [
    ("p5",  "p10", "rgba(215,215,215,0.40)"),
    ("p10", "p20", "rgba(200,200,200,0.45)"),
    ("p20", "p30", "rgba(185,185,185,0.50)"),
    ("p30", "p40", "rgba(170,170,170,0.55)"),
    ("p40", "p50", "rgba(155,155,155,0.60)"),
    ("p50", "p60", "rgba(155,155,155,0.60)"),
    ("p60", "p70", "rgba(170,170,170,0.55)"),
    ("p70", "p80", "rgba(185,185,185,0.50)"),
    ("p80", "p90", "rgba(200,200,200,0.45)"),
    ("p90", "p95", "rgba(210,210,210,0.40)"),
    ("p95", "p98", "rgba(218,218,218,0.38)"),
    ("p98", "p99", "rgba(225,225,225,0.35)"),
]

# Quantile lines for primary (dark grey) and secondary (terracotta)
_QUANTILE_COLS = ["p10", "p20", "p30", "p40", "p50", "p60", "p70", "p80", "p90", "p95", "p98", "p99"]

_COLOR_PRIMARY   = "rgba(60,60,60,0.55)"
_COLOR_SECONDARY = "rgba(190,60,30,0.75)"

_SCOPE_OPTIONS = [
    {"label": "── Länder ──",      "value": "_sep1",   "disabled": True},
    {"label": "Deutschland (GER)", "value": "GER"},
    {"label": "Österreich (AUT)",  "value": "AUT"},
    {"label": "Schweiz (SUI)",     "value": "SUI"},
    {"label": "── Kontinente ──",  "value": "_sep2",   "disabled": True},
    {"label": "Europa",            "value": "Europe"},
    {"label": "Weltweit",          "value": "World"},
]

_SCOPE_OPTIONS_B = [
    {"label": "– kein Vergleich –", "value": "_none"},
    *[o for o in _SCOPE_OPTIONS if not o.get("disabled")],
]

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("ELO-Verteilung — Ländervergleich", className="page-title"),

        html.Div(
            "Bänder zeigen die Ratingverteilung des ersten Landes (grau). "
            "Die farbigen Linien zeigen die Quantile des zweiten Landes zum direkten Vergleich.",
            className="chart-info",
            style={"marginBottom": "16px"},
        ),

        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            "Land 1 (Bänder):",
                            style={"fontWeight": "600", "marginBottom": "6px", "display": "block",
                                   "color": "#333"},
                        ),
                        dcc.Dropdown(
                            id="scope1-dropdown-distb",
                            options=_SCOPE_OPTIONS,
                            value="GER",
                            clearable=False,
                            style={"fontSize": "13px", "width": "220px"},
                        ),
                    ],
                ),
                html.Div(
                    [
                        html.Label(
                            "Land 2 (Vergleichslinien):",
                            style={"fontWeight": "600", "marginBottom": "6px", "display": "block",
                                   "color": "rgba(190,60,30,0.9)"},
                        ),
                        dcc.Dropdown(
                            id="scope2-dropdown-distb",
                            options=_SCOPE_OPTIONS_B,
                            value="_none",
                            clearable=False,
                            style={"fontSize": "13px", "width": "220px"},
                        ),
                    ],
                ),
            ],
            style={"display": "flex", "gap": "32px", "alignItems": "flex-start", "marginBottom": "16px"},
        ),

        dcc.Graph(
            id="elo-chart-distb",
            config={"displayModeBar": False},
            style={"height": "700px"},
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

_pct_cache: pd.DataFrame | None = None


def _get_pct() -> pd.DataFrame:
    global _pct_cache
    if _pct_cache is None:
        _pct_cache = load_percentiles()
    return _pct_cache


def _scope_df(scope: str) -> pd.DataFrame:
    pct = _get_pct()
    df = pct[pct["scope"] == scope].sort_values("period")
    return df[df["p50"] >= 1000]


# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


@callback(
    Output("elo-chart-distb", "figure"),
    Input("scope1-dropdown-distb", "value"),
    Input("scope2-dropdown-distb", "value"),
)
def update_chart(scope1, scope2):
    if not scope1 or scope1.startswith("_"):
        return go.Figure()

    df1 = _scope_df(scope1)
    if df1.empty:
        return go.Figure()

    x1 = df1["period"] + pd.DateOffset(months=1)
    orig1 = df1["period"]

    fig = go.Figure()

    # --- Primary: filled bands ---
    fig.add_trace(go.Scatter(
        x=x1, y=df1["p5"],
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))
    for p_lo, p_hi, color in _BANDS:
        fig.add_trace(go.Scatter(
            x=x1, y=df1[p_hi],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=color,
            showlegend=False,
            customdata=orig1,
            hovertemplate=f"<b>{SCOPES[scope1]['label']} {p_hi}</b>: %{{y:.0f}}<br>Stand %{{customdata|%b %Y}}<extra></extra>",
        ))

    # --- Primary: quantile lines (dark grey) ---
    label1 = SCOPES[scope1]["label"]
    for col in _QUANTILE_COLS:
        is_median = col == "p50"
        fig.add_trace(go.Scatter(
            x=x1, y=df1[col],
            mode="lines",
            line=dict(color=_COLOR_PRIMARY, width=1.6 if is_median else 0.8),
            name=f"{label1} {col}",
            legendgroup="primary",
            legendgrouptitle=dict(text=label1) if col == _QUANTILE_COLS[0] else None,
            showlegend=True,
            customdata=orig1,
            hovertemplate=f"<b>{label1} {col}</b>: %{{y:.0f}}<br>Stand %{{customdata|%b %Y}}<extra></extra>",
        ))

    # --- Secondary: quantile lines only (terracotta) ---
    if scope2 and not scope2.startswith("_"):
        df2 = _scope_df(scope2)
        if not df2.empty:
            x2 = df2["period"] + pd.DateOffset(months=1)
            orig2 = df2["period"]
            label2 = SCOPES[scope2]["label"]
            for col in _QUANTILE_COLS:
                is_median = col == "p50"
                fig.add_trace(go.Scatter(
                    x=x2, y=df2[col],
                    mode="lines",
                    line=dict(color=_COLOR_SECONDARY, width=1.8 if is_median else 1.0, dash="dot" if is_median else "solid"),
                    name=f"{label2} {col}",
                    legendgroup="secondary",
                    legendgrouptitle=dict(text=label2) if col == _QUANTILE_COLS[0] else None,
                    showlegend=True,
                    customdata=orig2,
                    hovertemplate=f"<b>{label2} {col}</b>: %{{y:.0f}}<br>Stand %{{customdata|%b %Y}}<extra></extra>",
                ))

    fig.update_layout(
        xaxis=dict(
            range=["2009-01-01", "2026-12-31"],
            title=None, tickformat="%Y", dtick="M12",
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        yaxis=dict(
            range=[1270, 2900],
            tick0=1300, dtick=100,
            title="ELO",
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        legend=dict(
            x=1.01, y=1, xanchor="left", font=dict(size=11),
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#ddd", borderwidth=1,
            groupclick="togglegroup",
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=180, t=30, b=50),
        hovermode="closest",
    )

    return fig
