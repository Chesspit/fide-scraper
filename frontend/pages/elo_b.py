"""Page B: ELO 2700+ Spieler — Rating-Progression mit Perzentil-Bändern."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import pandas as pd
from dash import Input, Output, callback, dcc, html
import plotly.graph_objects as go

from data import DEFAULT_PLAYER_IDS, HIGHLIGHT_COLORS, MAX_HIGHLIGHTS, load_2700_history

dash.register_page(__name__, path="/b", name="Version B", title="FIDE | Version B")

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("ELO 2700+ Spieler — Rating-Progression", className="page-title"),

        html.Div(
            "Die grauen Flächen zeigen die Verteilung der ELO-Zahlen aller 128 Spieler, "
            "die seit 2009 jemals ≥ 2700 erreicht haben (nur Perioden mit ELO ≥ 2600 berücksichtigt). "
            "Das mittlere Band (p30–p70) umfasst die mittleren 40 % der Spieler, "
            "die äusseren Bänder je 20 %. Die gepunktete Linie zeigt den Median. "
            "Die farbigen Linien entsprechen den im Dropdown ausgewählten Spielern.",
            className="chart-info",
        ),

        # Dropdown
        html.Div(
            [
                html.Label(
                    "Spieler hervorheben (max. 12):",
                    style={"fontWeight": "600", "marginBottom": "6px", "display": "block"},
                ),
                dcc.Dropdown(
                    id="player-dropdown-b",
                    options=[],
                    value=DEFAULT_PLAYER_IDS,
                    multi=True,
                    placeholder="Spieler hinzufügen…",
                    clearable=False,
                    style={"fontSize": "13px"},
                ),
            ],
            style={"marginBottom": "10px"},
        ),


        dcc.Graph(
            id="elo-chart-b",
            config={"displayModeBar": False},
            style={"height": "600px"},
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BAND_CONFIGS = [
    (0.05, 0.30, "rgba(210,210,210,0.65)", "p5–p30"),
    (0.30, 0.70, "rgba(180,180,180,0.70)", "p30–p70 (mittlere 40 %)"),
    (0.70, 0.95, "rgba(210,210,210,0.65)", "p70–p95"),
]


def _background_traces(df):
    """Bands + median, computed over all players with ELO >= 2600."""
    df = df[df["rating"] >= 2600]
    grp = df.groupby("period")["rating"]
    counts = grp.count()
    qs = [0.05, 0.10, 0.30, 0.50, 0.70, 0.90, 0.95]
    pct = grp.quantile(qs).unstack()
    pct.columns = qs
    pct = pct[counts >= 5]
    pct = pct.rolling(window=3, center=True, min_periods=1).mean()
    periods = list(pct.index)

    traces = []
    for q_lo, q_hi, color, label in BAND_CONFIGS:
        y_hi = pct[q_hi].tolist()
        y_lo = pct[q_lo].tolist()
        traces.append(go.Scatter(
            x=periods + periods[::-1],
            y=y_hi + y_lo[::-1],
            fill="toself",
            fillcolor=color,
            line=dict(width=0),
            name=label,
            showlegend=False,
            hoverinfo="skip",
        ))

    traces.append(go.Scatter(
        x=periods,
        y=pct[0.50].tolist(),
        mode="lines",
        line=dict(color="rgba(100,100,100,0.8)", width=1.5),
        name="Median (p50)",
        showlegend=False,
        hovertemplate="Median p50: %{y:.0f}<br>%{x|%b %Y}<extra></extra>",
    ))

    return traces


def _all_options():
    df = load_2700_history()
    meta = df[["fide_id", "name", "federation"]].drop_duplicates("fide_id").sort_values("name")
    return [
        {"label": r['name'], "value": int(r["fide_id"])}
        for _, r in meta.iterrows()
    ]

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@callback(
    Output("player-dropdown-b", "options"),
    Output("player-dropdown-b", "value"),
    Input("player-dropdown-b", "id"),
)
def populate_options(_):
    return _all_options(), DEFAULT_PLAYER_IDS



@callback(
    Output("elo-chart-b", "figure"),
    Input("player-dropdown-b", "value"),
)
def update_chart(selected_ids):
    df = load_2700_history()
    selected_ids = selected_ids or []

    fig = go.Figure()

    for trace in _background_traces(df):
        fig.add_trace(trace)

    id_to_name = df[["fide_id", "name"]].drop_duplicates("fide_id").set_index("fide_id")["name"].to_dict()
    for i, fide_id in enumerate(selected_ids[:MAX_HIGHLIGHTS]):
        player_df = df[df["fide_id"] == fide_id].sort_values("period")
        if player_df.empty:
            continue
        color = HIGHLIGHT_COLORS[i]
        name = id_to_name.get(fide_id, f"ID {fide_id}")
        birth_year = player_df["birth_year"].iloc[0]
        label = f"{name} (*{int(birth_year)})" if pd.notna(birth_year) else name
        fig.add_trace(go.Scatter(
            x=player_df["period"],
            y=player_df["rating"],
            mode="lines",
            line=dict(color=color, width=2.5),
            name=label,
            hovertemplate=f"<b>{name}</b><br>%{{x|%b %Y}}<br>ELO %{{y}}<extra></extra>",
        ))

    fig.update_layout(
        xaxis=dict(
            range=["2008-12-01", "2026-12-31"],
            title=None,
            tickformat="%Y",
            dtick="M12",
            showgrid=True,
            gridcolor="rgba(200,200,200,0.4)",
        ),
        yaxis=dict(
            range=[2500, 2900],
            title="ELO",
            dtick=50,
            showgrid=True,
            gridcolor="rgba(200,200,200,0.4)",
        ),
        legend=dict(
            orientation="v",
            x=1.01,
            y=1,
            xanchor="left",
            font=dict(size=12),
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#ddd",
            borderwidth=1,
        ),
        plot_bgcolor="white",
        paper_bgcolor="white",
        margin=dict(l=60, r=200, t=20, b=50),
        hovermode="closest",
    )

    return fig
