"""Page 5: ELO-Verteilung — Perzentil-Bänder aller FIDE-Spieler."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import pandas as pd
from dash import Input, Output, callback, dcc, html
import plotly.graph_objects as go

from data import HIGHLIGHT_COLORS, MAX_HIGHLIGHTS
from data_percentiles import SCOPES, load_percentiles
from data_player_history import load_player_history, load_player_meta

dash.register_page(__name__, path="/dist", name="ELO-Verteilung", title="ELO-Einsichten | ELO-Verteilung", order=2)

# (p_lo_col, p_hi_col, fillcolor, hover_label)
_BANDS = [
    ("p5",  "p10", "rgba(215,215,215,0.40)", "p5–p10"),
    ("p10", "p20", "rgba(200,200,200,0.45)", "p10–p20"),
    ("p20", "p30", "rgba(185,185,185,0.50)", "p20–p30"),
    ("p30", "p40", "rgba(170,170,170,0.55)", "p30–p40"),
    ("p40", "p50", "rgba(155,155,155,0.60)", "p40–p50"),
    ("p50", "p60", "rgba(155,155,155,0.60)", "p50–p60"),
    ("p60", "p70", "rgba(170,170,170,0.55)", "p60–p70"),
    ("p70", "p80", "rgba(185,185,185,0.50)", "p70–p80"),
    ("p80", "p90", "rgba(200,200,200,0.45)", "p80–p90"),
    ("p90", "p95", "rgba(210,210,210,0.40)", "p90–p95"),
    ("p95", "p98", "rgba(218,218,218,0.38)", "p95–p98"),
    ("p98", "p99", "rgba(225,225,225,0.35)", "p98–p99"),
]

# Visible quantile lines (p10 … p99), p50 solid
_QUANTILE_LINES = [
    ("p10", "p10"),
    ("p20", "p20"),
    ("p30", "p30"),
    ("p40", "p40"),
    ("p60", "p60"),
    ("p70", "p70"),
    ("p80", "p80"),
    ("p90", "p90"),
    ("p95", "p95"),
    ("p98", "p98"),
    ("p99", "p99"),
]

_SCOPE_OPTIONS = [
    {"label": "── Länder ──",     "value": "_sep1",   "disabled": True},
    {"label": "Deutschland (GER)", "value": "GER"},
    {"label": "Österreich (AUT)", "value": "AUT"},
    {"label": "Schweiz (SUI)",    "value": "SUI"},
    {"label": "── Kontinente ──",  "value": "_sep2",   "disabled": True},
    {"label": "Europa",           "value": "Europe"},
    {"label": "Weltweit",         "value": "World"},
]

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("ELO-Verteilung FIDE-Spieler — Perzentil-Bänder", className="page-title"),

        html.Div(
            "Ratingverteilung aller gerateten FIDE-Spieler (rating > 0) aus monatlichen "
            "FIDE-Snapshots ab 2009. Bänder zeigen p5–p80 in 10-Prozent-Schritten; "
            "gestrichelte Linie = Median (p50).",
            className="chart-info",
            style={"marginBottom": "16px"},
        ),

        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            "Datenbasis:",
                            style={"fontWeight": "600", "marginBottom": "6px", "display": "block"},
                        ),
                        dcc.Dropdown(
                            id="scope-dropdown-dist",
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
                            "Spieler hervorheben (max. 12):",
                            style={"fontWeight": "600", "marginBottom": "6px", "display": "block"},
                        ),
                        dcc.Dropdown(
                            id="player-dropdown-dist",
                            options=[],
                            value=[],
                            multi=True,
                            placeholder="Name eingeben (mind. 3 Zeichen) …",
                            clearable=True,
                            style={"fontSize": "13px"},
                        ),
                    ],
                    style={"flex": "1", "minWidth": "320px"},
                ),
            ],
            style={
                "display": "flex", "gap": "20px",
                "alignItems": "flex-start", "marginBottom": "16px",
            },
        ),

        dcc.Graph(
            id="elo-chart-dist",
            config={"displayModeBar": False},
            style={"height": "700px"},
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Module-level cache
# ---------------------------------------------------------------------------

_pct_cache: pd.DataFrame | None = None


def _get_pct() -> pd.DataFrame:
    global _pct_cache
    if _pct_cache is None:
        _pct_cache = load_percentiles()
    return _pct_cache


def _meta_to_options(df: pd.DataFrame) -> list[dict]:
    return [
        {"label": f"{r['name']} ({r['federation']}) — {r['last_rating']}",
         "value": int(r["fide_id"])}
        for _, r in df.iterrows()
    ]

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@callback(
    Output("player-dropdown-dist", "options"),
    Input("player-dropdown-dist", "search_value"),
    Input("player-dropdown-dist", "value"),
)
def search_players(search_value, selected_ids):
    meta = load_player_meta()
    selected_ids = selected_ids or []

    # Always keep already-selected players in options
    kept = meta[meta["fide_id"].isin(selected_ids)]
    result = _meta_to_options(kept)

    q = (search_value or "").strip()
    if len(q) < 3:
        return result

    matches = meta[
        meta["name"].str.contains(q, case=False, na=False, regex=False)
        & ~meta["fide_id"].isin(selected_ids)
    ].nlargest(30, "peak_rating")
    return result + _meta_to_options(matches)


@callback(
    Output("elo-chart-dist", "figure"),
    Input("scope-dropdown-dist", "value"),
    Input("player-dropdown-dist", "value"),
)
def update_chart(scope, selected_ids):
    if not scope or scope.startswith("_"):
        return go.Figure()

    pct = _get_pct()
    df = pct[pct["scope"] == scope].sort_values("period")
    df = df[df["p50"] >= 1000]  # remove corrupt ZIP rows (e.g. jul11frl.zip)
    if df.empty:
        return go.Figure()

    # Shift x by +1 month: Dec 2011 data appears at Jan 2012 (tick "2012")
    # Hover shows original period (the actual snapshot month)
    x = df["period"] + pd.DateOffset(months=1)
    orig = df["period"]  # for hover labels

    fig = go.Figure()

    # Invisible baseline at p5 (anchor for first fill)
    fig.add_trace(go.Scatter(
        x=x, y=df["p5"],
        mode="lines", line=dict(width=0),
        showlegend=False, hoverinfo="skip",
    ))

    # Filled bands stacked bottom-to-top
    for p_lo, p_hi, color, hover_label in _BANDS:
        fig.add_trace(go.Scatter(
            x=x, y=df[p_hi],
            mode="lines", line=dict(width=0),
            fill="tonexty", fillcolor=color,
            showlegend=False,
            customdata=orig,
            hovertemplate=f"<b>{hover_label}</b><br>Stand %{{customdata|%b %Y}}: %{{y:.0f}}<extra></extra>",
        ))

    # Quantile lines p10–p99
    for col, label in _QUANTILE_LINES:
        fig.add_trace(go.Scatter(
            x=x, y=df[col],
            mode="lines",
            line=dict(color="rgba(80,80,80,0.45)", width=0.8),
            name=label,
            showlegend=True,
            customdata=orig,
            hovertemplate=f"<b>{label}</b>: %{{y:.0f}}<br>Stand %{{customdata|%b %Y}}<extra></extra>",
        ))

    # Median (p50) — solid, more prominent
    fig.add_trace(go.Scatter(
        x=x, y=df["p50"],
        mode="lines",
        line=dict(color="rgba(40,40,40,0.85)", width=1.8),
        name="Median (p50)",
        customdata=orig,
        hovertemplate="Median p50: %{y:.0f}<br>Stand %{customdata|%b %Y}<extra></extra>",
    ))

    # Top N lines — solid, Akzentfarbe (Indigo), dicker als Quantil-Linien
    for col, label in [("top100", "Top 100"), ("top50", "Top 50"), ("top10", "Top 10"), ("top1", "Top 1")]:
        s = df[col].dropna()
        if s.empty:
            continue
        xi = x[s.index]
        oi = orig[s.index]
        fig.add_trace(go.Scatter(
            x=xi, y=s,
            mode="lines",
            line=dict(color="rgba(70,50,160,0.85)", width=1.5),
            name=label,
            customdata=oi,
            hovertemplate=f"<b>{label}</b>: %{{y:.0f}}<br>Stand %{{customdata|%b %Y}}<extra></extra>",
        ))

    # Player highlights
    meta = load_player_meta().set_index("fide_id")
    for i, fide_id in enumerate((selected_ids or [])[:MAX_HIGHLIGHTS]):
        player_df = load_player_history(fide_id)
        if player_df.empty:
            continue
        color = HIGHLIGHT_COLORS[i]
        if fide_id in meta.index:
            row = meta.loc[fide_id]
            name = row["name"]
        else:
            name = f"ID {fide_id}"
        px = player_df["period"] + pd.DateOffset(months=1)
        fig.add_trace(go.Scatter(
            x=px, y=player_df["rating"],
            mode="lines", line=dict(color=color, width=2.5),
            name=name,
            customdata=player_df["period"],
            hovertemplate=f"<b>{name}</b><br>Stand %{{customdata|%b %Y}}<br>ELO %{{y}}<extra></extra>",
        ))

    y_lo = 1400.0
    y_max = 2900.0

    scope_label = SCOPES.get(scope, {}).get("label", scope)
    n_latest = int(df["n_players"].iloc[-1]) if not df.empty else 0

    fig.update_layout(
        title=dict(
            text=f"ELO-Verteilung — {scope_label}  (zuletzt: {n_latest:,} Spieler)",
            font=dict(size=13), x=0.5,
        ),
        xaxis=dict(
            range=["2009-01-01", "2026-12-31"],
            title=None, tickformat="%Y", dtick="M12",
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        yaxis=dict(
            range=[1270, 2900],
            tick0=1300,
            dtick=100,
            title="ELO",
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        legend=dict(
            x=1.01, y=1, xanchor="left", font=dict(size=12),
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#ddd", borderwidth=1,
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=160, t=50, b=50),
        hovermode="closest",
    )

    return fig
