"""Scraping Orchestrator — Web Dashboard (3 Tabs).

Tab 1 – Heatmap:   Föderations-Grid, Worker-Steuerung
Tab 2 – Queue:     Prioritätsliste (pending/running/failed), Priorität editierbar
Tab 3 – Completed: Abgeschlossene Gruppen mit Scraping-Statistiken

Run:  python orchestrator/app.py
Then open http://localhost:8050
"""

import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback_context, dash_table, dcc, html

from orchestrator.profile_manager import ProfileManager
from orchestrator.setup_db import DB_PATH, create_db

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
STATUS_CODE = {"pending": 0, "running": 1, "done": 2, "failed": 3, "skipped": 4}
STATUS_COLOR = {
    "pending": "#D0D0D0",
    "running": "#EF9F27",
    "done":    "#1D9E75",
    "failed":  "#E24B4A",
    "skipped": "#9E9E9E",
}
NO_DATA_CODE = -1

# z-Werte: -1=keine Daten, 0=pending, 1=running, 2=done, 3=failed, 4=skipped
# Normalisiert auf [0,1] mit zmin=-1, zmax=4 → Schrittweite 0.2
# Grenzen bei Mittelpunkten: 0.1, 0.3, 0.5, 0.7, 0.9
COLORSCALE = [
    [0.0, "#F0F0F0"], [0.1, "#F0F0F0"],  # z=-1: keine Daten
    [0.1, "#D0D0D0"], [0.3, "#D0D0D0"],  # z=0:  pending (grau)
    [0.3, "#EF9F27"], [0.5, "#EF9F27"],  # z=1:  running (orange)
    [0.5, "#1D9E75"], [0.7, "#1D9E75"],  # z=2:  done (grün)
    [0.7, "#E24B4A"], [0.9, "#E24B4A"],  # z=3:  failed (rot)
    [0.9, "#9E9E9E"], [1.0, "#9E9E9E"],  # z=4:  skipped (grau)
]

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
WORKER_STATE_PATH = _DATA_DIR / "worker_state.json"

OVERVIEW_FEDERATIONS = ["GER", "SUI", "AUT", "POL", "UKR", "NOR",
                        "·1", "·2", "·3", "·4", "·5", "·6", "·7", "·8"]

pm = ProfileManager()

# Fuzzy-Label aus aktuellen Gewichten bauen (wird bei App-Start einmalig gelesen)
def _fuzzy_label() -> str:
    weights_cfg = pm._data.get("fuzzy_weights", {})
    parts = [f"{p[0].upper()}{weights_cfg.get(p, 0)}%" for p in ["conservative", "normal", "aggressive"]]
    return f"Fuzzy ({' / '.join(parts)})"

FUZZY_LABEL = _fuzzy_label()


# ---------------------------------------------------------------------------
# DB helpers — shared
# ---------------------------------------------------------------------------
def get_conn():
    import sqlite3
    conn = create_db(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def query_continents() -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT continent FROM scrape_groups ORDER BY continent"
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def query_federations(continent: str) -> list[str]:
    conn = get_conn()
    rows = conn.execute(
        "SELECT DISTINCT federation FROM scrape_groups WHERE continent=? ORDER BY federation",
        (continent,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def query_overview() -> list[dict]:
    placeholders = ",".join("?" * len(OVERVIEW_FEDERATIONS))
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT federation,
                   (elo_min / 50) * 50 AS elo_bucket,
                   COUNT(*) AS total,
                   SUM(CASE WHEN status='done' THEN 1 ELSE 0 END) AS done_count
            FROM scrape_groups
            WHERE federation IN ({placeholders})
            GROUP BY federation, elo_bucket
            ORDER BY federation, elo_bucket DESC""",
        OVERVIEW_FEDERATIONS,
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_grid(federation: str) -> list[dict]:
    conn = get_conn()
    rows = conn.execute(
        """SELECT year, elo_min, elo_max, status, player_count,
                  records_found, retries, last_run_at, id
           FROM scrape_groups
           WHERE federation = ?
           ORDER BY elo_min DESC, year""",
        (federation,),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def query_global_stats() -> dict:
    conn = get_conn()
    rows = conn.execute(
        "SELECT status, COUNT(*) n FROM scrape_groups GROUP BY status"
    ).fetchall()
    conn.close()
    d = {r[0]: r[1] for r in rows}
    total = sum(d.values())
    return {
        "total":   total,
        "done":    d.get("done", 0),
        "pending": d.get("pending", 0),
        "running": d.get("running", 0),
        "failed":  d.get("failed", 0),
        "skipped": d.get("skipped", 0),
    }


def query_group_by_id(group_id: int) -> dict | None:
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM scrape_groups WHERE id=?", (group_id,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def update_group_status(group_id: int, new_status: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_groups SET status=? WHERE id=?", (new_status, group_id)
    )
    conn.commit()
    conn.close()


def update_group_priority(group_id: int, new_priority: int) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_groups SET priority=? WHERE id=?", (new_priority, group_id)
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# DB helpers — Tab 2: Queue
# ---------------------------------------------------------------------------
def query_queue() -> list[dict]:
    """Top 500 non-done, non-skipped groups sorted by priority (ascending = highest first)."""
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT id, priority, federation, continent, year,
                  elo_min || '–' || elo_max AS elo_band,
                  player_count, status, retries,
                  COALESCE(device, '') AS device,
                  COALESCE(profile, '') AS profile,
                  CASE COALESCE(profile, '')
                      WHEN 'conservative' THEN 'Langsam · Proxy immer'
                      WHEN 'normal'       THEN 'Normal · Proxy aktiv'
                      WHEN 'aggressive'   THEN 'Schnell · kein Proxy'
                      ELSE '{FUZZY_LABEL}'
                  END AS taktik,
                  COALESCE(last_run_at, '–') AS last_run_at
           FROM scrape_groups
           WHERE status IN ('pending', 'running', 'failed')
           ORDER BY priority ASC, federation ASC
           LIMIT 500""",
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def update_group_profile_db(group_id: int, profile: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_groups SET profile=? WHERE id=?",
        (profile if profile else None, group_id),
    )
    conn.commit()
    conn.close()


def update_group_device(group_id: int, device: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_groups SET device=? WHERE id=?",
        (device if device else None, group_id),
    )
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# DB helpers — Tab 3: Completed
# ---------------------------------------------------------------------------
def _fmt_dt(s: str | None) -> str:
    if not s:
        return "–"
    try:
        from datetime import datetime
        return datetime.fromisoformat(s.replace("T", " ")[:16]).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return s


def query_completed() -> list[dict]:
    """Done groups with scraping stats from scrape_runs."""
    conn = get_conn()
    rows = conn.execute(
        """SELECT
               g.federation,
               g.continent,
               g.year,
               g.elo_min || '–' || g.elo_max               AS elo_band,
               g.player_count,
               g.records_found,
               r.profile_used,
               CASE
                   WHEN g.player_count > 0 AND g.records_found > 0
                   THEN ROUND(CAST(g.records_found AS REAL) / g.player_count, 1)
                   ELSE NULL
               END                                          AS partien_per_spieler,
               ROUND(COALESCE(r.mb_downloaded, 0), 1)      AS mb,
               g.last_run_at,
               ROUND(
                   (julianday(r.finished_at) - julianday(r.started_at)) * 24, 2
               )                                            AS duration_h,
               CASE
                   WHEN r.finished_at IS NOT NULL
                        AND r.started_at IS NOT NULL
                        AND (julianday(r.finished_at) - julianday(r.started_at)) > 0
                   THEN ROUND(
                       r.records_found /
                       ((julianday(r.finished_at) - julianday(r.started_at)) * 24),
                       0)
                   ELSE NULL
               END                                          AS rate_per_h,
               g.id
           FROM scrape_groups g
           LEFT JOIN (
               SELECT group_id,
                      started_at, finished_at,
                      records_found, profile_used, proxy_used,
                      COALESCE(mb_downloaded, 0) AS mb_downloaded,
                      ROW_NUMBER() OVER (PARTITION BY group_id
                                        ORDER BY finished_at DESC) AS rn
               FROM scrape_runs
               WHERE status = 'success'
           ) r ON r.group_id = g.id AND r.rn = 1
           WHERE g.status = 'done'
           ORDER BY g.last_run_at DESC""",
    ).fetchall()
    conn.close()
    result = [dict(r) for r in rows]
    for row in result:
        row["last_run_at"] = _fmt_dt(row.get("last_run_at"))
    return result


# ---------------------------------------------------------------------------
# Worker state helpers
# ---------------------------------------------------------------------------
def read_worker_state() -> dict:
    if WORKER_STATE_PATH.exists():
        try:
            return json.loads(WORKER_STATE_PATH.read_text())
        except Exception:
            pass
    return {"command": "stopped"}


def write_worker_state(command: str) -> None:
    state = read_worker_state()
    state["command"] = command
    WORKER_STATE_PATH.write_text(json.dumps(state))


# ---------------------------------------------------------------------------
# Heatmap figure
# ---------------------------------------------------------------------------
def build_figure(federation: str) -> go.Figure:
    rows = query_grid(federation)
    if not rows:
        return go.Figure().update_layout(title=f"No data for {federation}")

    years = sorted(set(r["year"] for r in rows), reverse=True)
    # Aufsteigend sortieren: Plotly zeigt y[0] unten, y[-1] oben → hohe ELO oben
    bands = sorted(set(r["elo_min"] for r in rows), reverse=False)
    band_labels = []
    for bmin in bands:
        bmax = next(r["elo_max"] for r in rows if r["elo_min"] == bmin)
        band_labels.append(f"{bmin}–{bmax}")

    lookup = {(r["elo_min"], r["year"]): r for r in rows}

    z, text, ids = [], [], []
    for bmin in bands:
        z_row, text_row, id_row = [], [], []
        for yr in years:
            r = lookup.get((bmin, yr))
            if r:
                z_row.append(STATUS_CODE.get(r["status"], NO_DATA_CODE))
                icon = {"done": " 🔒", "running": " ⏳", "failed": " ❌"}.get(r["status"], "")
                text_row.append(
                    f"{r['status']}{icon}<br>"
                    f"Spieler: {r['player_count']}<br>"
                    f"Partien: {r['records_found'] or '–'}<br>"
                    f"Versuche: {r['retries']}<br>"
                    f"Letzter Lauf: {r['last_run_at'] or '–'}"
                )
                id_row.append(r["id"])
            else:
                z_row.append(NO_DATA_CODE)
                text_row.append("keine Daten")
                id_row.append(None)
        z.append(z_row)
        text.append(text_row)
        ids.append(id_row)

    height = max(400, 25 * len(bands) + 120)
    fig = go.Figure(go.Heatmap(
        z=z, x=years, y=band_labels, text=text,
        hovertemplate="%{text}<extra></extra>",
        colorscale=COLORSCALE, zmin=NO_DATA_CODE, zmax=4,
        showscale=False, customdata=ids, xgap=1, ygap=1,
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=100, r=20, t=60, b=20),
        plot_bgcolor="#FAFAFA", paper_bgcolor="#FAFAFA",
        xaxis=dict(tickmode="linear", dtick=1, title="Jahr", side="top", autorange="reversed"),
        yaxis=dict(title="ELO-Band", autorange=True),
        title=dict(text=f"{federation} — Scraping Grid", x=0.5),
    )
    return fig


# ---------------------------------------------------------------------------
# Overview figure (Übersicht-Tab)
# ---------------------------------------------------------------------------
# z = 0..100, diskret in 10%-Schritten. 0% = grau, 100% = tiefgrün.
_OV_ZMIN, _OV_ZMAX = 0, 100

_OV_STEPS = [
    (0,   "#BDBDBD"),  # 0%   grau
    (10,  "#DCEDC8"),  # 10%  sehr hellgrün
    (20,  "#DCEDC8"),  # 20%  = 10%
    (30,  "#81C784"),  # 30%  hellgrün
    (40,  "#81C784"),  # 40%  = 30%
    (50,  "#43A047"),  # 50%  mittelgrün
    (60,  "#43A047"),  # 60%  = 50%
    (70,  "#2E7D32"),  # 70%  dunkelgrün
    (80,  "#2E7D32"),  # 80%  = 70%
    (90,  "#1B5E20"),  # 90%  sehr dunkel
    (100, "#0D4A18"),  # 100% tiefgrün (dunkler als 90%, aber nicht schwarz)
]

# Diskrete Farbstufen: jede 10%-Stufe hat eine eigene Farbe
OVERVIEW_COLORSCALE = []
for _pct, _col in _OV_STEPS:
    _lo = _pct / 100
    _hi = (_pct + 9.99) / 100 if _pct < 100 else 1.0
    OVERVIEW_COLORSCALE += [[_lo, _col], [_hi, _col]]

_OV_LEGEND = [
    ("#BDBDBD", "<10%"),
    ("#DCEDC8", "<30%"),
    ("#81C784", "<50%"),
    ("#43A047", "<70%"),
    ("#2E7D32", "<90%"),
    ("#0D4A18", "100%"),
]


def build_overview_figure() -> go.Figure:
    rows = query_overview()
    if not rows:
        return go.Figure().update_layout(title="Keine Daten")

    all_buckets = sorted(set(r["elo_bucket"] for r in rows), reverse=False)
    max_bucket = max(all_buckets)

    def bucket_label(b):
        return f"≥{b}" if b == max_bucket else f"{b}–{b + 49}"

    bucket_labels = [bucket_label(b) for b in all_buckets]

    # Lookup: (federation, elo_bucket) → row
    lookup = {(r["federation"], r["elo_bucket"]): r for r in rows}

    z, text = [], []
    for bkt in all_buckets:
        z_row, text_row = [], []
        for fed in OVERVIEW_FEDERATIONS:
            r = lookup.get((fed, bkt))
            if r:
                pct = round(r["done_count"] / r["total"] * 100 / 10) * 10
                z_row.append(pct)
                text_row.append(f"{pct}%<br>{r['done_count']}/{r['total']} Gruppen done")
            else:
                z_row.append(0)
                text_row.append("0% · noch nicht eingeplant")
        z.append(z_row)
        text.append(text_row)

    height = max(400, 25 * len(all_buckets) + 120)
    fig = go.Figure(go.Heatmap(
        z=z,
        x=OVERVIEW_FEDERATIONS,
        y=bucket_labels,
        text=text,
        hovertemplate="%{text}<extra></extra>",
        colorscale=OVERVIEW_COLORSCALE,
        zmin=_OV_ZMIN,
        zmax=_OV_ZMAX,
        showscale=False,
        xgap=2,
        ygap=1,
    ))
    fig.update_layout(
        height=height,
        margin=dict(l=90, r=20, t=60, b=20),
        plot_bgcolor="#FAFAFA",
        paper_bgcolor="#FAFAFA",
        xaxis=dict(title="", side="top", tickfont=dict(size=13, family="monospace")),
        yaxis=dict(title="ELO-Band", autorange=True, tickfont=dict(size=11)),
        title=dict(text="Scraping-Fortschritt nach Land & ELO-Band", x=0.5),
    )
    return fig


# ---------------------------------------------------------------------------
# Layout helpers
# ---------------------------------------------------------------------------
def metric_card(label: str, value_id: str, color: str) -> dbc.Card:
    return dbc.Card(
        dbc.CardBody([
            html.H2(id=value_id, className="card-title mb-0", style={"color": color}),
            html.Small(label, className="text-muted"),
        ]),
        className="text-center shadow-sm",
    )


def legend_item(color: str, label: str) -> html.Span:
    return html.Span([
        html.Span(style={
            "display": "inline-block", "width": "14px", "height": "14px",
            "backgroundColor": color, "borderRadius": "2px",
            "marginRight": "4px", "verticalAlign": "middle",
        }),
        html.Span(label, style={"marginRight": "12px", "fontSize": "0.85rem"}),
    ])


def status_badge(status: str) -> str:
    icons = {"pending": "⏳", "running": "▶️", "failed": "❌", "done": "✅", "skipped": "⏭️"}
    return f"{icons.get(status, '')} {status}"


# ---------------------------------------------------------------------------
# App
# ---------------------------------------------------------------------------
app = dash.Dash(
    __name__,
    external_stylesheets=[dbc.themes.BOOTSTRAP],
    title="FIDE Scraping Orchestrator",
    suppress_callback_exceptions=True,
)

continents = query_continents()
default_continent = "Europe"
default_federation = query_federations(default_continent)[0] if continents else "GER"

# ---------------------------------------------------------------------------
# Tab 0 — Übersicht layout
# ---------------------------------------------------------------------------
def _ov_legend_item(color: str, label: str) -> html.Span:
    return html.Span([
        html.Span(style={
            "display": "inline-block", "width": "18px", "height": "14px",
            "backgroundColor": color, "borderRadius": "2px",
            "marginRight": "4px", "verticalAlign": "middle",
        }),
        html.Span(label, style={"marginRight": "14px", "fontSize": "0.82rem"}),
    ])

tab_overview = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-overview", interval=30_000, n_intervals=0),
    html.Div(
        [_ov_legend_item(c, l) for c, l in _OV_LEGEND],
        className="mb-2 mt-3",
    ),
    dcc.Graph(id="overview-grid", config={"displayModeBar": False}),
], className="py-2")


# ---------------------------------------------------------------------------
# Tab 1 — Heatmap layout
# ---------------------------------------------------------------------------
tab_heatmap = dbc.Container(fluid=True, children=[

    # Auto-refresh
    dcc.Interval(id="interval", interval=10_000, n_intervals=0),
    dcc.Store(id="selected-group-id"),

    # Metric cards
    dbc.Row([
        dbc.Col(metric_card("Total",   "stat-total",   "#333333"), width=2),
        dbc.Col(metric_card("Done",    "stat-done",    STATUS_COLOR["done"]),    width=2),
        dbc.Col(metric_card("Pending", "stat-pending", STATUS_COLOR["pending"]), width=2),
        dbc.Col(metric_card("Running", "stat-running", STATUS_COLOR["running"]), width=2),
        dbc.Col(metric_card("Failed",  "stat-failed",  STATUS_COLOR["failed"]),  width=2),
        dbc.Col(metric_card("Skipped", "stat-skipped", STATUS_COLOR["skipped"]), width=2),
    ], className="mb-3 g-2"),

    # Controls
    dbc.Row([
        dbc.Col([
            dbc.Label("Kontinent", className="small text-muted mb-1"),
            dcc.Dropdown(
                id="dd-continent",
                options=[{"label": c, "value": c} for c in continents],
                value=default_continent, clearable=False,
            ),
        ], width=2),
        dbc.Col([
            dbc.Label("Föderation", className="small text-muted mb-1"),
            dcc.Dropdown(id="dd-federation", clearable=False),
        ], width=2),
        dbc.Col([
            dbc.Label("Scrape-Profil", className="small text-muted mb-1"),
            dcc.Dropdown(
                id="dd-profile",
                options=[{"label": p, "value": p} for p in pm.available()],
                value=pm.get_active()["name"], clearable=False,
            ),
        ], width=2),
        dbc.Col([
            dbc.Label("Max Gruppen", className="small text-muted mb-1"),
            dbc.Input(
                id="input-max-groups", type="number", min=1, step=1,
                placeholder="∞", size="sm",
                style={"width": "90px"},
            ),
        ], width=1),
        dbc.Col([
            dbc.Label("Max Stunden", className="small text-muted mb-1"),
            dbc.Input(
                id="input-max-hours", type="number", min=0.5, step=0.5,
                placeholder="∞", size="sm",
                style={"width": "90px"},
            ),
        ], width=1),
        dbc.Col([
            dbc.Label("Worker", className="small text-muted mb-1"),
            html.Div([
                dbc.Button("▶ Start",  id="btn-start",  color="success", size="sm", className="me-1"),
                dbc.Button("⏸ Pause",  id="btn-pause",  color="warning", size="sm", className="me-1"),
                dbc.Button("⏹ Stop",   id="btn-stop",   color="danger",  size="sm"),
            ]),
        ], width=2),
        dbc.Col([
            dbc.Label("Status", className="small text-muted mb-1"),
            html.Div(id="worker-status", className="small text-muted mt-1"),
        ], width=2),
    ], className="mb-3 align-items-end g-2"),

    # Legend
    html.Div([
        legend_item(STATUS_COLOR["done"],    "Done"),
        legend_item(STATUS_COLOR["running"], "Running"),
        legend_item(STATUS_COLOR["failed"],  "Failed"),
    ], className="mb-2"),

    # Heatmap
    dcc.Graph(id="grid", config={"displayModeBar": False}),

    # Detail Modal
    dbc.Modal([
        dbc.ModalHeader(dbc.ModalTitle(id="modal-title")),
        dbc.ModalBody(id="modal-body"),
        dbc.ModalFooter(id="modal-footer"),
    ], id="detail-modal", is_open=False, size="lg"),

    # Dummy outputs
    html.Div(id="worker-cmd-out",  style={"display": "none"}),
    html.Div(id="modal-apply-out", style={"display": "none"}),
])

# ---------------------------------------------------------------------------
# Tab 2 — Queue layout
# ---------------------------------------------------------------------------
QUEUE_COLUMNS = [
    {"name": "Priorität",   "id": "priority",    "editable": True,  "type": "numeric"},
    {"name": "Gerät",       "id": "device",      "editable": True,  "presentation": "dropdown"},
    {"name": "Profil",      "id": "profile",     "editable": False},
    {"name": "Föd.",        "id": "federation",  "editable": False},
    {"name": "Kontinent",   "id": "continent",   "editable": False},
    {"name": "Jahr",        "id": "year",        "editable": False, "type": "numeric"},
    {"name": "ELO-Band",    "id": "elo_band",    "editable": False},
    {"name": "Spieler",     "id": "player_count","editable": False, "type": "numeric"},
    {"name": "Status",      "id": "status",      "editable": False},
    {"name": "Versuche",    "id": "retries",     "editable": False, "type": "numeric"},
    {"name": "Letzter Lauf","id": "last_run_at", "editable": False},
]

DEVICE_OPTIONS = [
    {"label": "— (beliebig)",  "value": ""},
    {"label": "mac_mini",      "value": "mac_mini"},
    {"label": "raspi",         "value": "raspi"},
    {"label": "vps",           "value": "vps"},
]

PROFILE_OPTIONS = [
    {"label": "— (fuzzy)",     "value": ""},
    {"label": "conservative",  "value": "conservative"},
    {"label": "normal",        "value": "normal"},
    {"label": "aggressive",    "value": "aggressive"},
]

tab_queue = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-queue", interval=15_000, n_intervals=0),
    dcc.Store(id="queue-selected-id"),

    dbc.Row([
        dbc.Col(html.H5("Scraping-Queue", className="text-secondary fw-bold my-3"), width="auto"),
        dbc.Col(
            dbc.Badge(id="queue-count", color="primary", className="ms-2 align-self-center"),
            width="auto",
        ),
    ], align="center", className="mb-1"),

    # ── Profil-Aktionsleiste (oben, immer sichtbar) ───────────────────────
    dbc.Card(
        dbc.CardBody([
            dbc.Row([
                dbc.Col(html.Div(id="queue-sel-label",
                                 className="small text-muted",
                                 style={"paddingTop": "6px"}),
                        width=True),
                dbc.Col([
                    dcc.Dropdown(
                        id="queue-profile-dd",
                        options=PROFILE_OPTIONS,
                        placeholder="Profil wählen…",
                        clearable=True,
                        style={"fontSize": "13px", "minWidth": "180px"},
                    ),
                ], width="auto"),
                dbc.Col(
                    dbc.Button("Profil setzen", id="queue-profile-btn",
                               color="primary", size="sm", disabled=True),
                    width="auto",
                ),
                dbc.Col(
                    html.Div(id="queue-profile-out",
                             className="small text-success",
                             style={"paddingTop": "6px"}),
                    width="auto",
                ),
            ], align="center", className="g-2"),
        ], className="py-2 px-3"),
        className="mb-2",
        style={"border": "1px solid #dee2e6", "borderRadius": "4px",
               "backgroundColor": "#F8F9FA"},
    ),

    dash_table.DataTable(
        id="queue-table",
        columns=QUEUE_COLUMNS,
        data=[],
        editable=True,
        row_selectable="single",
        dropdown={
            "device": {"options": DEVICE_OPTIONS, "clearable": True},
        },
        page_size=50,
        page_action="native",
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#F0F0F0", "fontWeight": "bold", "fontSize": "0.85rem"},
        style_cell={"fontSize": "0.85rem", "padding": "6px 10px", "textAlign": "left"},
        style_data_conditional=[
            {"if": {"filter_query": '{status} = "pending"',  "column_id": "status"},
             "color": STATUS_COLOR["pending"], "fontWeight": "bold"},
            {"if": {"filter_query": '{status} = "running"',  "column_id": "status"},
             "color": STATUS_COLOR["running"], "fontWeight": "bold"},
            {"if": {"filter_query": '{status} = "failed"',   "column_id": "status"},
             "color": STATUS_COLOR["failed"],  "fontWeight": "bold"},
            {"if": {"column_id": "priority"}, "backgroundColor": "#FFFDE7"},
            {"if": {"column_id": "device"},   "backgroundColor": "#E8F5E9"},
            {"if": {"column_id": "profile"},  "backgroundColor": "#EDE7F6"},
        ],
        tooltip_header={
            "priority": "Klicken zum Bearbeiten — niedrigerer Wert = früher gescrapt",
            "device":   "Gerät zuweisen — leer = beliebiges Gerät",
            "profile":  f"Aktuelles Profil — leer = {FUZZY_LABEL}",
        },
    ),

    html.Div(id="queue-save-out", style={"display": "none"}),
], className="py-3")

# ---------------------------------------------------------------------------
# Tab 3 — Completed layout
# ---------------------------------------------------------------------------
COMPLETED_COLUMNS = [
    {"name": "Föd.",           "id": "federation"},
    {"name": "Kontinent",      "id": "continent"},
    {"name": "Jahr",           "id": "year"},
    {"name": "ELO-Band",       "id": "elo_band"},
    {"name": "Spieler",        "id": "player_count"},
    {"name": "Partien",        "id": "records_found"},
    {"name": "Partien/Spieler","id": "partien_per_spieler"},
    {"name": "MB",             "id": "mb"},
    {"name": "Dauer (h)",      "id": "duration_h"},
    {"name": "Rate/h",         "id": "rate_per_h"},
    {"name": "Abgeschlossen",  "id": "last_run_at"},
]

tab_completed = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-completed", interval=30_000, n_intervals=0),
    dbc.Row([
        dbc.Col(html.H5("Abgeschlossene Gruppen", className="text-secondary fw-bold my-3"), width="auto"),
        dbc.Col(
            dbc.Badge(id="completed-count", color="success", className="ms-2 align-self-center"),
            width="auto",
        ),
    ], align="center"),
    dash_table.DataTable(
        id="completed-table",
        columns=COMPLETED_COLUMNS,
        data=[],
        page_size=50,
        page_action="native",
        sort_action="native",
        filter_action="native",
        style_table={"overflowX": "auto"},
        style_header={"backgroundColor": "#F0F0F0", "fontWeight": "bold", "fontSize": "0.85rem"},
        style_cell={"fontSize": "0.85rem", "padding": "6px 10px", "textAlign": "left"},
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#FAFAFA"},
        ],
    ),
], className="py-3")

# ---------------------------------------------------------------------------
# Main layout
# ---------------------------------------------------------------------------
app.layout = dbc.Container(fluid=True, children=[
    dbc.Row(dbc.Col(html.H4(
        "FIDE Scraping Orchestrator",
        className="my-3 text-secondary fw-bold",
    ))),
    dbc.Tabs([
        dbc.Tab(tab_overview, label="🌍 Übersicht",   tab_id="tab-overview"),
        dbc.Tab(tab_heatmap,  label="🗺️ Heatmap",    tab_id="tab-heatmap"),
        dbc.Tab(tab_queue,    label="📋 Queue",       tab_id="tab-queue"),
        dbc.Tab(tab_completed,label="✅ Abgeschlossen", tab_id="tab-completed"),
    ], id="main-tabs", active_tab="tab-overview"),
], style={"backgroundColor": "#F8F9FA", "minHeight": "100vh", "paddingBottom": "40px"})


# ===========================================================================
# Callbacks — Tab 1 (Heatmap)
# ===========================================================================

@app.callback(
    Output("dd-federation", "options"),
    Output("dd-federation", "value"),
    Input("dd-continent", "value"),
)
def update_federation_dropdown(continent):
    feds = query_federations(continent)
    opts = [{"label": f, "value": f} for f in feds]
    # Default: GER wenn verfügbar, sonst erster Eintrag
    default = "GER" if "GER" in feds else (feds[0] if feds else None)
    return opts, default


@app.callback(
    Output("grid", "figure"),
    Output("stat-total",   "children"),
    Output("stat-done",    "children"),
    Output("stat-pending", "children"),
    Output("stat-running", "children"),
    Output("stat-failed",  "children"),
    Output("stat-skipped", "children"),
    Output("worker-status", "children"),
    Input("interval", "n_intervals"),
    Input("dd-federation", "value"),
)
def refresh_heatmap(_, federation):
    if not federation:
        return dash.no_update, *["–"] * 6, "–"
    fig = build_figure(federation)
    s = query_global_stats()
    ws = read_worker_state()
    cmd = ws.get("command", "stopped")
    done = ws.get("groups_done", 0)
    max_g = ws.get("max_groups")
    max_h = ws.get("max_hours")
    limits = []
    if max_g: limits.append(f"{done}/{max_g} Gruppen")
    if max_h: limits.append(f"max {max_h}h")
    limit_str = f" · {', '.join(limits)}" if limits else ""

    status_parts = [f"Worker: {cmd}{limit_str}"]
    current_group = ws.get("current_group")
    if current_group:
        import time as _time
        profile_name = ws.get("current_profile", "?")
        c_done = ws.get("combos_done", 0)
        c_total = ws.get("combos_total")
        n_players = ws.get("player_count")
        started_at = ws.get("group_started_at")
        mb = ws.get("mb_downloaded", 0.0)

        combo_str = f"{c_done}/{c_total}" if c_total else str(c_done)
        player_str = f"{n_players} Spieler · " if n_players else ""

        speed_str = ""
        eta_str = ""
        if started_at and c_done:
            elapsed = _time.time() - started_at
            if elapsed > 0:
                cph = c_done / elapsed * 3600
                speed_str = f" · {cph:.0f} c/h"
                if c_total and c_total > c_done:
                    eta_sec = (c_total - c_done) / (c_done / elapsed)
                    eta_h = int(eta_sec // 3600)
                    eta_m = int((eta_sec % 3600) // 60)
                    eta_str = f" · ETA {eta_h}h{eta_m:02d}m"

        mb_str = f" · {mb:.1f} MB" if mb else ""
        year = ws.get("current_year", "")
        # Label aufsplitten: "POL/2026/2361–2739" → "POL · ELO 2361–2739"
        parts = current_group.split("/")
        fed = parts[0] if parts else current_group
        elo = parts[2] if len(parts) > 2 else ""
        group_str = f"{fed} · ELO {elo}" if elo else current_group
        year_str = f"Jahr {year} · " if year else ""
        status_parts.append(f"{year_str}{group_str} [{profile_name}]")
        status_parts.append(f"{player_str}{combo_str} combos{speed_str}{eta_str}{mb_str}")

    return (fig,
            f"{s['total']:,}", f"{s['done']:,}", f"{s['pending']:,}",
            f"{s['running']:,}", f"{s['failed']:,}", f"{s['skipped']:,}",
            " | ".join(status_parts))


@app.callback(
    Output("dd-profile", "value"),
    Input("dd-profile", "value"),
    prevent_initial_call=True,
)
def switch_profile(name):
    if name:
        pm.set_active(name)
    return name


@app.callback(
    Output("worker-cmd-out", "children"),
    Input("btn-start", "n_clicks"),
    Input("btn-pause", "n_clicks"),
    Input("btn-stop",  "n_clicks"),
    State("input-max-groups", "value"),
    State("input-max-hours",  "value"),
    prevent_initial_call=True,
)
def handle_worker_buttons(start, pause, stop, max_groups, max_hours):
    triggered = callback_context.triggered_id
    if triggered == "btn-start":
        state = read_worker_state()
        state["command"]    = "run"
        state["max_groups"] = int(max_groups) if max_groups else None
        state["max_hours"]  = float(max_hours) if max_hours else None
        state["started_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        state["groups_done"] = 0
        WORKER_STATE_PATH.write_text(json.dumps(state, indent=2))
    elif triggered == "btn-pause":
        write_worker_state("pause")
    elif triggered == "btn-stop":
        write_worker_state("stopped")
    return ""


@app.callback(
    Output("detail-modal", "is_open"),
    Output("modal-title",  "children"),
    Output("modal-body",   "children"),
    Output("modal-footer", "children"),
    Output("selected-group-id", "data"),
    Input("grid", "clickData"),
    Input("modal-close", "n_clicks"),       # may not exist yet — suppress_callback_exceptions=True
    State("detail-modal", "is_open"),
    State("dd-federation", "value"),
    prevent_initial_call=True,
)
def toggle_modal(click_data, close_clicks, is_open, federation):
    triggered = callback_context.triggered_id
    if triggered == "modal-close":
        return False, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    if not click_data:
        return is_open, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    point = click_data["points"][0]
    group_id = point.get("customdata")
    if group_id is None:
        return is_open, dash.no_update, dash.no_update, dash.no_update, dash.no_update

    g = query_group_by_id(group_id)
    if not g:
        return is_open, "Nicht gefunden", "", _modal_footer_empty(), None

    status = g["status"]
    title = f"{federation} | {g['year']} | ELO {g['elo_min']}–{g['elo_max']}"

    body = dbc.Table([
        html.Tbody([
            html.Tr([html.Td(k, className="text-muted pe-3"), html.Td(str(v))])
            for k, v in {
                "Status":        status_badge(status),
                "Spieler":       g["player_count"],
                "Partien":       g["records_found"] or "–",
                "Versuche":      g["retries"],
                "Letzter Lauf":  g["last_run_at"] or "–",
                "Priorität":     g["priority"],
                "Notizen":       g["notes"] or "–",
            }.items()
        ])
    ], bordered=False, size="sm")

    footer = _modal_footer(status, group_id)
    return True, title, body, footer, group_id


def _modal_footer_empty():
    return dbc.Button("Schliessen", id="modal-close", color="secondary", size="sm")


def _modal_footer(status: str, group_id: int):
    close_btn = dbc.Button("Schliessen", id="modal-close", color="secondary", size="sm", className="ms-2")

    if status == "running":
        return html.Div([
            dbc.Alert("⏳ Wird gerade gescrapt — kein Status-Override möglich.", color="warning",
                      className="mb-2 py-1 px-2 small"),
            close_btn,
        ])

    if status == "done":
        return html.Div([
            dbc.Alert("✅ Bereits abgeschlossen. Zurücksetzen löscht den done-Status "
                      "und stellt die Gruppe in die Queue.", color="success",
                      className="mb-2 py-1 px-2 small"),
            dbc.Button("↩ Zurück auf pending", id="modal-apply", color="warning",
                       size="sm", className="me-2"),
            dcc.Store(id="modal-override-status", data="pending"),
            close_btn,
        ])

    # pending / failed / skipped — volle Kontrolle
    return html.Div([
        dbc.Label("Status setzen:", className="me-2 small"),
        dcc.Dropdown(
            id="modal-status-dd",
            options=[{"label": s, "value": s}
                     for s in ("pending", "done", "failed", "skipped")],
            placeholder="Override…",
            style={"width": "160px", "display": "inline-block"},
            className="me-2",
        ),
        dbc.Button("Anwenden", id="modal-apply", color="primary", size="sm"),
        dcc.Store(id="modal-override-status", data=None),
        close_btn,
    ], className="d-flex align-items-center flex-wrap gap-1")


@app.callback(
    Output("modal-apply-out", "children"),
    Input("modal-apply", "n_clicks"),
    State("selected-group-id", "data"),
    State("modal-override-status", "data"),   # for done-cells: pre-filled with "pending"
    State("modal-status-dd",      "value"),   # for editable cells: user choice
    prevent_initial_call=True,
)
def apply_status_override(n, group_id, override_status, dd_status):
    if not group_id:
        return ""
    new_status = override_status or dd_status
    if new_status:
        update_group_status(group_id, new_status)
    return ""


# ===========================================================================
# Callbacks — Tab 2 (Queue)
# ===========================================================================

@app.callback(
    Output("queue-table", "data"),
    Output("queue-count", "children"),
    Input("interval-queue", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_queue(_, active_tab):
    if active_tab != "tab-queue":
        return dash.no_update, dash.no_update
    rows = query_queue()
    return rows, f"{len(rows):,} Gruppen"


@app.callback(
    Output("queue-save-out", "children"),
    Input("queue-table", "data"),
    State("queue-table", "data_previous"),
    prevent_initial_call=True,
)
def save_queue_edits(current_data, previous_data):
    """Persist priority and device changes made directly in the table."""
    if not current_data or not previous_data:
        return ""
    for curr, prev in zip(current_data, previous_data):
        if curr.get("priority") != prev.get("priority"):
            try:
                update_group_priority(curr["id"], int(curr["priority"]))
            except (ValueError, TypeError, KeyError):
                pass
        if curr.get("device") != prev.get("device"):
            try:
                update_group_device(curr["id"], curr.get("device", "") or "")
            except (ValueError, TypeError, KeyError):
                pass
        if curr.get("profile") != prev.get("profile"):
            try:
                update_group_profile_db(curr["id"], curr.get("profile", "") or "")
            except (ValueError, TypeError, KeyError):
                pass
    return ""


# ===========================================================================
# Callbacks — Queue Selektion & Profil-Aktionsleiste
# ===========================================================================

@app.callback(
    Output("queue-selected-id", "data"),
    Output("queue-sel-label",   "children"),
    Output("queue-profile-btn", "disabled"),
    Input("queue-table", "selected_rows"),
    State("queue-table", "data"),
    prevent_initial_call=True,
)
def on_queue_row_select(selected_rows, data):
    if not selected_rows or not data:
        return None, "Keine Zeile ausgewählt — Zeile anklicken um Profil zu setzen.", True
    row = data[selected_rows[0]]
    gid = row.get("id")
    label = (f"Ausgewählt: {row.get('federation')} {row.get('year')} "
             f"ELO {row.get('elo_band')}  |  Aktuelles Profil: "
             f"{row.get('profile') or '— (fuzzy)'}")
    return gid, label, False


@app.callback(
    Output("queue-profile-out", "children"),
    Output("queue-table",       "data",            allow_duplicate=True),
    Output("queue-count",       "children",        allow_duplicate=True),
    Input("queue-profile-btn",  "n_clicks"),
    State("queue-selected-id",  "data"),
    State("queue-profile-dd",   "value"),
    prevent_initial_call=True,
)
def apply_queue_profile(n_clicks, group_id, profile_val):
    if not group_id:
        return "Keine Gruppe ausgewählt.", dash.no_update, dash.no_update
    try:
        update_group_profile_db(int(group_id), profile_val or "")
        label = profile_val if profile_val else "fuzzy"
        rows  = query_queue()
        return f"✓ Profil '{label}' gesetzt.", rows, f"{len(rows):,} Gruppen"
    except Exception as exc:
        return f"Fehler: {exc}", dash.no_update, dash.no_update


# ===========================================================================
# Callbacks — Tab 3 (Completed)
# ===========================================================================

@app.callback(
    Output("completed-table", "data"),
    Output("completed-count", "children"),
    Input("interval-completed", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_completed(_, active_tab):
    if active_tab != "tab-completed":
        return dash.no_update, dash.no_update
    rows = query_completed()
    return rows, f"{len(rows):,} Gruppen"


# ===========================================================================
# Callbacks — Tab 0 (Übersicht)
# ===========================================================================

@app.callback(
    Output("overview-grid", "figure"),
    Input("interval-overview", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_overview(_, active_tab):
    if active_tab not in ("tab-overview", None):
        return dash.no_update
    return build_overview_figure()


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug, host="0.0.0.0", port=8050)
