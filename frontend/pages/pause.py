"""Story E2: Die Pause und ihre Folgen (Daten: data_covid.py, gebaut von
scripts/analysis/build_covid_story.py)."""
import dash
import numpy as np
import pandas as pd
import plotly.graph_objects as go
from dash import Input, Output, callback, dcc, html

import data_covid as cv

dash.register_page(
    __name__,
    path="/pause",
    name="Die Pause",
    title="ELO-Einsichten | Die Pause und ihre Folgen",
    order=14,
)

# Kategorische Reihen in fester Reihenfolge (dataviz-Referenzpalette, light)
SERIES = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
C_MUTED, C_GRID, C_NORMAL = "#8a8985", "#ecebe8", "#c9c8c4"
# Divergierend: Verlust rot ↔ grau ↔ Gewinn blau
DIVERGING = [[0.0, "#b8312f"], [0.25, "#e98584"], [0.5, "#f0efec"], [0.75, "#86b6ef"], [1.0, "#1c5cab"]]
MIN_GAMES = 300   # Matrixfelder mit weniger Partien bleiben leer
PAUSE = ("2020-04-01", "2021-07-01")
CORRECTION = "2024-03-01"


def _colors(dim: str) -> dict:
    return dict(zip(cv.order(dim), SERIES))


def _layout(fig: go.Figure, height: int) -> go.Figure:
    fig.update_layout(
        height=height, margin=dict(l=60, r=24, t=40, b=48),
        paper_bgcolor="white", plot_bgcolor="white", font=dict(size=13, color="#333"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, x=0),
    )
    fig.update_xaxes(gridcolor=C_GRID, zeroline=False)
    fig.update_yaxes(gridcolor=C_GRID, zeroline=False)
    return fig


def _marks(fig: go.Figure, correction=True) -> None:
    fig.add_vrect(x0=PAUSE[0], x1=PAUSE[1], fillcolor="#f2f1ee", line_width=0, layer="below")
    fig.add_annotation(x=PAUSE[0], y=1, yref="paper", text="Pause", showarrow=False,
                       xanchor="left", yanchor="top", xshift=4, font=dict(size=11, color="#666"))
    if correction:
        fig.add_vline(x=CORRECTION, line=dict(color=C_MUTED, width=1, dash="dot"))
        fig.add_annotation(x=CORRECTION, y=1, yref="paper", text="FIDE-Korrektur", showarrow=False,
                           xanchor="left", yanchor="top", xshift=4, font=dict(size=11, color="#666"))


def _graph(gid: str, fig=None) -> dcc.Graph:
    kw = {"figure": fig} if fig is not None else {}
    return dcc.Graph(id=gid, config={"displayModeBar": False}, **kw)


def _kpi(value: str, label: str) -> html.Div:
    return html.Div([html.Div(value, className="gp-kpi-value"),
                     html.Div(label, className="gp-kpi-label")], className="gp-kpi")


def _pct(x: float) -> str:
    return f"{x * 100:.0f} %"


def _de(x: float, nd: int = 1) -> str:
    return f"{x:.{nd}f}".replace(".", ",")


_MONATE = ["Januar", "Februar", "März", "April", "Mai", "Juni", "Juli", "August", "September",
           "Oktober", "November", "Dezember"]


def _monat(ts) -> str:
    return f"{_MONATE[ts.month - 1]} {ts.year}"


def _int(x: float) -> str:
    return f"{x:,.0f}".replace(",", ".")


# ---------------------------------------------------------------------------
# Diagramme
# ---------------------------------------------------------------------------

def fig_activity(dim: str) -> go.Figure:
    a = cv.activity()
    fig = go.Figure()
    if dim == "all":
        t = a[a["dim"] == "all"].sort_values("period")
        fig.add_trace(go.Scatter(
            x=t["period"], y=t["n"], line=dict(color=SERIES[0], width=2), name="Spieler mit Partien",
            hovertemplate="%{x|%m/%Y}: %{y:,.0f} Spieler<extra></extra>"))
        fig.update_yaxes(title="Spieler mit gewerteten Partien", rangemode="tozero", separatethousands=True)
        fig.update_layout(showlegend=False)
    else:
        colors = _colors(dim)
        for grp in cv.order(dim):
            t = a[(a["dim"] == dim) & (a["group"] == grp)].sort_values("period")
            y = t["index"].rolling(3, min_periods=1).mean()
            fig.add_trace(go.Scatter(
                x=t["period"], y=y, name=grp, line=dict(color=colors[grp], width=2),
                hovertemplate=grp + ": %{y:.0f}<extra></extra>"))
        fig.add_hline(y=100, line=dict(color=C_MUTED, width=1))
        fig.update_yaxes(title="Aktive Spieler (2019 = 100, 3-Monats-Mittel)", rangemode="tozero")
    _layout(fig, 380)
    fig.update_layout(hovermode="x unified")
    _marks(fig)
    return fig


def fig_returns(dim: str) -> go.Figure:
    groups = ["alle"] if dim == "all" else cv.order(dim)
    d = "all" if dim == "all" else dim
    normal = [cv.return_share(d, g, "normal") * 100 for g in groups]
    corona = [cv.return_share(d, g, "corona") * 100 for g in groups]
    fig = go.Figure()
    fig.add_trace(go.Bar(x=groups, y=normal, name="normal (Stichtag März 2017)", marker_color=C_NORMAL,
                         marker_line_width=0, text=[f"{v:.0f} %" for v in normal], textposition="outside",
                         cliponaxis=False, hovertemplate="normal: %{y:.0f} %<extra>%{x}</extra>"))
    fig.add_trace(go.Bar(x=groups, y=corona, name="nach der Pause (Stichtag März 2020)", marker_color=SERIES[1],
                         marker_line_width=0, text=[f"{v:.0f} %" for v in corona], textposition="outside",
                         cliponaxis=False, hovertemplate="nach der Pause: %{y:.0f} %<extra>%{x}</extra>"))
    _layout(fig, 360)
    fig.update_layout(barmode="group", bargap=0.3, bargroupgap=0.06)
    fig.update_yaxes(title="nach 33 Monaten wieder aktiv (%)", range=[0, 108])
    fig.update_xaxes(title=None if dim == "all" else ("Alter 2017 bzw. 2020" if dim == "age" else "Rating"))
    return fig


def fig_matrix(dim: str, phase: str, metric: str) -> go.Figure:
    f = cv.flows()
    f = f[(f["dim"] == dim) & (f["phase"] == phase)]
    groups = cv.order(dim)
    val = "mean" if metric == "game" else "per_year"
    z = np.full((len(groups), len(groups)), np.nan)
    n = np.zeros_like(z)
    for i, own in enumerate(groups):
        for j, opp in enumerate(groups):
            row = f[(f["own"] == own) & (f["opp"] == opp)]
            if len(row) and row["n"].iloc[0] >= MIN_GAMES:
                z[i, j] = row[val].iloc[0] / (1 if metric == "game" else 1000)
                n[i, j] = row["n"].iloc[0]
    lim = np.nanmax(np.abs(z)) if np.isfinite(z).any() else 1
    fmt = (lambda v: f"{v:+.1f}".replace(".", ",")) if metric == "game" else (lambda v: f"{v:+.0f}")
    text = [[("" if np.isnan(v) else fmt(v)) for v in r] for r in z]
    unit = "Punkte pro Partie" if metric == "game" else "Tsd. Punkte pro Jahr"
    fig = go.Figure(go.Heatmap(
        z=z, x=groups, y=groups, colorscale=DIVERGING, zmid=0, zmin=-lim, zmax=lim,
        text=text, texttemplate="%{text}", textfont=dict(size=12), customdata=n, xgap=2, ygap=2,
        colorbar=dict(title=dict(text=unit, side="right"), thickness=12),
        hovertemplate="%{y} gegen %{x}: %{z:+.2f} " + unit + "<br>%{customdata:,.0f} Partien<extra></extra>",
    ))
    _layout(fig, 420)
    fig.update_xaxes(title=f"Gegner ({cv.DIM_LABEL[dim]})", side="bottom", showgrid=False)
    fig.update_yaxes(title=f"Spieler ({cv.DIM_LABEL[dim]})", autorange="reversed", showgrid=False)
    return fig


def fig_series(dim: str) -> go.Figure:
    s = cv.series()
    s = s[s["dim"] == dim]
    colors = _colors(dim)
    fig = go.Figure()
    for grp in cv.order(dim):
        t = s[s["group"] == grp].sort_values("date")
        fig.add_trace(go.Scatter(
            x=t["date"], y=t["mean"], name=grp, mode="lines+markers",
            line=dict(color=colors[grp], width=2), marker=dict(size=7), customdata=t["n"],
            hovertemplate=grp + ": %{y:+.2f} Punkte/Partie (%{customdata:,.0f} Partien)<extra></extra>"))
    fig.add_hline(y=0, line=dict(color=C_MUTED, width=1))
    _layout(fig, 380)
    fig.update_layout(hovermode="x unified")
    fig.update_yaxes(title="Ø Rating-Änderung pro Partie (K × Δ)")
    _marks(fig)
    return fig


# ---------------------------------------------------------------------------
# Layout
# ---------------------------------------------------------------------------

def layout(**_):
    a = cv.activity()
    t = a[a["dim"] == "all"].set_index("period")
    base, low, low_at = t["base"].iloc[0], t["n"].min(), t["n"].idxmin()
    back90 = t["index"].rolling(3).mean()[t.index >= "2020-06-01"].ge(90).idxmax()
    ret_c, ret_n = cv.return_share("all", "alle", "corona"), cv.return_share("all", "alle", "normal")
    r = cv.returns()
    ret_now = r[(r["dim"] == "all") & (r["cohort"] == "corona")].sort_values("months")["share"].iloc[-1]
    gap = {g: cv.return_share("rating", g, "normal") - cv.return_share("rating", g, "corona")
           for g in cv.RATING_ORDER}
    gap_age = {g: cv.return_share("age", g, "normal") - cv.return_share("age", g, "corona")
               for g in cv.AGE_ORDER}
    k = {ph: cv.flow_value("age", ph, "U15", "60+") for ph in cv.PHASE_ORDER}
    k_back = {ph: cv.flow_value("age", ph, "60+", "U15") for ph in cv.PHASE_ORDER}
    f = cv.flows()
    fa = f[f["dim"] == "age"]
    young, old = ["U15", "15–19"], ["40–59", "60+"]
    win = fa[(fa["phase"] == "Welle") & fa["own"].isin(young) & fa["opp"].isin(old)]["per_year"].sum()
    loss = fa[(fa["phase"] == "Welle") & fa["own"].isin(old) & fa["opp"].isin(young)]["per_year"].sum()

    def mio(x):
        return _de(abs(x) / 1e6, 2) + " Mio."

    phase_opts = [{"label": f"{p} ({cv.PHASE_SPAN[p]})", "value": p} for p in cv.PHASE_ORDER]

    return html.Div(
        [
            html.Div("Corona · 2020–2026", className="gp-kicker"),
            html.H2("Die Pause und ihre Folgen", className="page-title"),
            html.P(
                "Im Frühjahr 2020 kam das gewertete Schach weltweit fast zum Stillstand. "
                "Wer kam zurück, wer blieb weg — und wer hat danach wem die Punkte abgenommen? "
                "Grundlage sind die monatlichen FIDE-Listen und 17,6 Millionen Partien seit 2020.",
                className="gp-lead",
            ),
            html.Div(
                [
                    _kpi(f"{(1 - low / base) * 100:.0f} %".replace(".", ","),
                         f"weniger aktive Spieler im {_monat(low_at)} als im Schnitt 2019"),
                    _kpi(_pct(ret_c), f"spielten innerhalb von 33 Monaten wieder — normal sind {_pct(ret_n)}"),
                    _kpi(_de(k["Rückkehr"]),
                         f"Punkte pro Partie nahmen Unter-15-Jährige den Über-60-Jährigen 2021/22 ab "
                         f"(vorher {_de(k['Vor der Pause'])})"),
                    _kpi(_de(k["Nach der Korrektur"]), "Punkte pro Partie nach der FIDE-Korrektur 2024"),
                ],
                className="gp-kpis",
            ),

            html.Div(
                [
                    html.Label("Gruppen nach:", className="gp-label"),
                    dcc.RadioItems(id="pz-dim", options=[{"label": " Alter", "value": "age"},
                                                         {"label": " Rating", "value": "rating"}],
                                   value="age", inline=True, inputStyle={"marginLeft": "12px"}),
                ],
                className="gp-control pz-sticky",
            ),

            html.Div([
                html.H4("1 · Der Stillstand", className="gp-h"),
                html.P(
                    f"2019 spielten jeden Monat im Schnitt {_int(base)} Spieler eine gewertete Partie. "
                    f"Im {_monat(low_at)} waren es {_int(low)}. Erst ab {_monat(back90)} lag die Aktivität "
                    "wieder bei mindestens 90 % des alten Niveaus. Heute spielen mehr Menschen gewertet "
                    "als vor der Pause, vor allem Junge. Die obere Kurve zeigt alle, die untere die "
                    "gewählten Gruppen im Vergleich zu 2019.",
                    className="gp-text"),
                _graph("pz-activity-all", fig_activity("all")),
                _graph("pz-activity"),
            ], className="gp-section"),

            html.Div([
                html.H4("2 · Wer kam zurück — und wer nicht", className="gp-h"),
                html.P(
                    f"Von allen, die zwischen Februar 2019 und März 2020 gewertet spielten, hatten nach 33 "
                    f"Monaten {_pct(ret_c)} wieder eine gewertete Partie gespielt. Bei einer Gruppe vor "
                    f"Corona (Stichtag März 2017) waren es im gleichen Zeitraum {_pct(ret_n)}. Bis heute sind "
                    f"{_pct(ret_now)} zurückgekehrt — jeder Vierte nicht. Am stärksten traf es die "
                    f"schwächeren Spieler: Unter 1400 fehlten {gap['<1400'] * 100:.0f} Prozentpunkte "
                    f"gegenüber normal, ab 2200 nur {gap['2200+'] * 100:.0f}. Nach Alter am stärksten die "
                    f"15- bis 19-Jährigen ({gap_age['15–19'] * 100:.0f} Punkte), die ohnehin am häufigsten "
                    "aufhören.",
                    className="gp-text"),
                _graph("pz-returns"),
            ], className="gp-section"),

            html.Div([
                html.H4("3 · Wer wem die Punkte abnahm", className="gp-h"),
                html.P(
                    "Jede Zeile ist eine Spielergruppe, jede Spalte die Gruppe ihrer Gegner. Blau: Die Zeile "
                    "gewinnt im Schnitt Punkte gegen diese Gegner, rot: sie verliert. Rating-Punkte fließen "
                    "immer von alt nach jung, weil Jugendliche schneller besser werden, als ihr Rating "
                    f"nachkommt. Nach der Pause wurde der Strom stärker: Unter-15-Jährige nahmen "
                    f"Über-60-Jährigen vor der Pause {_de(k['Vor der Pause'])} Punkte pro Partie ab, in der "
                    f"Rückkehr {_de(k['Rückkehr'])}, nach der FIDE-Korrektur nur noch "
                    f"{_de(k['Nach der Korrektur'])}.",
                    className="gp-text"),
                html.Div([
                    html.Label("Phase:", className="gp-label"),
                    dcc.Dropdown(id="pz-phase", options=phase_opts, value="Welle", clearable=False,
                                 style={"minWidth": "300px"}),
                    html.Label("Wert:", className="gp-label"),
                    dcc.RadioItems(id="pz-metric", options=[{"label": " pro Partie", "value": "game"},
                                                            {"label": " Summe pro Jahr", "value": "year"}],
                                   value="game", inline=True, inputStyle={"marginLeft": "12px"}),
                ], className="gp-control"),
                _graph("pz-matrix"),
                html.P(
                    f"Gewinne und Verluste sind nicht gleich groß: In der Welle 2023 gewannen die Unter-20-"
                    f"Jährigen gegen die Über-40-Jährigen {mio(win)} Punkte pro Jahr, die Älteren verloren "
                    f"dabei aber nur {mio(loss)} Punkte. Grund ist der K-Faktor — Jugendliche unter 18 haben K = 40, "
                    "Erwachsene meist 20 oder 10. Jeder Sieg eines Kindes gegen einen Erwachsenen bringt dem "
                    "Kind doppelt so viele Punkte, wie der Erwachsene verliert. So entstehen Punkte aus dem "
                    "Nichts.",
                    className="gp-text"),
                html.P("Leere Felder: weniger als 300 Partien in dieser Phase. Die Phase „Vor der Pause“ "
                       "umfasst nur drei Monate; die Summe pro Jahr ist dort hochgerechnet und durch die "
                       "turnierreichen Wintermonate eher zu hoch.", className="gp-note"),
            ], className="gp-section"),

            html.Div([
                html.H4("4 · Die Welle und die Korrektur", className="gp-h"),
                html.P(
                    "Die durchschnittliche Rating-Änderung pro Partie je Gruppe, halbjährlich. Nach der "
                    "Pause legten die Jungen pro Partie deutlich mehr zu als vorher, die Älteren verloren "
                    "mehr. Im März 2024 hob die FIDE alle Ratings unter 2000 einmalig an "
                    "(+0,4 × (2000 − Rating)) — gerade die unterbewerteten Jungen rückten damit näher an "
                    "ihre Spielstärke. Seither gewinnen die Unter-15-Jährigen pro Partie nur noch rund ein "
                    "Drittel dessen, was sie vor der Pause gewannen.",
                    className="gp-text"),
                _graph("pz-series"),
                html.P("Die Korrektur selbst ist in diesen Werten nicht enthalten — sie verschob die "
                       "Ratings zwischen zwei Listen, nicht über Partien. Die Gruppe unter 1400 ist danach "
                       "fast leer, weil die Korrektur jedes Rating auf mindestens 1400 hob.", className="gp-note"),
            ], className="gp-section"),

            html.Details(
                [
                    html.Summary("Wie gerechnet wurde"),
                    html.Ul([
                        html.Li("Aktivität: bewertete Spieler mit mindestens einer gewerteten Partie in der "
                                "jeweiligen FIDE-Monatsliste (Standard)."),
                        html.Li("Rückkehr: alle, die zwischen Februar 2019 und März 2020 gespielt haben; "
                                "Vergleichsgruppe dieselbe Rechnung mit Februar 2016 bis März 2017. Gezählt "
                                "wird die erste gewertete Partie nach dem Stichtag."),
                        html.Li("Punkteströme: 17,6 Mio. Partien seit 2020 aus den FIDE-Berechnungsseiten "
                                "(ab 2021 vollständig, Abgleich mit den Partienzahlen der Listen). Wert = "
                                "Rating-Änderung der Partie inklusive K-Faktor, aus Sicht des Spielers der Zeile."),
                        html.Li("Eigenes Rating = Liste des Vormonats. Alter = Kalenderjahr minus Geburtsjahr "
                                "(aus der neuesten Liste)."),
                        html.Li("Das Alter des Gegners steht nicht in den Partiedaten. Gegner wurden über Name "
                                "und ein exakt passendes Listenrating zugeordnet — bei 80 % der Partien "
                                "eindeutig, und bei Partien mit bekannter Gegner-ID zu 99,8 % richtig. Für "
                                "Ratinggruppen ist keine Zuordnung nötig."),
                        html.Li("Die Phasen richten sich nach dem Monat der Liste, in die eine Partie einging."),
                        html.Li("Online-Partien sind in den FIDE-Standardratings nicht enthalten; die Story "
                                "misst die Folgen der Pause am Brett, nicht das Online-Spiel selbst."),
                    ]),
                ],
                className="gp-method",
            ),
        ],
        className="gp-page",
    )


@callback(Output("pz-activity", "figure"), Output("pz-returns", "figure"), Output("pz-series", "figure"),
          Input("pz-dim", "value"))
def update_dim(dim):
    return fig_activity(dim), fig_returns(dim), fig_series(dim)


@callback(Output("pz-matrix", "figure"),
          Input("pz-dim", "value"), Input("pz-phase", "value"), Input("pz-metric", "value"))
def update_matrix(dim, phase, metric):
    return fig_matrix(dim, phase, metric)
