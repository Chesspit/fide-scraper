"""Page 1: ELO 2700+ Spieler — Rating-Progression."""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
from dash import Input, Output, State, callback, dcc, html
import plotly.graph_objects as go

from data import DEFAULT_PLAYER_IDS, HIGHLIGHT_COLORS, MAX_HIGHLIGHTS, load_2700_history

dash.register_page(__name__, path="/a", name="Version A", title="FIDE | Version A")

YEAR_MIN, YEAR_MAX, YEAR_STEP = 1965, 2015, 5
SLIDER_MARKS = {y: str(y) for y in range(YEAR_MIN, YEAR_MAX + 1, YEAR_STEP)}

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("ELO 2700+ Spieler — Rating-Progression", className="page-title"),

        # Birth year slider
        html.Div(
            [
                html.Label(
                    "Geburtsjahr-Filter:",
                    style={"fontWeight": "600", "marginBottom": "4px", "display": "block"},
                ),
                dcc.RangeSlider(
                    id="birth-year-slider-a",
                    min=YEAR_MIN,
                    max=YEAR_MAX,
                    step=YEAR_STEP,
                    value=[YEAR_MIN, YEAR_MAX],
                    marks=SLIDER_MARKS,
                    tooltip={"placement": "bottom", "always_visible": False},
                ),
            ],
            style={"marginBottom": "24px", "paddingRight": "16px"},
        ),

        # Dropdown + selection box
        html.Div(
            [
                html.Div(
                    [
                        html.Label(
                            "Spieler hervorheben (max. 12):",
                            style={"fontWeight": "600", "marginBottom": "6px", "display": "block"},
                        ),
                        dcc.Dropdown(
                            id="player-dropdown-a",
                            options=[],
                            value=DEFAULT_PLAYER_IDS,
                            multi=True,
                            placeholder="Spieler hinzufügen…",
                            clearable=False,
                            style={"fontSize": "13px"},
                        ),
                    ],
                    style={"flex": "1", "minWidth": "320px"},
                ),

                html.Div(
                    id="selected-players-box-a",
                    style={
                        "minWidth": "220px",
                        "maxWidth": "300px",
                        "padding": "10px 14px",
                        "border": "1px solid #ddd",
                        "borderRadius": "6px",
                        "background": "#fafafa",
                        "alignSelf": "flex-start",
                        "fontSize": "13px",
                    },
                ),
            ],
            style={
                "display": "flex",
                "gap": "20px",
                "alignItems": "flex-start",
                "marginBottom": "16px",
            },
        ),

        dcc.Graph(
            id="elo-chart-a",
            config={"displayModeBar": False},
            style={"height": "600px"},
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _filter_by_birth_year(df, year_range):
    lo, hi = year_range
    return df[df["birth_year"].between(lo, hi)]


def _player_options(df):
    meta = df[["fide_id", "name", "federation"]].drop_duplicates("fide_id").sort_values("name")
    return [
        {"label": f"{r['name']} ({r['federation']})", "value": int(r["fide_id"])}
        for _, r in meta.iterrows()
    ]

# ---------------------------------------------------------------------------
# Callbacks
# ---------------------------------------------------------------------------


@callback(
    Output("player-dropdown-a", "options"),
    Output("player-dropdown-a", "value"),
    Input("birth-year-slider-a", "value"),
    State("player-dropdown-a", "value"),
)
def update_options(year_range, selected_ids):
    df = load_2700_history()
    filtered = _filter_by_birth_year(df, year_range)
    options = _player_options(filtered)

    # Always keep selected players in options (even if outside birth year range)
    selected_ids = (selected_ids or [])[:MAX_HIGHLIGHTS]
    option_ids = {int(o["value"]) for o in options}
    extra = _player_options(df[df["fide_id"].isin([sid for sid in selected_ids if sid not in option_ids])])
    options = options + extra

    return options, selected_ids


@callback(
    Output("selected-players-box-a", "children"),
    Input("player-dropdown-a", "value"),
)
def update_player_box(selected_ids):
    if not selected_ids:
        return html.Span("Keine Spieler ausgewählt", style={"color": "#aaa"})

    df = load_2700_history()
    id_to_name = df[["fide_id", "name"]].drop_duplicates("fide_id").set_index("fide_id")["name"].to_dict()

    items = [html.P("Ausgewählte Spieler:", style={"margin": "0 0 8px", "fontWeight": "600"})]
    for i, fide_id in enumerate(selected_ids[:MAX_HIGHLIGHTS]):
        color = HIGHLIGHT_COLORS[i]
        name = id_to_name.get(fide_id, f"ID {fide_id}")
        items.append(
            html.Div(
                [
                    html.Span("●", style={"color": color, "marginRight": "8px", "fontSize": "16px"}),
                    html.Span(name),
                ],
                style={"marginBottom": "4px", "display": "flex", "alignItems": "center"},
            )
        )

    remaining = MAX_HIGHLIGHTS - len(selected_ids)
    if remaining > 0:
        items.append(
            html.P(
                f"{remaining} Slot{'s' if remaining != 1 else ''} frei",
                style={"margin": "8px 0 0", "color": "#999", "fontSize": "11px"},
            )
        )
    return items


@callback(
    Output("elo-chart-a", "figure"),
    Input("player-dropdown-a", "value"),
    Input("birth-year-slider-a", "value"),
)
def update_chart(selected_ids, year_range):
    df = load_2700_history()
    filtered_df = _filter_by_birth_year(df, year_range)
    selected_ids = selected_ids or []
    selected_set = set(selected_ids)

    fig = go.Figure()

    # Grey background lines
    for fide_id, group in filtered_df.groupby("fide_id"):
        if fide_id in selected_set:
            continue
        fig.add_trace(
            go.Scatter(
                x=group["period"],
                y=group["rating"],
                mode="lines",
                line=dict(color="rgba(180,180,180,0.2)", width=1),
                showlegend=False,
                hoverinfo="skip",
            )
        )

    # Highlighted lines (always shown even if outside birth year filter)
    id_to_name = df[["fide_id", "name"]].drop_duplicates("fide_id").set_index("fide_id")["name"].to_dict()
    for i, fide_id in enumerate(selected_ids[:MAX_HIGHLIGHTS]):
        player_df = df[df["fide_id"] == fide_id].sort_values("period")
        if player_df.empty:
            continue
        color = HIGHLIGHT_COLORS[i]
        name = id_to_name.get(fide_id, f"ID {fide_id}")
        birth_year = player_df["birth_year"].iloc[0]
        label = f"{name} (*{int(birth_year)})" if pd.notna(birth_year) else name
        fig.add_trace(
            go.Scatter(
                x=player_df["period"],
                y=player_df["rating"],
                mode="lines",
                line=dict(color=color, width=2.5),
                name=label,
                hovertemplate=f"<b>{name}</b><br>%{{x|%b %Y}}<br>ELO %{{y}}<extra></extra>",
            )
        )

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
            range=[2600, 2900],
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


import pandas as pd  # noqa: E402 — needed for pd.notna in callback
