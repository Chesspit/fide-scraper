"""Page 2: GM/IM Entwicklung über Zeit — animierter Bar Race, Karte, Bubble."""
import sys, os, math
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import dash
from dash import Input, Output, callback, dcc, html
import plotly.graph_objects as go
import pandas as pd

from data_titles import load_title_evolution
from data_population import FIDE_TO_ISO3, load_population, get_pop_lookup

dash.register_page(__name__, path="/titles", name="GM/IM Entwicklung", title="ELO-Einsichten | GM/IM Entwicklung", order=11)

TOP_N = 20

CONTINENT_COLORS = {
    "Europe":   "#2196F3",
    "Asia":     "#E63946",
    "Americas": "#4CAF50",
    "Africa":   "#FF9800",
    "Oceania":  "#9C27B0",
    "Other":    "#9E9E9E",
}

# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

layout = html.Div(
    [
        html.H2("Entwicklung der GM- und IM-Titel weltweit (2009–2026)", className="page-title"),

        html.Div(
            "Anzahl der von der FIDE vergebenen GM- und IM-Titel pro Land und Kontinent, "
            "jeweils gemessen im Januar des jeweiligen Jahres (18 Jahres-Snapshots). "
            "Bevölkerungsdaten: UN/Weltbank-Schätzungen ~2020 (statische Näherung).",
            className="chart-info",
            style={"marginBottom": "16px"},
        ),

        html.Div(
            [
                # Titel
                html.Div([
                    html.Label("Titel:", style={"fontWeight": "600", "marginRight": "8px"}),
                    dcc.RadioItems(
                        id="title-type",
                        options=[
                            {"label": " GM", "value": "gm"},
                            {"label": " IM", "value": "im"},
                            {"label": " GM + IM", "value": "both"},
                        ],
                        value="gm", inline=True, style={"fontSize": "13px"},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),

                # Gruppierung
                html.Div([
                    html.Label("Gruppierung:", style={"fontWeight": "600", "marginRight": "8px"}),
                    dcc.RadioItems(
                        id="group-by",
                        options=[
                            {"label": " Länder (Top 20)", "value": "country"},
                            {"label": " Kontinente", "value": "continent"},
                        ],
                        value="country", inline=True, style={"fontSize": "13px"},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),

                # Darstellung
                html.Div([
                    html.Label("Darstellung:", style={"fontWeight": "600", "marginRight": "8px"}),
                    dcc.RadioItems(
                        id="chart-type",
                        options=[
                            {"label": " Balken", "value": "bar"},
                            {"label": " Weltkarte", "value": "map"},
                            {"label": " Bubble (Rosling)", "value": "bubble"},
                        ],
                        value="bar", inline=True, style={"fontSize": "13px"},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),

                # Per-Kopf Toggle
                html.Div([
                    dcc.Checklist(
                        id="per-capita",
                        options=[{"label": " Pro Million Einwohner", "value": "yes"}],
                        value=[],
                        style={"fontSize": "13px", "fontWeight": "600"},
                    ),
                ], style={"display": "flex", "alignItems": "center"}),
            ],
            style={
                "display": "flex", "gap": "28px", "flexWrap": "wrap",
                "marginBottom": "16px", "padding": "12px 16px",
                "background": "#f8f9fa", "borderRadius": "6px",
                "border": "1px solid #e0e0e0",
            },
        ),

        dcc.Graph(
            id="title-chart",
            config={"displayModeBar": False},
            style={"height": "580px"},
        ),
    ],
    style={"padding": "24px 32px", "maxWidth": "1400px", "margin": "0 auto"},
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_cache_df: pd.DataFrame | None = None
_pop_lookup: dict | None = None


def _get_df() -> pd.DataFrame:
    global _cache_df
    if _cache_df is None:
        _cache_df = load_title_evolution()
    return _cache_df


def _get_pop_lookup() -> dict:
    global _pop_lookup
    if _pop_lookup is None:
        _pop_lookup = get_pop_lookup(load_population())
    return _pop_lookup


def _values(row_or_series, title_type):
    if title_type == "gm":
        return row_or_series["gm"]
    if title_type == "im":
        return row_or_series["im"]
    return row_or_series["gm"] + row_or_series["im"]


def _label(title_type, per_capita):
    base = {"gm": "GMs", "im": "IMs", "both": "GMs + IMs"}[title_type]
    return f"{base} pro Mio. Einwohner" if per_capita else base


def _fide_to_iso3(fed):
    return FIDE_TO_ISO3.get(fed, fed)


def _add_pop(agg: pd.DataFrame) -> pd.DataFrame:
    """Add year-specific population (in millions) from World Bank data."""
    lookup = _get_pop_lookup()
    agg = agg.copy()
    agg["pop_m"] = agg.apply(
        lambda r: lookup.get((r["federation"], r["year"]),
                  lookup.get((r["federation"], 2020), 0.0)),  # fallback to 2020
        axis=1,
    )
    return agg


def _animation_layout(years, title, max_val, extra=None):
    layout = go.Layout(
        title=dict(text=title, font=dict(size=13), x=0.5),
        updatemenus=[dict(
            type="buttons", showactive=False, y=1.12, x=0, xanchor="left",
            buttons=[
                dict(label="▶ Play", method="animate",
                     args=[None, {"frame": {"duration": 850, "redraw": True},
                                  "fromcurrent": True, "mode": "immediate"}]),
                dict(label="⏸ Pause", method="animate",
                     args=[[None], {"frame": {"duration": 0}, "mode": "immediate"}]),
            ],
        )],
        sliders=[dict(
            active=0,
            currentvalue=dict(prefix="Jahr: ", font=dict(size=13)),
            pad=dict(t=10),
            steps=[dict(method="animate",
                        args=[[str(y)], {"frame": {"duration": 0, "redraw": True},
                                         "mode": "immediate"}],
                        label=str(y)) for y in years],
        )],
        **(extra or {}),
    )
    return layout


def _year_annotation(year):
    return [dict(x=0.98, y=0.04, xref="paper", yref="paper",
                 text=str(year), font=dict(size=44, color="rgba(0,0,0,0.09)"),
                 showarrow=False)]

# ---------------------------------------------------------------------------
# Bar chart
# ---------------------------------------------------------------------------

def _bar_figure(df, title_type, group_by, per_capita):
    years = sorted(df["year"].unique())
    key = "continent" if group_by == "continent" else "federation"
    agg = df.groupby(["year", key])[["gm", "im"]].sum().reset_index()

    if per_capita and group_by == "country":
        agg = _add_pop(agg)
        agg = agg[agg["pop_m"] > 0.01]
        agg["value"] = _values(agg, title_type) / agg["pop_m"]
    else:
        agg["value"] = _values(agg, title_type)

    all_ents = sorted(agg[key].unique())
    colors = {e: f"hsl({i*360//max(len(all_ents),1)},55%,50%)" for i, e in enumerate(all_ents)}
    x_max = agg["value"].max() * 1.15

    def _frame_data(year):
        sub = agg[agg["year"] == year].nlargest(TOP_N, "value").sort_values("value")
        return go.Bar(
            x=sub["value"], y=sub[key], orientation="h",
            marker_color=[colors.get(e, "#888") for e in sub[key]],
            text=sub["value"].apply(lambda v: f"{v:.2f}" if per_capita else str(int(v))),
            textposition="outside",
            hovertemplate=f"%{{y}}: %{{x:.2f}}<extra></extra>" if per_capita
                          else "%{y}: %{x}<extra></extra>",
        )

    fig = go.Figure(
        data=[_frame_data(years[0])],
        frames=[go.Frame(data=[_frame_data(y)], name=str(y),
                         layout=go.Layout(annotations=_year_annotation(y))) for y in years],
        layout=_animation_layout(years, _label(title_type, per_capita), x_max, extra=dict(
            xaxis=dict(title=_label(title_type, per_capita), range=[0, x_max]),
            yaxis=dict(title=None, tickfont=dict(size=11)),
            plot_bgcolor="white", paper_bgcolor="white",
            margin=dict(l=130, r=40, t=50, b=50),
            annotations=_year_annotation(years[0]),
        )),
    )
    return fig

# ---------------------------------------------------------------------------
# Choropleth map
# ---------------------------------------------------------------------------

def _map_figure(df, title_type, per_capita):
    years = sorted(df["year"].unique())
    agg = df.groupby(["year", "federation"])[["gm", "im"]].sum().reset_index()

    if per_capita:
        agg = _add_pop(agg)
        agg = agg[agg["pop_m"] > 0.01]
        agg["value"] = _values(agg, title_type) / agg["pop_m"]
    else:
        agg["value"] = _values(agg, title_type)

    agg["iso3"] = agg["federation"].apply(_fide_to_iso3)
    max_val = agg["value"].max()

    def _frame_data(year):
        sub = agg[agg["year"] == year]
        return go.Choropleth(
            locations=sub["iso3"],
            z=sub["value"],
            locationmode="ISO-3",
            colorscale="Blues",
            zmin=0, zmax=max_val,
            colorbar=dict(title=_label(title_type, per_capita), thickness=14, len=0.6),
            hovertemplate="%{location}: %{z:.2f}<extra></extra>" if per_capita
                          else "%{location}: %{z}<extra></extra>",
        )

    fig = go.Figure(
        data=[_frame_data(years[0])],
        frames=[go.Frame(data=[_frame_data(y)], name=str(y)) for y in years],
        layout=_animation_layout(years, _label(title_type, per_capita), max_val, extra=dict(
            geo=dict(showframe=False, showcoastlines=True, projection_type="natural earth"),
            margin=dict(l=0, r=0, t=50, b=10),
        )),
    )
    return fig

# ---------------------------------------------------------------------------
# Bubble (Rosling)
# ---------------------------------------------------------------------------

def _bubble_figure(df, title_type, per_capita):
    years = sorted(df["year"].unique())
    agg = df.groupby(["year", "federation", "continent"])[["gm", "im"]].sum().reset_index()
    agg["total"] = _values(agg, title_type)
    agg = _add_pop(agg)
    agg = agg[(agg["pop_m"] > 0.01) & (agg["total"] > 0)]

    if per_capita:
        # Rosling style: X=population (log), Y=titles per million
        agg["y_val"] = agg["total"] / agg["pop_m"]
        x_title = "Einwohner (Mio., log)"
        y_title = _label(title_type, True)
        x_range = [math.log10(0.03), math.log10(1600)]
        y_max = agg["y_val"].max() * 1.15
        x_log = True
    else:
        # IMs vs GMs per country
        agg["y_val"] = agg["gm"] if title_type != "im" else agg["im"]
        agg["x_val_bubble"] = agg["im"] if title_type != "im" else agg["gm"]
        x_title = "Anzahl IMs" if title_type != "im" else "Anzahl GMs"
        y_title = "Anzahl GMs" if title_type != "im" else "Anzahl IMs"
        x_range = None
        y_max = None
        x_log = False

    continents = sorted(agg["continent"].unique())

    def _traces(year):
        sub = agg[agg["year"] == year]
        traces = []
        for cont in continents:
            c = sub[sub["continent"] == cont]
            if c.empty:
                continue
            x_vals = c["pop_m"] if per_capita else c.get("x_val_bubble", c["im"])
            threshold = c["y_val"].quantile(0.82)
            labels = c["federation"].where(c["y_val"] >= threshold, "")
            traces.append(go.Scatter(
                x=x_vals, y=c["y_val"],
                mode="markers+text", name=cont,
                text=labels, textposition="top center", textfont=dict(size=9),
                marker=dict(
                    size=c["total"] ** 0.43 * 3.8,
                    color=CONTINENT_COLORS.get(cont, "#888"),
                    opacity=0.75, line=dict(width=0.5, color="white"),
                ),
                customdata=c[["federation", "total", "pop_m", "y_val"]].values,
                hovertemplate=(
                    "<b>%{customdata[0]}</b><br>"
                    f"{_label(title_type, False)}: %{{customdata[1]:.0f}}<br>"
                    + ("Einwohner: %{customdata[2]:.2f} Mio<br>"
                       f"Pro Mio: %{{customdata[3]:.2f}}<extra></extra>" if per_capita
                       else "<extra></extra>")
                ),
            ))
        return traces

    extra = dict(
        xaxis=dict(title=x_title, type="log" if x_log else "linear",
                   range=x_range if x_range else None),
        yaxis=dict(title=y_title, range=[0, y_max] if y_max else None),
        plot_bgcolor="white", paper_bgcolor="white",
        margin=dict(l=70, r=30, t=50, b=60),
        legend=dict(title="Kontinent", font=dict(size=12)),
        annotations=_year_annotation(years[0]),
    )

    fig = go.Figure(
        data=_traces(years[0]),
        frames=[go.Frame(data=_traces(y), name=str(y),
                         layout=go.Layout(annotations=_year_annotation(y))) for y in years],
        layout=_animation_layout(years, _label(title_type, per_capita), 0, extra=extra),
    )
    return fig

# ---------------------------------------------------------------------------
# Callback
# ---------------------------------------------------------------------------


@callback(
    Output("title-chart", "figure"),
    Input("title-type", "value"),
    Input("group-by", "value"),
    Input("chart-type", "value"),
    Input("per-capita", "value"),
)
def update_chart(title_type, group_by, chart_type, per_capita_val):
    df = _get_df()
    per_capita = bool(per_capita_val)

    if chart_type == "map":
        return _map_figure(df, title_type, per_capita)
    if chart_type == "bubble":
        return _bubble_figure(df, title_type, per_capita)
    return _bar_figure(df, title_type, group_by, per_capita)
