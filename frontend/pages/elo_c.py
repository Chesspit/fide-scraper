"""Page C: ELO-Progression mit Top-100-Hintergrund + Tabelle."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
import pandas as pd
from dash import Input, Output, callback, dash_table, dcc, html
import plotly.graph_objects as go

from data import DEFAULT_PLAYER_IDS, HIGHLIGHT_COLORS, MAX_HIGHLIGHTS, load_2700_history
from data_top100 import load_top100

dash.register_page(__name__, path="/c", name="Version C", title="FIDE | Top-100 Hintergrund")

BAND_CONFIGS = [
    (0.00, 0.50, "rgba(175,175,175,0.50)"),   # Rang 200–101
    (0.50, 0.75, "rgba(195,195,195,0.45)"),   # Rang 100–51
    (0.75, 0.90, "rgba(210,210,210,0.40)"),   # Rang 50–21
    (0.90, 0.95, "rgba(225,225,225,0.38)"),   # Rang 20–11
]

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("ELO-Progression — Hintergrund: Top 100 Weltrangliste", className="page-title"),

        html.Div(
            "Die grauen Flächen zeigen die Ratingverteilung der jeweils 200 bestplatzierten Spieler "
            "der FIDE-Weltrangliste im Januar des Jahres. "
            "Rang 100 liegt stabil um ~2650, Rang 200 um ~2610.",
            className="chart-info",
            style={"marginBottom": "16px"},
        ),

        html.Div(
            [
                html.Label(
                    "Spieler hervorheben (max. 12):",
                    style={"fontWeight": "600", "marginBottom": "6px", "display": "block"},
                ),
                dcc.Dropdown(
                    id="player-dropdown-c",
                    options=[],
                    value=DEFAULT_PLAYER_IDS,
                    multi=True,
                    placeholder="Spieler hinzufügen…",
                    clearable=False,
                    style={"fontSize": "13px"},
                ),
            ],
            style={"marginBottom": "20px"},
        ),

        dcc.Graph(
            id="elo-chart-c",
            config={"displayModeBar": False},
            style={"height": "600px"},
        ),

        html.Hr(style={"margin": "32px 0 24px"}),

        # Table section
        html.Div(
            [
                html.H3("Top-200 Weltrangliste — Jahres-Snapshot",
                        style={"fontSize": "1.1rem", "fontWeight": "600", "marginBottom": "12px"}),
                html.Div(
                    [
                        html.Label("Jahr:", style={"fontWeight": "600", "marginRight": "10px"}),
                        dcc.Dropdown(
                            id="table-year-c",
                            options=[{"label": str(y), "value": y} for y in range(2009, 2027)],
                    # Table now shows Top 200
                            value=2026,
                            clearable=False,
                            style={"width": "100px", "fontSize": "13px"},
                        ),
                    ],
                    style={"display": "flex", "alignItems": "center", "marginBottom": "12px"},
                ),
                dash_table.DataTable(
                    id="top100-table-c",
                    columns=[
                        {"name": "Rang", "id": "rank"},
                        {"name": "Name", "id": "name"},
                        {"name": "Verband", "id": "federation"},
                        {"name": "ELO", "id": "rating"},
                    ],
                    style_table={"height": "500px", "overflowY": "auto"},
                    style_cell={"fontSize": "13px", "padding": "6px 10px", "fontFamily": "inherit"},
                    style_header={"fontWeight": "600", "backgroundColor": "#f0f4f8"},
                    style_data_conditional=[
                        {"if": {"filter_query": "{rating} >= 2700"},
                         "backgroundColor": "#e8f4fd", "fontWeight": "500"},
                    ],
                    page_size=100,
                ),
            ],
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_hist_cache: pd.DataFrame | None = None
_top100_cache: pd.DataFrame | None = None


def _get_hist():
    global _hist_cache
    if _hist_cache is None:
        _hist_cache = load_2700_history()
    return _hist_cache


def _get_top100():
    global _top100_cache
    if _top100_cache is None:
        _top100_cache = load_top100()
    return _top100_cache


BAND_RANKS = [(200, 100), (100, 50), (50, 20), (20, 10)]


def _background_traces(top100: pd.DataFrame):
    """Bands computed directly from exact rank ratings — boundaries align with rank lines."""
    # Build lookup: (year, rank) -> rating
    rank_rating = top100.set_index(["year", "rank"])["rating"]

    years = sorted(top100["year"].unique())
    periods = pd.to_datetime([f"{y}-01-01" for y in years])

    def ratings_for_rank(rank):
        return [rank_rating.get((y, rank), None) for y in years]

    traces = []
    band_labels = ["Rang 200–101", "Rang 100–51", "Rang 50–21", "Rang 20–11"]
    for (rank_lo, rank_hi), (_, __, color), label in zip(BAND_RANKS, BAND_CONFIGS, band_labels):
        y_hi = ratings_for_rank(rank_hi)   # higher rank = higher ELO = top edge
        y_lo = ratings_for_rank(rank_lo)   # lower rank = lower ELO = bottom edge
        traces.append(go.Scatter(
            x=list(periods) + list(periods[::-1]),
            y=y_hi + y_lo[::-1],
            fill="toself",
            fillcolor=color,
            line=dict(width=0),
            name=label,
            showlegend=False,
            hoverinfo="skip",
        ))

    # Rank lines
    rank_styles = [
        (10,  "rgba(60,60,60,0.75)",   "Rang 10"),
        (20,  "rgba(80,80,80,0.65)",   "Rang 20"),
        (50,  "rgba(100,100,100,0.6)", "Rang 50"),
        (100, "rgba(120,120,120,0.55)","Rang 100"),
        (200, "rgba(140,140,140,0.50)","Rang 200"),
    ]
    for rank, color, label in rank_styles:
        rank_df = (
            top100[top100["rank"] == rank]
            .sort_values("year")
            .assign(period=lambda d: pd.to_datetime(d["year"].astype(str) + "-01-01"))
        )
        traces.append(go.Scatter(
            x=rank_df["period"],
            y=rank_df["rating"],
            mode="lines",
            line=dict(color=color, width=1.4),
            name=label,
            showlegend=True,
            customdata=rank_df[["name", "federation", "rating"]].values,
            hovertemplate=(
                f"<b>{label}</b><br>"
                "%{x|%Y}<br>"
                "ELO: <b>%{customdata[2]}</b><br>"
                "%{customdata[0]} (%{customdata[1]})"
                "<extra></extra>"
            ),
        ))

    return traces

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@callback(
    Output("player-dropdown-c", "options"),
    Output("player-dropdown-c", "value"),
    Input("player-dropdown-c", "id"),
)
def populate_options(_):
    df = _get_hist()
    meta = df[["fide_id", "name"]].drop_duplicates("fide_id").sort_values("name")
    opts = [{"label": r["name"], "value": int(r["fide_id"])} for _, r in meta.iterrows()]
    return opts, DEFAULT_PLAYER_IDS


@callback(
    Output("elo-chart-c", "figure"),
    Input("player-dropdown-c", "value"),
)
def update_chart(selected_ids):
    hist = _get_hist()
    top100 = _get_top100()
    selected_ids = selected_ids or []

    fig = go.Figure()

    for trace in _background_traces(top100):
        fig.add_trace(trace)

    id_to_name = hist[["fide_id", "name"]].drop_duplicates("fide_id").set_index("fide_id")["name"].to_dict()
    for i, fide_id in enumerate(selected_ids[:MAX_HIGHLIGHTS]):
        player_df = hist[hist["fide_id"] == fide_id].sort_values("period")
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
            title=None, tickformat="%Y", dtick="M12",
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        yaxis=dict(
            range=[2540, 2900], title="ELO", dtick=50,
            showgrid=True, gridcolor="rgba(200,200,200,0.4)",
        ),
        legend=dict(
            x=1.01, y=1, xanchor="left", font=dict(size=12),
            bgcolor="rgba(255,255,255,0.9)", bordercolor="#ddd", borderwidth=1,
        ),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=60, r=200, t=20, b=50),
        hovermode="closest",
    )
    return fig


@callback(
    Output("top100-table-c", "data"),
    Input("table-year-c", "value"),
)
def update_table(year):
    df = _get_top100()
    sub = df[df["year"] == year][["rank", "name", "federation", "rating"]]
    return sub.to_dict("records")
