"""Story A3: Die Zone 2400–2600 — Durchgangsstation oder Endstation? (Daten: data_zone2400.py)"""
import dash
import pandas as pd
import plotly.graph_objects as go
import pyarrow.dataset as ds
from dash import Input, Output, callback, dcc, html
from plotly.subplots import make_subplots

import data_zone2400 as z
from data_player_history import HISTORY_PATH

dash.register_page(
    __name__,
    path="/zone-2400",
    name="Zone 2400",
    title="ELO-Einsichten | Die Zone 2400–2600",
    order=13,
)

# Zustände: fest zugeordnete Farben (dataviz-Referenzpalette, light)
C_UP, C_DOWN, C_IN = "#2a78d6", "#eb6834", "#c9c8c4"
C_WOMEN = "#1baf7a"   # eigene Farbe, damit sie nicht wie „unter 2400“ aussieht
C_LINE = "#2a78d6"
C_MUTED = "#8a8985"
C_GRID = "#ecebe8"
STATE_STYLE = {
    "oben":  ("über 2600", C_UP),
    "drin":  ("noch in der Zone", C_IN),
    "unten": ("wieder unter 2400", C_DOWN),
}
DEFAULT_PLAYER = 46616543  # Gukesh D


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=56, r=24, t=36, b=48),
        paper_bgcolor="white", plot_bgcolor="white", font=dict(size=13, color="#333"),
        legend=dict(orientation="h", yanchor="bottom", y=1.04, x=0),
    )
    fig.update_xaxes(gridcolor=C_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=C_GRID, zeroline=False)
    return fig


def _graph(fig, gid=None) -> dcc.Graph:
    kw = {"id": gid} if gid else {}
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, **kw)


def _kpi(value: str, label: str) -> html.Div:
    return html.Div([html.Div(value, className="gp-kpi-value"),
                     html.Div(label, className="gp-kpi-label")], className="gp-kpi")


def _section(title, text, *children) -> html.Div:
    return html.Div([html.H4(title, className="gp-h"), html.P(text, className="gp-text"), *children],
                    className="gp-section")


# ---------------------------------------------------------------------------
# Diagramme
# ---------------------------------------------------------------------------

def _add_states(fig, curve: pd.DataFrame, row=None, col=None, legend=True):
    for state in z.STATES:   # oben, drin, unten — gestapelt von oben nach unten
        label, color = STATE_STYLE[state]
        fig.add_trace(go.Scatter(
            x=curve["years"], y=curve[state] * 100, name=label, stackgroup="s",
            mode="lines", line=dict(width=0.5, color="white"), fillcolor=color,
            customdata=curve["n"], showlegend=legend, legendgroup=state,
            hovertemplate="%{y:.0f} % " + label + "<br>%{customdata} Einsteiger<extra></extra>",
        ), row=row, col=col)


def fig_states(s: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    _add_states(fig, z.state_curve(s))
    _layout(fig, 380)
    fig.update_layout(hovermode="x unified")
    fig.update_xaxes(title="Jahre nach dem ersten Überschreiten von 2400", dtick=1)
    fig.update_yaxes(title="Anteil der Einsteiger (%)", range=[0, 100])
    return fig


def fig_age(s: pd.DataFrame) -> go.Figure:
    counts = s["age_group"].value_counts()
    fig = make_subplots(rows=1, cols=3, shared_yaxes=True, horizontal_spacing=0.04,
                        subplot_titles=[f"Einstieg {a} ({counts.get(a, 0)})" for a in z.AGE_GROUPS])
    for i, a in enumerate(z.AGE_GROUPS, start=1):
        _add_states(fig, z.state_curve(s[s["age_group"] == a], max_years=10), row=1, col=i, legend=(i == 1))
    _layout(fig, 360)
    fig.update_layout(hovermode="x unified", margin=dict(t=90),
                      legend=dict(orientation="h", yanchor="bottom", y=1.14, x=0))
    fig.update_xaxes(dtick=2, title="Jahre")
    fig.update_yaxes(range=[0, 100])
    fig.update_yaxes(title="Anteil (%)", row=1, col=1)
    return fig


def fig_peaks(s: pd.DataFrame) -> go.Figure:
    p = z.peak_shares(s)
    fig = go.Figure(go.Bar(
        x=p["band"], y=p["share"] * 100, marker_color=[C_LINE] + [C_IN] * (len(p) - 1),
        marker_line_width=0, customdata=p["n"], text=[_pct(v) for v in p["share"]],
        textposition="outside", cliponaxis=False,
        hovertemplate="%{x}: %{y:.0f} %<br>%{customdata} Spieler<extra></extra>",
    ))
    _layout(fig, 300)
    fig.update_layout(showlegend=False, bargap=0.35)
    fig.update_xaxes(title="Höchstes Rating seit dem Einstieg")
    fig.update_yaxes(title="Anteil (%)", showticklabels=False, showgrid=False)
    return fig


def fig_sex(s: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    for sex, label, color in (("M", "Männer", C_IN), ("F", "Frauen", C_WOMEN)):
        vals = [z.state_after(s[(s["sex"] == sex) & (s["age_group"] == a)], 3) for a in z.AGE_GROUPS]
        fig.add_trace(go.Bar(
            x=z.AGE_GROUPS, y=[v["unten"] * 100 for v in vals], name=label, marker_color=color,
            marker_line_width=0, customdata=[v["n"] for v in vals],
            text=[f"{v['unten'] * 100:.0f} %<br>n={v['n']}" for v in vals], textposition="outside",
            cliponaxis=False,
            hovertemplate=label + ": %{y:.0f} % wieder unter 2400<br>%{customdata} Einsteiger<extra></extra>",
        ))
    _layout(fig, 340)
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.08)
    fig.update_xaxes(title="Alter beim Einstieg")
    fig.update_yaxes(title="nach 3 Jahren wieder unter 2400 (%)", range=[0, 110])
    return fig


def player_options(s: pd.DataFrame) -> list[dict]:
    s = s.sort_values("peak", ascending=False)
    return [{"label": f"{r.name} ({r.federation}, Einstieg {r.entry:%m/%Y} mit {r.age_entry:.0f})",
             "value": int(r.fide_id)} for r in s.itertuples() if isinstance(r.name, str)]


def fig_player(s: pd.DataFrame, fide_id: int | None) -> go.Figure:
    fig = go.Figure()
    _layout(fig, 360)
    fig.add_hrect(y0=z.LOW, y1=z.HIGH, fillcolor="#f2f1ee", line_width=0, layer="below")
    for y in (z.LOW, z.HIGH):
        fig.add_hline(y=y, line=dict(color=C_MUTED, width=1, dash="dot"))
    match = s[s["fide_id"] == fide_id] if fide_id else s.iloc[0:0]
    if not match.empty:
        row = match.iloc[0]
        h = ds.dataset(HISTORY_PATH).to_table(filter=ds.field("fide_id") == fide_id).to_pandas()
        h = h.sort_values("period")
        h = h[h["period"] >= row["entry"] - pd.DateOffset(years=2)]
        fig.update_yaxes(range=[min(h["rating"].min(), z.LOW) - 50, max(h["rating"].max(), z.HIGH) + 50])
        fig.add_trace(go.Scatter(x=h["period"], y=h["rating"], name=row["name"],
                                 line=dict(color=C_LINE, width=2),
                                 hovertemplate="%{x|%m/%Y}: %{y}<extra></extra>"))
        for col, text in (("entry", "Einstieg"), ("up", "2600"), ("down", "unter 2400"), ("back", "zurück")):
            if pd.notna(row[col]):
                fig.add_vline(x=row[col], line=dict(color=C_MUTED, width=1))
                fig.add_annotation(x=row[col], y=1, yref="paper", text=text, showarrow=False,
                                   xanchor="left", xshift=3, font=dict(size=11, color="#555"))
    fig.update_layout(showlegend=False, hovermode="x unified")
    fig.update_yaxes(title="Rating")
    return fig


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**_):
    s = z.load_entries()
    f = z.facts(s)
    u18_10 = z.state_after(s[s["age_group"] == "U18"], 10)
    o25_10 = z.state_after(s[s["age_group"] == "25+"], 10)
    n_f = int((s["sex"] == "F").sum())
    default = DEFAULT_PLAYER if (s["fide_id"] == DEFAULT_PLAYER).any() else None
    n_txt = f"{f['n']:,}".replace(",", ".")

    return html.Div(
        [
            html.Div("IM · GM · 2400", className="gp-kicker"),
            html.H2("Die Zone 2400–2600: Durchgangsstation oder Endstation?", className="page-title"),
            html.P(
                f"2400 ist die Schwelle zum IM-Titel, 2600 die Grenze zur erweiterten Weltspitze. "
                f"Seit {f['first_year']} haben {n_txt} Spieler die 2400 zum ersten Mal überschritten. "
                "Was ist aus ihnen geworden?",
                className="gp-lead",
            ),
            html.Div(
                [
                    _kpi(_pct(f["down_1y"]), "sind nach einem Jahr wieder unter 2400"),
                    _kpi(_pct(f["peak_lt_2450"]), "kommen nie über 2449 hinaus"),
                    _kpi(_pct(f["up_5y"]), "stehen nach fünf Jahren über 2600"),
                    _kpi(f"{f['age_up']:.0f}", "Jahre alt waren die, die es nach oben schafften, beim Einstieg (Median)"),
                ],
                className="gp-kpis",
            ),

            _section(
                "Die meisten fallen zurück",
                f"Was aus den Einsteigern nach 1, 2, … Jahren geworden ist. Wer nach unten rausfällt, "
                f"tut das meist schnell: im Median nach {f['months_to_down']:.0f} Monaten. Nach etwa fünf "
                f"Jahren ändert sich das Bild kaum noch — rund ein Drittel bleibt in der Zone, knapp zwei "
                f"Drittel sind wieder darunter, und nur wenige stehen über 2600. Fast die Hälfte der "
                f"Zurückgefallenen ({_pct(f['back_share'])}) überschreitet die 2400 später noch einmal.",
                _graph(fig_states(s)),
            ),

            _section(
                "Wer jung kommt, kommt weiter",
                f"Dieselbe Auswertung nach Alter beim Einstieg. Von den Unter-18-Jährigen steht nach zehn "
                f"Jahren fast jeder Vierte ({_pct(u18_10['oben'])}) über 2600. Wer erst mit 25 oder später "
                f"einsteigt, schafft das praktisch nie — {_pct(o25_10['unten'])} sind nach zehn Jahren "
                "wieder unter 2400.",
                _graph(fig_age(s)),
            ),

            _section(
                "Für die Mehrheit ist 2400 der Gipfel",
                "Das höchste Rating, das die Einsteiger seither erreicht haben. Mehr als die Hälfte kommt "
                "nie über 2449 hinaus — für sie war das Überschreiten der 2400 selbst der Höhepunkt.",
                _graph(fig_peaks(s)),
            ),

            _section(
                "Frauen fallen häufiger zurück",
                f"Anteil, der drei Jahre nach dem Einstieg wieder unter 2400 steht. Frauen und Männer "
                f"steigen gleich alt ein, und ihr Höchstwert ist ähnlich verteilt — trotzdem fallen Frauen "
                f"in jeder Altersgruppe häufiger zurück. Vorsicht: Es sind nur {n_f} Frauen, die Balken "
                "schwanken entsprechend. Woran es liegt, zeigen diese Daten nicht; ein Kandidat ist die "
                "Turnierwahl (Frauen- statt offene Turniere).",
                _graph(fig_sex(s)),
            ),

            html.Div(
                [
                    html.H4("Einzelne Wege durch die Zone", className="gp-h"),
                    html.P("Jeder Einsteiger seit 2011 lässt sich einblenden. Die graue Fläche ist die Zone, "
                           "die Linien markieren Einstieg, Absturz und Rückkehr bzw. den Sprung über 2600.",
                           className="gp-text"),
                    html.Div([
                        html.Label("Spieler:", className="gp-label"),
                        dcc.Dropdown(id="z24-player", options=player_options(s), value=default,
                                     placeholder="Name eingeben …", clearable=True,
                                     style={"minWidth": "340px"}),
                    ], className="gp-control"),
                    _graph(fig_player(s, default), "z24-player-chart"),
                ],
                className="gp-section",
            ),

            html.Details(
                [
                    html.Summary("Wie gerechnet wurde"),
                    html.Ul([
                        html.Li("Grundlage: monatliche FIDE-Listen ab 2009. Einstieg = erste Liste mit "
                                "mindestens 2400, davor mindestens 12 Monate darunter. Deshalb beginnen "
                                "die Einstiege 2010/2011."),
                        html.Li("Nicht mitgezählt: wer schon 2006–2008 über 2400 stand, und Großmeister, "
                                "die seit 2009 nie 2500 erreicht haben — beide waren früher schon einmal "
                                "oben. Bei Älteren können trotzdem vereinzelt Rückkehrer dabei sein."),
                        html.Li("Nach unten raus = erstmals sechs Listen in Folge unter 2400. Nach oben "
                                "raus = erste Liste mit mindestens 2600. Es zählt das erste Verlassen."),
                        html.Li("Der Stand nach t Jahren nutzt nur Einsteiger, deren Einstieg mindestens "
                                "t Jahre zurückliegt; spätere Jahre beruhen daher auf weniger Spielern."),
                        html.Li("„Noch in der Zone“ schließt Spieler ein, die nicht mehr spielen: Bei "
                                "rund jedem Fünften hat sich das Rating seit einem Jahr nicht verändert."),
                        html.Li("Geschlecht und Geburtsjahr stammen aus der neuesten FIDE-Liste."),
                    ]),
                ],
                className="gp-method",
            ),
        ],
        className="gp-page",
    )


@callback(Output("z24-player-chart", "figure"), Input("z24-player", "value"))
def update_player(fide_id):
    return fig_player(z.load_entries(), fide_id)
