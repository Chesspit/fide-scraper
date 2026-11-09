"""Story E1: Wann ist ein Großmeister am stärksten? (Daten: data_gm_peak.py)"""
import dash
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data_gm_peak as gp

dash.register_page(
    __name__,
    path="/gm-peak",
    name="GM-Peak",
    title="ELO-Einsichten | Wann ist ein Großmeister am stärksten?",
    order=12,
)

# Kategorische Reihen in fester Reihenfolge (dataviz-Referenzpalette, light)
C1, C2, C3 = "#2a78d6", "#eb6834", "#1baf7a"
C_MUTED = "#8a8985"
C_GRID = "#ecebe8"
DEFAULT_PLAYER = 1503014  # Carlsen

_LEVEL_COLORS = dict(zip(gp.LEVELS, (C1, C2, C3)))


def _fig(height=380) -> go.Figure:
    fig = go.Figure()
    fig.update_layout(
        height=height, margin=dict(l=56, r=24, t=16, b=48),
        paper_bgcolor="white", plot_bgcolor="white",
        font=dict(size=13, color="#333"),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(gridcolor=C_GRID, zeroline=False, ticks="outside", tickcolor=C_GRID)
    fig.update_yaxes(gridcolor=C_GRID, zeroline=True, zerolinecolor="#c9c8c4")
    return fig


def _kpi(value: str, label: str) -> html.Div:
    return html.Div(
        [html.Div(value, className="gp-kpi-value"), html.Div(label, className="gp-kpi-label")],
        className="gp-kpi",
    )


def _section(title: str, text, graph) -> html.Div:
    return html.Div(
        [html.H4(title, className="gp-h"), html.P(text, className="gp-text"), graph],
        className="gp-section",
    )


def _graph(fig, gid=None) -> dcc.Graph:
    kw = {"id": gid} if gid else {}
    return dcc.Graph(figure=fig, config={"displayModeBar": False}, **kw)


# ---------------------------------------------------------------------------
# Diagramme
# ---------------------------------------------------------------------------

def fig_career(df: pd.DataFrame, fide_id: int | None) -> go.Figure:
    act, alle = gp.age_curve(df, True), gp.age_curve(df, False)
    fig = _fig(420)
    fig.add_trace(go.Scatter(
        x=alle["age"], y=alle["curve"], name="alle Jahre (inkl. ohne Partien)",
        line=dict(color=C_MUTED, width=2, dash="dash"),
        hovertemplate="%{y:.0f}<extra>alle Jahre</extra>",
    ))
    fig.add_trace(go.Scatter(
        x=act["age"], y=act["curve"], name="typischer GM (Jahre mit Partien)",
        line=dict(color=C1, width=3),
        hovertemplate="%{y:.0f}<extra>typischer GM</extra>",
    ))
    if fide_id:
        p = gp.player_by_age(fide_id)
        fig.add_trace(go.Scatter(
            x=p["age"], y=p["rel"], name=p["name"].iloc[0],
            line=dict(color=C2, width=2),
            hovertemplate="%{y:.0f}<extra>" + p["name"].iloc[0] + "</extra>",
        ))
    pa = gp.peak_age(act)
    fig.add_vline(x=pa, line=dict(color=C_MUTED, width=1, dash="dot"))
    fig.add_annotation(x=pa, y=0, text=f"Peak mit {pa}", showarrow=False,
                       yshift=14, xanchor="left", xshift=4, font=dict(color="#333"))
    fig.update_xaxes(title="Alter", range=[gp.AGE_MIN, gp.AGE_MAX])
    fig.update_yaxes(title="Punkte unter dem Höchstwert")
    return fig


def fig_yearly(df: pd.DataFrame) -> go.Figure:
    a = gp.age_curve(df, True)
    fig = _fig(320)
    fig.add_trace(go.Bar(
        x=a["age"], y=a["mean"], marker_color=C1, marker_line_width=0,
        customdata=a["n"],
        hovertemplate="Alter %{x}: %{y:+.1f} Punkte/Jahr<br>%{customdata} Spielerjahre<extra></extra>",
    ))
    fig.update_layout(hovermode="closest", bargap=0.25, showlegend=False)
    fig.update_xaxes(title="Alter")
    fig.update_yaxes(title="Ø Veränderung pro Jahr")
    return fig


def fig_levels(df: pd.DataFrame) -> go.Figure:
    fig = _fig(380)
    for lvl in gp.LEVELS:
        c = gp.age_curve(df[df["level"] == lvl], True, min_n=25)
        n = df.loc[df["level"] == lvl, "fide_id"].nunique()
        fig.add_trace(go.Scatter(
            x=c["age"], y=c["curve"], name=f"Höchstwert {lvl} ({n} GMs)",
            line=dict(color=_LEVEL_COLORS[lvl], width=2),
            hovertemplate="%{y:.0f}<extra>" + lvl + "</extra>",
        ))
    fig.update_xaxes(title="Alter", range=[gp.AGE_MIN, 60])
    fig.update_yaxes(title="Punkte unter dem Höchstwert")
    return fig


def fig_time(df: pd.DataFrame) -> go.Figure:
    d = df[df["active"] & df["age"].between(30, 45)]
    y = d.groupby("year")["delta"].agg(n="size", mean="mean").reset_index()
    fig = _fig(320)
    fig.add_trace(go.Bar(
        x=y["year"], y=y["mean"], marker_color=[C2 if yr >= 2022 else C1 for yr in y["year"]],
        marker_line_width=0, customdata=y["n"],
        hovertemplate="%{x}: %{y:+.1f} Punkte<br>%{customdata} GMs<extra></extra>",
    ))
    fig.update_layout(hovermode="closest", bargap=0.25, showlegend=False)
    fig.update_xaxes(title="Jahr (Januar → Januar)", dtick=2)
    fig.update_yaxes(title="Ø Veränderung, GMs 30–45")
    return fig


def fig_inactive(df: pd.DataFrame) -> go.Figure:
    s = gp.inactive_share(df)
    fig = _fig(300)
    fig.add_trace(go.Scatter(
        x=s["age"], y=s["share"] * 100, line=dict(color=C1, width=2), fill="tozeroy",
        fillcolor="rgba(42,120,214,0.10)", customdata=s["n"],
        hovertemplate="Alter %{x}: %{y:.0f} %<br>%{customdata} Spielerjahre<extra></extra>",
    ))
    fig.update_layout(hovermode="closest", showlegend=False)
    fig.update_xaxes(title="Alter")
    fig.update_yaxes(title="Jahre ohne Partien (%)", rangemode="tozero")
    return fig


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**_):
    df = gp.load_gm_years()
    act = gp.age_curve(df, True)
    pa = gp.peak_age(act)
    n_gm = df["fide_id"].nunique()
    y0, y1 = int(df["year"].min()), int(df["year"].max()) + 1
    inact_30 = gp.inactive_share(df).query("30 <= age <= 39")["share"].mean()
    t = df[df["active"] & df["age"].between(30, 45)]
    before = t[t["year"].between(2012, 2021)]["delta"].mean()
    after = t[t["year"] >= 2022]["delta"].mean()
    lvl40 = {l: gp.points_below_peak(gp.age_curve(df[df["level"] == l], True, min_n=25), 40)
             for l in ("2500–2599", "2700+")}
    lvl_txt = f"{lvl40['2700+']} statt {lvl40['2500–2599']} Punkte".replace("-", "−")

    return html.Div(
        [
            html.Div("GM · Karriere", className="gp-kicker"),
            html.H2("Wann ist ein Großmeister am stärksten?", className="page-title"),
            html.P(
                f"Alle {n_gm:,} ".replace(",", ".")
                + f"Großmeister der aktuellen FIDE-Liste, ihre Ratings Jahr für Jahr von {y0} "
                f"bis {y1}. Wann geht es nach oben, wann bergab — und wie schnell?",
                className="gp-lead",
            ),
            html.Div(
                [
                    _kpi(f"{pa}", "Jahre — dann ist der typische GM am stärksten"),
                    _kpi(f"−{abs(gp.points_below_peak(act, 40))}", "Punkte unter dem Höchstwert mit 40"),
                    _kpi(f"−{abs(gp.points_below_peak(act, 50))}", "Punkte mit 50"),
                    _kpi(f"{inact_30:.0%}".replace("%", " %"), "der Jahre ohne Partien zwischen 30 und 39"),
                ],
                className="gp-kpis",
            ),

            html.Div(
                [
                    html.H4("Die Karrierekurve", className="gp-h"),
                    html.P(
                        "Bis 20 steigt das Rating steil, danach immer langsamer. Ab Mitte 20 ist die "
                        f"Kurve fast flach, mit {pa} erreicht sie ihren Höchstpunkt. Danach geht es "
                        "langsam, aber stetig bergab. Die gestrichelte Linie zählt auch Jahre ohne "
                        "Partien mit — wer nicht spielt, verliert nichts, deshalb sinkt sie flacher.",
                        className="gp-text",
                    ),
                    html.Div(
                        [
                            html.Label("Großmeister einblenden:", className="gp-label"),
                            dcc.Dropdown(id="gp-player", options=gp.player_options(),
                                         value=DEFAULT_PLAYER, placeholder="Name eingeben …",
                                         clearable=True, style={"minWidth": "320px"}),
                        ],
                        className="gp-control",
                    ),
                    _graph(fig_career(df, DEFAULT_PLAYER), "gp-career"),
                    html.P(
                        "Beim eingeblendeten Spieler ist der Höchstwert der höchste seit 2009 — "
                        "bei Älteren liegt der echte Höchstwert oft davor.",
                        className="gp-note",
                    ),
                ],
                className="gp-section",
            ),

            _section(
                "Jahr für Jahr",
                "Die mittlere Rating-Veränderung pro Lebensjahr, nur Jahre mit Partien. "
                "Mit 14 sind es noch fast 100 Punkte im Jahr, mit 20 noch 20. Ab 28 wird das "
                "Vorzeichen negativ — und bleibt es.",
                _graph(fig_yearly(df)),
            ),

            _section(
                "Auch die Elite altert",
                f"Aufgeteilt nach dem Höchstwert seit 2009. Alle drei Gruppen sind mit 26 oder 27 "
                f"am stärksten, und danach verlaufen die Kurven fast gleich. Die 2700er verlieren "
                f"bis 40 sogar etwas mehr ({lvl_txt}). Für sie reichen die Daten nur bis Mitte 40.",
                _graph(fig_levels(df)),
            ),

            _section(
                "Seit 2022 wird es härter",
                f"Erwachsene Großmeister (30–45 Jahre) verloren von 2012 bis 2021 im Schnitt "
                f"{abs(before):.0f} Punkte pro Jahr, seit 2022 sind es {abs(after):.0f}. Das liegt "
                "nicht am Alter, sondern an der Zeit. Nach der Corona-Pause kamen viele junge "
                "Spieler mit zu niedrigem Rating zurück ans Brett und nahmen den Etablierten "
                "Punkte ab — die naheliegende Erklärung, belegt ist sie hier nicht. Ein Teil des "
                "„Alterns“ in der Kurve oben ist deshalb Zeitgeist.",
                _graph(fig_time(df)),
            ),

            _section(
                "Weniger spielen",
                "Anteil der Jahre, in denen sich das Rating nicht verändert hat — praktisch "
                "immer, weil keine gewertete Partie gespielt wurde. Mit 20 ist das selten, "
                "mit 40 jedes vierte Jahr, ab 60 jedes dritte.",
                _graph(fig_inactive(df)),
            ),

            html.Details(
                [
                    html.Summary("Wie gerechnet wurde"),
                    html.Ul([
                        html.Li("Grundlage: alle Spieler mit GM-Titel und Geburtsjahr in der neuesten "
                                "FIDE-Liste, ihre Ratings aus den monatlichen FIDE-Listen ab 2009."),
                        html.Li("Je Spieler und Jahr die Veränderung von Januar zu Januar. Alter = "
                                "Kalenderjahr minus Geburtsjahr."),
                        html.Li("Die Karrierekurve summiert die mittlere Veränderung je Alter auf "
                                "(mindestens 50 Spielerjahre je Alter). Das typische Peak-Alter ist "
                                "ihr Maximum. Den Höchstwert je Spieler direkt zu messen geht nicht: "
                                "Für Ältere fehlt der Karrierebeginn vor 2009, für Junge das Ende."),
                        html.Li("Jahre ohne jede Rating-Veränderung gelten als Jahre ohne Partien."),
                        html.Li("Wer im Abstieg aufhört, fehlt in späteren Jahren. Die Kurve ist also "
                                "die Kurve derer, die weiterspielen."),
                        html.Li("Nur Großmeister: Wer den Titel nie geschafft hat, ist nicht dabei. "
                                "Unter den GMs sind nur 44 Frauen — zu wenige für einen eigenen Vergleich."),
                        html.Li("Generationen lassen sich in diesem Zeitfenster nicht sauber trennen: "
                                "Jüngere Jahrgänge waren genau in den härteren Jahren ab 2022 erwachsen."),
                    ]),
                ],
                className="gp-method",
            ),
        ],
        className="gp-page",
    )


@callback(Output("gp-career", "figure"), Input("gp-player", "value"))
def update_career(fide_id):
    return fig_career(gp.load_gm_years(), fide_id)
