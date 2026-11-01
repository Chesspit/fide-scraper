"""QC-Bereich im Orchestrator-Dashboard (Block 4, Phase 3b).

Liest ausschließlich über qc/ — kein eigenes QC-SQL hier:

  Audit-Läufe     qc.audit_store (gespeichert von scripts/audit_data.py --store):
                  Trend der Kopfzahlen, Checks des gewählten Laufs, Drill-down
                  nach Jahr / Föderation / Periode, Stichproben
  Einzelabfrage   qc.live.player_period — Spieler × Periode, live gerechnet
  Rating-Fenster  orchestrator/qc_pages/ (vorher Frontend-Seiten /qc,
  Fälle, 2024     /qc-cases, /qc-corrections) über qc.rating_views

Der Inhalt wird erst beim Aktivieren des Tabs gerendert (render_qc_tab), damit
das Dashboard beim Start keine QC-Abfragen absetzt.
"""

from __future__ import annotations

import json
from datetime import date

import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback, dash_table, dcc, html

from core.periods import parse_period
from orchestrator.qc_pages import cases, corrections, overview
from qc import audit_store, live
from qc.db import connect

TAB_ID = "tab-qc"

_MARK = {"ok": "✓", "info": "ℹ", "soft": "△", "hard": "✗"}
_SEV_STYLE = [
    {"if": {"filter_query": '{severity} = "hard"'}, "backgroundColor": "#FFF0F0", "color": "#C62828"},
    {"if": {"filter_query": '{severity} = "soft"'}, "backgroundColor": "#FFFDE7", "color": "#E65100"},
    {"if": {"filter_query": '{severity} = "info"'}, "color": "#1565C0"},
]
_TABLE = dict(
    style_table={"overflowX": "auto"},
    style_header={"backgroundColor": "#F0F0F0", "fontWeight": "600", "fontSize": "0.82rem"},
    style_cell={"fontSize": "0.85rem", "padding": "5px 8px", "textAlign": "left",
                "whiteSpace": "normal", "height": "auto"},
    page_size=25,
)


def _pct(v) -> str:
    return "–" if v is None else f"{v:.1f} %".replace(".", ",")


def _n(v) -> str:
    return "–" if v is None else f"{v:,}".replace(",", ".")


def _error(msg: str) -> dbc.Alert:
    return dbc.Alert(msg, color="warning", className="my-2")


# ── Layout ────────────────────────────────────────────────────────────────────

tab_qc = html.Div(id="qcui-root", children=html.Div("QC wird geladen …", className="p-3"))


def _layout_runs() -> html.Div:
    return html.Div(className="p-2", children=[
        html.P([
            "Gespeicherte Läufe von ", html.Code("scripts/audit_data.py --store"),
            " (Monatslauf Schritt 5). Ein Lauf ab 2020 dauert zu lange für eine Live-Rechnung.",
        ], className="text-muted small"),
        dcc.Graph(id="qcui-trend", style={"height": "300px"}),
        dbc.Row([
            dbc.Col(dcc.Dropdown(id="qcui-run-dd", clearable=False,
                                 placeholder="Lauf wählen …"), md=7),
            dbc.Col(html.Div(id="qcui-run-meta", className="small text-muted pt-2"), md=5),
        ], className="mb-2"),
        html.Div(id="qcui-headline", className="mb-2"),
        html.H6("Checks (Zeile wählen für Details)", className="mt-3"),
        html.Div(id="qcui-checks"),
        html.Div(id="qcui-check-detail", className="mt-3"),
        html.H6("Drill-down nach Jahr", className="mt-4"),
        html.Div(id="qcui-by-year"),
        html.H6("Drill-down nach Föderation", className="mt-4"),
        dcc.Input(id="qcui-fed-filter", placeholder="Föderation filtern, z. B. GER",
                  debounce=True, className="form-control mb-2", style={"maxWidth": "260px"}),
        html.Div(id="qcui-by-fed"),
        dcc.Store(id="qcui-run-store"),
    ])


def _layout_live() -> html.Div:
    return html.Div(className="p-2", children=[
        html.P("Live: eine Kombination Spieler × Periode gegen Liste, Scrape-Status, "
               "Partien, Kette und Elo-Formel prüfen (qc/live.py).",
               className="text-muted small"),
        dbc.Row([
            dbc.Col(dbc.Input(id="qcui-live-fid", type="number", placeholder="FIDE-ID"), md=3),
            dbc.Col(dbc.Input(id="qcui-live-period", placeholder="Periode YYYY-MM"), md=3),
            dbc.Col(dbc.Button("Prüfen", id="qcui-live-btn", color="primary"), md=2),
        ], className="mb-3"),
        dcc.Loading(html.Div(id="qcui-live-out")),
    ])


@callback(Output("qcui-root", "children"), Input("main-tabs", "active_tab"),
          State("qcui-root", "children"))
def render_qc_tab(active_tab, current):
    """Unter-Tabs beim ersten Öffnen aufbauen; danach unverändert lassen."""
    if active_tab != TAB_ID:
        return current
    if isinstance(current, dict) and current.get("props", {}).get("id") == "qcui-tabs":
        return current
    return dbc.Tabs(id="qcui-tabs", active_tab="qcui-runs", children=[
        dbc.Tab(_layout_runs(), label="Audit-Läufe", tab_id="qcui-runs"),
        dbc.Tab(_layout_live(), label="Einzelabfrage", tab_id="qcui-live"),
        dbc.Tab(_safe(overview.layout), label="Rating-Fenster", tab_id="qcui-windows"),
        dbc.Tab(_safe(cases.layout), label="Fälle", tab_id="qcui-cases"),
        dbc.Tab(_safe(corrections.layout), label="FIDE 2024 Korrekturen", tab_id="qcui-corr"),
    ])


def _safe(layout_fn):
    try:
        return layout_fn()
    except Exception as exc:  # z. B. qc_rating_check fehlt in einer frischen DB
        return _error(f"Ansicht nicht verfügbar: {exc}")


# ── Audit-Läufe ───────────────────────────────────────────────────────────────

@callback(
    Output("qcui-run-dd", "options"),
    Output("qcui-run-dd", "value"),
    Output("qcui-trend", "figure"),
    Input("qcui-tabs", "active_tab"),
)
def load_runs(sub_tab):
    fig = go.Figure()
    fig.update_layout(margin=dict(l=40, r=10, t=30, b=30), title="Trend der Gesamtläufe",
                      yaxis_title="%", legend=dict(orientation="h"))
    try:
        conn = connect()
        try:
            runs = audit_store.list_runs(conn)
            trend = audit_store.trend(conn)
        finally:
            conn.close()
    except Exception as exc:
        fig.add_annotation(text=f"Keine Audit-Läufe lesbar ({exc.__class__.__name__}) — "
                                "Migration 021 angewendet?", showarrow=False)
        return [], None, fig
    options = [{
        "label": (f"#{r['id']} {r['finished_at']:%d.%m.%Y %H:%M} · "
                  f"{r['since_period']:%Y-%m}–{r['last_period']:%Y-%m}" if r["last_period"] else
                  f"#{r['id']} {r['finished_at']:%d.%m.%Y %H:%M}")
                 + (f" · {','.join(r['federations'])}" if r["federations"] else "")
                 + (" · ✗" if r["has_hard"] else " · ✓"),
        "value": r["id"],
    } for r in runs]
    x = [t["finished_at"] for t in trend]
    for key, name in (("pct_periods_complete", "Perioden vollständig"),
                      ("pct_games_found", "Partien vorhanden"),
                      ("pct_chain_unexplained", "Kette unerklärt")):
        fig.add_trace(go.Scatter(x=x, y=[t["headline"].get(key) for t in trend],
                                 mode="lines+markers", name=name))
    return options, (runs[0]["id"] if runs else None), fig


@callback(
    Output("qcui-run-store", "data"),
    Output("qcui-run-meta", "children"),
    Output("qcui-headline", "children"),
    Output("qcui-checks", "children"),
    Output("qcui-by-year", "children"),
    Input("qcui-run-dd", "value"),
)
def show_run(run_id):
    if not run_id:
        return None, "", _error("Noch kein gespeicherter Lauf."), "", ""
    conn = connect()
    try:
        run = audit_store.get_run(conn, run_id)
    finally:
        conn.close()
    if not run:
        return None, "", _error(f"Lauf {run_id} nicht gefunden."), "", ""
    h = run["headline"]
    meta = (f"Quelle {run['source']}, {float(run['duration_s'] or 0) / 60:.1f} min, "
            f"Ebenen {','.join(map(str, run['layers']))}"
            + (f", übersprungen: {', '.join(f'{p:%Y-%m}' for p in run['skipped_periods'])}"
               if run["skipped_periods"] else ""))
    cards = dbc.Row([
        _card("Soll-Kombos", _n(h.get("soll_combos"))),
        _card("zurückgezogen", _n(h.get("withdrawn_combos"))),
        _card("gescrapt", _pct(h.get("pct_periods_scraped"))),
        _card("vollständig", _pct(h.get("pct_periods_complete"))),
        _card("Partien", _pct(h.get("pct_games_found"))),
        _card("Kette unerklärt", _pct(h.get("pct_chain_unexplained"))),
        _card("harte / weiche", f"{h.get('hard_checks', 0)} / {h.get('soft_checks', 0)}"),
    ], className="g-2")
    checks = dash_table.DataTable(
        id="qcui-checks-table",
        columns=[{"name": n, "id": i} for n, i in (
            ("", "mark"), ("Ebene", "layer"), ("Check", "id"), ("Titel", "title"),
            ("Befunde", "n_findings"), ("geprüft", "n_checked"), ("Anteil %", "pct"),
            ("severity", "severity"))],
        hidden_columns=["severity"],
        data=[{**{k: c[k] for k in ("layer", "id", "title", "n_findings", "n_checked",
                                    "pct", "severity")}, "mark": _MARK[c["severity"]]}
              for c in run["checks"]],
        row_selectable="single", style_data_conditional=_SEV_STYLE,
        css=[{"selector": ".show-hide", "rule": "display: none"}], **_TABLE,
    )
    by_year = _year_table(run["by_year"])
    store = {"by_federation": run["by_federation"], "checks": run["checks"]}
    return json.dumps(store, default=str), meta, cards, checks, by_year


def _card(label: str, value: str) -> dbc.Col:
    return dbc.Col(dbc.Card(dbc.CardBody([
        html.Div(label, className="small text-muted"),
        html.Div(value, className="fs-5 fw-bold"),
    ]), className="text-center"), md=True)


def _ratio(a, b) -> float | None:
    return round(100.0 * a / b, 1) if b else None


def _year_table(by_year: dict) -> dash_table.DataTable:
    rows = []
    for y, v in sorted(by_year.items()):
        soll, chain = v.get("soll", 0), v.get("chain_checked", 0)
        rows.append({
            "Jahr": y, "Soll": soll, "fehlt": v.get("missing", 0),
            "zurückgezogen": v.get("withdrawn", 0),
            "no_data/error": v.get("listed_no_data", 0) + v.get("listed_error", 0),
            "gescrapt %": _ratio(v.get("ok", 0), soll),
            "vollständig %": _ratio(v.get("complete", 0), soll),
            "Partien %": _ratio(v.get("found_games", 0), v.get("listed_games", 0)),
            "Kette unerklärt %": _ratio(v.get("chain_unexplained", 0), chain),
        })
    return dash_table.DataTable(columns=[{"name": k, "id": k} for k in (rows[0] if rows else {})],
                                data=rows, sort_action="native", **_TABLE)


@callback(Output("qcui-by-fed", "children"),
          Input("qcui-run-store", "data"), Input("qcui-fed-filter", "value"))
def show_by_federation(store, fed_filter):
    if not store:
        return ""
    by_fed = json.loads(store)["by_federation"]
    flt = (fed_filter or "").strip().upper()
    rows = []
    for fed, v in by_fed.items():
        if flt and flt not in fed:
            continue
        soll = v.get("soll", 0)
        rows.append({
            "Föd.": fed, "Soll": soll, "fehlt": v.get("missing", 0),
            "gescrapt %": _ratio(v.get("ok", 0), soll),
            "Partien %": _ratio(v.get("found_games", 0), v.get("listed_games", 0)),
            "Spieler nie gescrapt": v.get("players_never", 0), "Spieler": v.get("players", 0),
        })
    rows.sort(key=lambda r: -r["fehlt"])
    return dash_table.DataTable(columns=[{"name": k, "id": k} for k in (rows[0] if rows else {})],
                                data=rows, sort_action="native", **_TABLE)


@callback(Output("qcui-check-detail", "children"),
          Input("qcui-checks-table", "selected_rows"), State("qcui-checks-table", "data"),
          State("qcui-run-store", "data"))
def show_check(selected, data, store):
    if not selected or not store:
        return ""
    cid = data[selected[0]]["id"]
    c = next(x for x in json.loads(store)["checks"] if x["id"] == cid)
    parts = [html.H6(f"{_MARK[c['severity']]} {c['id']} — {c['title']}")]
    if c.get("note"):
        parts.append(html.P(c["note"], className="small"))
    if c.get("fix_hint"):
        parts.append(html.P([html.B("Fix: "), c["fix_hint"]], className="small"))
    if c.get("worst_periods"):
        parts.append(html.P("Schlechteste Perioden: " + ", ".join(
            f"{p[:7]} ({_pct(v)})" for p, v in c["worst_periods"]), className="small"))
    if c.get("by_year"):
        parts.append(html.P("Nach Jahr: " + ", ".join(
            f"{y}: {_n(n)}" for y, n in sorted(c["by_year"].items())), className="small"))
    if c.get("by_federation"):
        top = sorted(c["by_federation"].items(), key=lambda kv: -kv[1])[:15]
        parts.append(html.P("Top-Föderationen: " + ", ".join(f"{k} {_n(v)}" for k, v in top),
                            className="small"))
    sample = c.get("sample") or []
    if sample:
        keys = list(dict.fromkeys(k for s in sample for k in s))
        parts.append(dash_table.DataTable(
            columns=[{"name": k, "id": k} for k in keys],
            data=[{k: (json.dumps(v, ensure_ascii=False) if isinstance(v, (dict, list)) else v)
                   for k, v in s.items()} for s in sample],
            filter_action="native", sort_action="native", **_TABLE))
    return dbc.Card(dbc.CardBody(parts))


# ── Einzelabfrage ─────────────────────────────────────────────────────────────

@callback(Output("qcui-live-out", "children"), Input("qcui-live-btn", "n_clicks"),
          State("qcui-live-fid", "value"), State("qcui-live-period", "value"),
          prevent_initial_call=True)
def live_check(_, fide_id, period_str):
    if not fide_id or not period_str:
        return _error("FIDE-ID und Periode (YYYY-MM) angeben.")
    try:
        period = parse_period(period_str)
    except ValueError as exc:
        return _error(str(exc))
    conn = connect()
    try:
        r = live.player_period(conn, int(fide_id), period)
    finally:
        conn.close()
    return _render_live(r)


def _kv(rows: list[tuple[str, object]]) -> dbc.Table:
    return dbc.Table([html.Tbody([html.Tr([html.Th(k), html.Td("–" if v is None else str(v))])
                                  for k, v in rows])], size="sm", bordered=True,
                     style={"maxWidth": "640px"})


def _render_live(r: dict) -> html.Div:
    p, ls, sc, ch = r["player"] or {}, r["listed"] or {}, r["scrape"] or {}, r["chain"]
    color = {"hard": "danger", "soft": "warning", "info": "info", "ok": "success"}
    out = [html.H6(f"{p.get('name', '?')} ({r['fide_id']}, {p.get('federation', '?')}) — "
                   f"{r['period']:%Y-%m}")]
    out += [dbc.Alert(f"{_MARK[s]} {t}", color=color[s], className="py-1 my-1") for s, t in r["findings"]]
    out.append(_kv([
        ("Liste: Rating / Partien / Ro", f"{ls.get('published_rating')} / {ls.get('num_games')} / "
                                         f"{ls.get('std_rating')}"),
        ("Liste Vorperiode", (r["listed_prev"] or {}).get("published_rating")),
        ("Scrape-Status", " · ".join(str(x) for x in (
            sc.get("status"), sc.get("k_factor") and f"K={sc['k_factor']}",
            sc.get("no_data_reason"), sc.get("http_status") and f"HTTP {sc['http_status']}") if x)
            if sc else "nicht gescrapt"),
        ("Partien gescrapt", len(r["games"])),
        ("neueste Liste / steht drin", f"{r['latest_list']} / {'ja' if r['in_latest_list'] else 'nein'}"),
        ("Kette ΔListe − ΣK×Δ − Korrektur",
         f"{ch['list_delta']} − {ch['sum_weighted']} − {ch['correction']} = {ch['residual']:+.2f}"
         if ch else "nicht prüfbar"),
        ("Elo-Formel prüfbar", "ja (ein Turnier, Ro bekannt)" if r["formula_checkable"] else "nein"),
    ]))
    if r["games"]:
        cols = ["game_index", "opponent_name", "opponent_rating", "result", "rating_change",
                "rating_change_weighted", "expected", "formula_ok", "tournament_name"]
        out.append(dash_table.DataTable(
            columns=[{"name": c, "id": c} for c in cols],
            data=[{c: (str(g[c]) if isinstance(g[c], (bool, date)) else
                       (float(g[c]) if g[c] is not None and c in ("rating_change", "rating_change_weighted")
                        else g[c])) for c in cols} for g in r["games"]],
            style_data_conditional=[{"if": {"filter_query": '{formula_ok} = "False"'},
                                     "backgroundColor": "#FFF0F0"}],
            **_TABLE))
    return html.Div(out)
