"""Scraping Orchestrator — Web Dashboard (3 Tabs).

Tab 1 – Heatmap:   Föderations-Grid, Worker-Steuerung
Tab 2 – Queue:     Prioritätsliste (pending/running/failed), Priorität editierbar
Tab 3 – Completed: Abgeschlossene Gruppen mit Scraping-Statistiken

Run:  python orchestrator/app.py
Then open http://localhost:8050
"""

import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import dash
import dash_bootstrap_components as dbc
import plotly.graph_objects as go
from dash import Input, Output, State, callback_context, dash_table, dcc, html

from orchestrator import runtime_settings, state_io, store
from orchestrator.fide_iso import fide_to_iso3, SOUTH_AMERICA_FEDS
from orchestrator.profile_manager import ProfileManager, PROFILES_PATH
from orchestrator.state_io import read_worker_state
from orchestrator.store import (
    query_continents,
    query_federations,
    query_global_stats,
    query_grid,
    query_group_by_id,
    update_group_device,
    update_group_priority,
    update_group_status,
    update_group_thread_affinity,
)

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

# Föderationen die direkt als eigene Spalte erscheinen (kein DC-Aggregat);
# Reihenfolge = Spaltenreihenfolge links in der Übersichts-Heatmap.
_OV_DIRECT_FEDS = ("GER", "SUI", "AUT")

pm = ProfileManager()


def _get_concurrency_cfg() -> dict:
    """[concurrency]-Sicht: profiles.yaml-Topologie + Runtime-Overrides (live)."""
    return runtime_settings.effective_concurrency()


def _dc_thread_maps() -> tuple[dict[int, str], dict[str, str]]:
    """Derive {slot: label} and {thread_affinity_id: label} from profiles.yaml,
    live — so new DC threads (e.g. dc_update_1/2/3) show up everywhere without
    editing app.py. Falls back to an empty mapping if profiles.yaml is
    unreadable; call sites should tolerate missing keys gracefully."""
    threads = _get_concurrency_cfg().get("datacenter_threads", [])
    slot_labels = {t["slot"]: t["label"] for t in threads if "slot" in t and "label" in t}
    id_labels = {t["id"]: t["label"] for t in threads if "id" in t and "label" in t}
    return slot_labels, id_labels


def _overview_columns() -> list[str]:
    """Spalten der Übersichts-Heatmap: Direkt-Föderationen + Separator + DC-Threads.

    Live aus der Thread-Config abgeleitet (nach Slot sortiert) statt hartkodiert
    — neue DC-Threads erscheinen automatisch (Review #7; die frühere feste Liste
    musste bei jedem neuen Thread manuell nachgezogen werden). Threads ohne
    eigene Aggregat-Föderationen bleiben draußen, ihre Spalte wäre
    konstruktionsbedingt leer: dc_dach deckt nur die Direktspalten GER/SUI/AUT
    ab, dc_update_* haben federations=[] (P1/P2/P3 statt Föderationen).
    """
    threads = _get_concurrency_cfg().get("datacenter_threads", [])
    dc_labels = [
        t["label"]
        for t in sorted(threads, key=lambda t: t.get("slot", 99))
        if t.get("label") and set(t.get("federations", [])) - set(_OV_DIRECT_FEDS)
    ]
    return list(_OV_DIRECT_FEDS) + ["·"] + dc_labels


def _overview_elo_bounds() -> tuple[int, int]:
    """(floor, ceiling) der Übersichts-Heatmap aus profiles.yaml [dashboard]."""
    cfg = pm.dashboard_settings()
    return int(cfg.get("overview_elo_floor", 1400)), int(cfg.get("overview_elo_ceiling", 2300))


def _save_dc_thread_enabled(dc_id: str, enabled: bool) -> None:
    """Persist enabled-Flag für einen DC-Thread (runtime_settings.json)."""
    try:
        runtime_settings.update_dc_thread(dc_id, enabled=bool(enabled))
    except Exception:
        pass


def _save_dc_thread_active_hours(dc_id: str, h_start, h_end) -> None:
    """Persist active_hours für einen DC-Thread (runtime_settings.json)."""
    try:
        runtime_settings.update_dc_thread(dc_id, active_hours=[int(h_start), int(h_end)])
    except Exception:
        pass


def _save_slot_max_hours(slot: int, hours) -> None:
    """Persist max_hours für einen Residential-Slot (None = unbegrenzt)."""
    try:
        runtime_settings.update_worker_slot(slot, max_hours=float(hours) if hours else None)
    except Exception:
        pass


def _save_dc_thread_max_hours(dc_id: str, hours) -> None:
    """Persist max_hours für einen DC-Thread (None = unbegrenzt)."""
    try:
        runtime_settings.update_dc_thread(dc_id, max_hours=float(hours) if hours else None)
    except Exception:
        pass


def _get_dc_thread_status() -> list[dict]:
    """Gibt für jeden DC-Thread: label, id, enabled, local_time, is_active, has_credentials."""
    from zoneinfo import ZoneInfo
    from datetime import datetime as _dt
    threads = _get_concurrency_cfg().get("datacenter_threads", [])
    result = []
    for t in threads:
        tz_name = t.get("timezone", "UTC")
        try:
            tz = ZoneInfo(tz_name)
            now = _dt.now(tz)
            local_time = now.strftime("%H:%M")
            h_start, h_end = t.get("active_hours", [7, 23])
            is_active_hours = h_start <= now.hour < h_end
        except Exception:
            local_time = "?"
            is_active_hours = True
        has_creds = bool(os.getenv(t.get("username_env", ""), ""))
        result.append({
            "id":         t.get("id", ""),
            "label":      t.get("label", t.get("id", "")),
            "enabled":    t.get("enabled", False),
            "local_time": local_time,
            "timezone":   tz_name,
            "is_active_hours": is_active_hours,
            "has_credentials": has_creds,
            "federations": t.get("federations", []),
            "slot":        t.get("slot", 99),
            "max_hours":   t.get("max_hours"),
            "active_hours": t.get("active_hours", [7, 23]),
        })
    return result


def _save_worker_profile_for_slot(slot: int, profile_name: str) -> None:
    """Persist worker_slots[slot].profile (runtime_settings.json)."""
    try:
        runtime_settings.update_worker_slot(slot, profile=profile_name)
    except Exception:
        pass


def _save_residential_slot_enabled(slot: int, enabled: bool) -> None:
    """Persist worker_slots[slot].enabled (runtime_settings.json)."""
    try:
        runtime_settings.update_worker_slot(slot, enabled=bool(enabled))
    except Exception:
        pass


def _get_residential_thread_status() -> list[dict]:
    """Gibt für jeden Residential-Slot (T0–T3): label, profil, enabled, laufende Gruppe."""
    cfg        = _get_concurrency_cfg()
    ws_threads = {t.get("slot"): t for t in read_worker_state().get("threads", [])}
    _SLOT_BADGE = ["primary", "success", "warning", "info"]

    # worker_slots (neu) oder Fallback auf max_workers
    slots_cfg = cfg.get("worker_slots")
    if not slots_cfg:
        max_w    = cfg.get("max_workers", 1)
        profiles = cfg.get("worker_profiles", ["normal"] * 4)
        slots_cfg = [
            {"slot": i, "enabled": i < max_w,
             "profile": profiles[i] if i < len(profiles) else "normal"}
            for i in range(4)
        ]

    result = []
    for s in sorted(slots_cfg, key=lambda x: x.get("slot", 0)):
        slot    = s.get("slot", 0)
        ws      = ws_threads.get(slot, {})
        result.append({
            "slot":          slot,
            "label":         f"T{slot + 1}",
            "profile":       s.get("profile", "normal"),
            "enabled":       s.get("enabled", False),
            "badge_color":   _SLOT_BADGE[slot % len(_SLOT_BADGE)],
            "current_group": ws.get("current_group", ""),
            "combos_done":   ws.get("combos_done", 0),
            "combos_total":  ws.get("combos_total"),
            "player_count":  ws.get("player_count"),
            "started_at":    ws.get("group_started_at"),
            "max_hours":     s.get("max_hours"),
        })
    return result


# Fuzzy-Label aus aktuellen Gewichten bauen (wird bei App-Start einmalig gelesen)
def _fuzzy_label() -> str:
    weights_cfg = pm._data.get("fuzzy_weights", {})
    parts = [f"{p[0].upper()}{weights_cfg.get(p, 0)}%" for p in ["conservative", "normal", "aggressive"]]
    return f"Fuzzy ({' / '.join(parts)})"

FUZZY_LABEL = _fuzzy_label()


# ---------------------------------------------------------------------------
# DB-Zugriff: seit Review #6 in orchestrator/store.py — hier nur noch dünne
# Wrapper, die Config-abhängige Parameter (Thread-Maps, ELO-Grenzen,
# Live-Worker-State) injizieren. Direkt durchgereichte Funktionen: siehe Import.
# ---------------------------------------------------------------------------

def _get_dc_overview_map() -> dict[str, list[str]]:
    """DC-Label → Föderationsliste (live aus profiles.yaml)."""
    threads = _get_concurrency_cfg().get("datacenter_threads", [])
    return {t["label"]: t.get("federations", []) for t in threads}


def query_overview() -> list[dict]:
    """Scraping-Fortschritt pro (Spalte, ELO-Bucket) — Parameter-Bau für store.

    Direktspalten (GER/SUI/AUT) mappen auf sich selbst, DC-aggregierte
    Föderationen auf ihr DC-Label; Direktspalten gewinnen bei Überschneidung
    (dc_dach führt GER/SUI/AUT, die trotzdem eigene Spalten bleiben).
    """
    fed_to_column: dict[str, str] = {f: f for f in _OV_DIRECT_FEDS}
    for label, feds in _get_dc_overview_map().items():
        for fed in feds:
            fed_to_column.setdefault(fed, label)
    elo_floor, elo_ceiling = _overview_elo_bounds()
    return store.query_overview(fed_to_column, elo_floor, elo_ceiling)


# ---------------------------------------------------------------------------
# DB helpers — Tab 2: Queue
# ---------------------------------------------------------------------------
def query_queue(affinity_filter: str | None = None) -> list[dict]:
    """Queue-Sicht — reicht Thread-Maps + Live-Worker-State an store durch."""
    slot_labels, id_labels = _dc_thread_maps()
    return store.query_queue(
        affinity_filter, slot_labels, id_labels,
        worker_threads=read_worker_state().get("threads", []),
    )


# ---------------------------------------------------------------------------
# DB helpers — Tab 3: Completed
# ---------------------------------------------------------------------------
def query_completed() -> list[dict]:
    """Done groups with scraping stats — Slot-Labels aus der Thread-Config."""
    return store.query_completed(_dc_thread_maps()[0])


# ---------------------------------------------------------------------------
# Worker state — kanonische, ATOMARE Implementierung aus state_io (Review #6);
# die frühere app-eigene write_text-Version hatte das Truncation-Race, gegen
# das worker.py seine Startup-Grace brauchte.
# ---------------------------------------------------------------------------
def write_worker_state(command: str) -> None:
    state_io.write_state(command=command)


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
# z = -10..100.
#   z=-10        → keine Daten (nicht in DB)
#   z=0          → 0% pending (DB-Eintrag, 0/N done)
#   z=5          → >0% aber <10% (angefangen)
#   z=10..100    → 10–100%, gerundet auf 10er-Schritte
_OV_ZMIN, _OV_ZMAX = -10, 100


def _ov_pos(z: float) -> float:
    """Normalisiert z aus [_OV_ZMIN, _OV_ZMAX] auf [0.0, 1.0]."""
    return (z - _OV_ZMIN) / (_OV_ZMAX - _OV_ZMIN)


# Diskrete Farbstufen — jedes Band explizit als [lo, color], [hi, color]
OVERVIEW_COLORSCALE = [
    [_ov_pos(-10),   "#F0F0F0"],  # keine Daten (sehr hell)
    [_ov_pos(-0.01), "#F0F0F0"],
    [_ov_pos(0),     "#BDBDBD"],  # 0% pending (grau)
    [_ov_pos(4.99),  "#BDBDBD"],
    [_ov_pos(5),     "#F1F8E9"],  # >0–<10%  sehr blasses Grün
    [_ov_pos(9.99),  "#F1F8E9"],
    [_ov_pos(10),    "#DCEDC8"],  # 10–29%   sehr hellgrün
    [_ov_pos(29.99), "#DCEDC8"],
    [_ov_pos(30),    "#81C784"],  # 30–49%   hellgrün
    [_ov_pos(49.99), "#81C784"],
    [_ov_pos(50),    "#43A047"],  # 50–69%   mittelgrün
    [_ov_pos(69.99), "#43A047"],
    [_ov_pos(70),    "#2E7D32"],  # 70–89%   dunkelgrün
    [_ov_pos(89.99), "#2E7D32"],
    [_ov_pos(90),    "#1B5E20"],  # 90–99%   sehr dunkel
    [_ov_pos(99.99), "#1B5E20"],
    [_ov_pos(100),   "#0D4A18"],  # 100%     tiefgrün
    [1.0,            "#0D4A18"],
]

_OV_LEGEND = [
    ("#F0F0F0", "keine Daten"),
    ("#BDBDBD", "0%"),
    ("#F1F8E9", "<10%"),
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

    # DC-Thread-Föderationen für Hover-Text (Label → sortierte Feds-Liste)
    dc_map = _get_dc_overview_map()

    overview_cols = _overview_columns()
    z, text = [], []
    for bkt in all_buckets:
        z_row, text_row = [], []
        for fed in overview_cols:
            if fed == "·":
                # Visueller Separator — transparent / kein Tooltip
                z_row.append(float("nan"))
                text_row.append("")
                continue
            r = lookup.get((fed, bkt))
            if r:
                raw_pct = r["done_count"] / r["total"] * 100
                if raw_pct == 0:
                    pct = 0
                elif raw_pct < 10:
                    pct = 5   # <10%-Band (z=5)
                else:
                    pct = round(raw_pct / 10) * 10
                pct_label = f"<10%" if pct == 5 else f"{pct}%"
                if fed in dc_map:
                    feds_str = ", ".join(dc_map[fed])
                    hover = (
                        f"<b>{fed}</b>: {pct_label}<br>"
                        f"{r['done_count']}/{r['total']} Gruppen done<br>"
                        f"<span style='font-size:0.85em;color:#666'>{feds_str}</span>"
                    )
                else:
                    hover = f"{pct_label}<br>{r['done_count']}/{r['total']} Gruppen done"
                z_row.append(pct)
                text_row.append(hover)
            else:
                z_row.append(-10)
                if fed in dc_map:
                    feds_str = ", ".join(dc_map[fed])
                    text_row.append(
                        f"<b>{fed}</b>: keine Daten<br>noch nicht eingeplant<br>"
                        f"<span style='font-size:0.85em;color:#666'>{feds_str}</span>"
                    )
                else:
                    text_row.append("keine Daten · noch nicht eingeplant")
        z.append(z_row)
        text.append(text_row)

    height = max(400, 25 * len(all_buckets) + 120)
    fig = go.Figure(go.Heatmap(
        z=z,
        x=overview_cols,
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
        xaxis=dict(title="", side="top", tickfont=dict(size=11, family="monospace")),
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
# Tab 0b — Karte (Choropleth: Queue-Fortschritt pro Land)
# ---------------------------------------------------------------------------
# Kontinent-Werte wie in scrape_groups.continent; GLOBAL/Other (P1–P3-Update-
# Batches, FID/NON) sind nicht landgebunden und bleiben von der Karte fern.
# "Americas" ist in der Queue EIN Kontinent — die Karte teilt ihn rein
# darstellerisch in Nord (inkl. Mittelamerika/Karibik) und Süd
# (SOUTH_AMERICA_FEDS in fide_iso.py).
_MAP_CONTINENTS = ["Welt", "Europe", "Asia", "Americas-N", "Americas-S",
                   "Africa", "Oceania"]
_MAP_LABELS = {
    "Welt": "Welt", "Europe": "Europa", "Asia": "Asien",
    "Americas-N": "Nordamerika", "Americas-S": "Südamerika",
    "Africa": "Afrika", "Oceania": "Ozeanien",
}
# plotly kennt keine Scopes für Nord/Süd-Amerika-Split mit Mittelamerika/
# Karibik und Ozeanien → manuelle Ranges. Europa ebenfalls manuell statt
# scope="europe": Plotlys/Natural-Earth-eigene Kontinent-Einteilung weicht von
# unserem _EUROPE-Set ab (TUR/ISR/GEO/ARM/AZE gehören bei uns zu Europa,
# fehlen aber in Plotlys Europa-Scope) — Range deckt alle _EUROPE-Föderationen
# geografisch ab (Süden: Israel ~29°N, Osten: Aserbaidschan ~50°E).
_MAP_GEO = {
    "Welt":       dict(projection_type="natural earth"),
    "Europe":     dict(projection_type="natural earth",
                       lonaxis_range=[-25, 50], lataxis_range=[28, 72]),
    "Asia":       dict(scope="asia"),
    "Africa":     dict(scope="africa"),
    "Americas-N": dict(projection_type="natural earth",
                       lonaxis_range=[-170, -50], lataxis_range=[5, 75]),
    "Americas-S": dict(projection_type="natural earth",
                       lonaxis_range=[-85, -30], lataxis_range=[-58, 14]),
    "Oceania":    dict(projection_type="natural earth",
                       lonaxis_range=[110, 185], lataxis_range=[-50, 22]),
}
# Fortschritt 0→100 %: weiß → done-Grün
_MAP_COLORSCALE = [
    [0.0, "#FFFFFF"],
    [1.0, STATUS_COLOR["done"]],
]


def _map_merged_rows(continent: str) -> tuple[dict, list[str]]:
    """query_laender_data → {iso3: Aggregat}, plus nicht kartierbare Codes.

    UK-Föderationen (ENG/SCO/WLS/NIR) teilen sich die GBR-Geometrie und
    werden summiert; customdata behält die Quell-Codes ('ENG+SCO+…').
    """
    rows = [r for r in store.query_laender_data()
            if r["_continent"] not in ("GLOBAL", "Other")]
    if continent == "Americas-N":
        rows = [r for r in rows if r["_continent"] == "Americas"
                and r["_fed"] not in SOUTH_AMERICA_FEDS]
    elif continent == "Americas-S":
        rows = [r for r in rows if r["_fed"] in SOUTH_AMERICA_FEDS]
    elif continent != "Welt":
        rows = [r for r in rows if r["_continent"] == continent]

    merged: dict[str, dict] = {}
    unmapped: list[str] = []
    for r in rows:
        iso = fide_to_iso3(r["_fed"])
        if iso is None:
            unmapped.append(r["_fed"])
            continue
        m = merged.setdefault(iso, {
            "feds": [], "done": 0, "total": 0,
            "scraped": 0, "active": 0, "mb": 0.0, "plan_from": None,
        })
        m["feds"].append(r["_fed"])
        m["done"]    += r["_r_done_g"]
        m["total"]   += r["_r_total_g"]
        m["scraped"] += r["_r_fide_scraped"]
        m["active"]  += r["_r_fide_total"]
        m["mb"]      += r["_r_mb"]
        # Geplantes Backfill-Startjahr (MIN(year) der Queue) — pro Land
        # individuell steuerbar, sobald die Queue entsprechend bestückt ist
        yp0 = r["_r_yp0"]
        if yp0 is not None and (m["plan_from"] is None or yp0 < m["plan_from"]):
            m["plan_from"] = yp0
    return merged, unmapped


def build_map_figure(continent: str) -> go.Figure:
    merged, unmapped = _map_merged_rows(continent)

    isos, z, custom = [], [], []
    for iso, m in sorted(merged.items()):
        pct = round(m["done"] / m["total"] * 100, 1) if m["total"] else 0.0
        isos.append(iso)
        z.append(pct)
        custom.append(["+".join(m["feds"]), m["done"], m["total"],
                       str(m["plan_from"]) if m["plan_from"] else "—"])

    fig = go.Figure(go.Choropleth(
        locations=isos, z=z, locationmode="ISO-3",
        zmin=0, zmax=100,
        colorscale=_MAP_COLORSCALE,
        marker_line_color="white", marker_line_width=0.4,
        colorbar=dict(title="fertig", thickness=14, len=0.6, ticksuffix=" %"),
        customdata=custom,
        hovertemplate="%{customdata[0]} — %{z:.1f} % "
                      "(%{customdata[1]}/%{customdata[2]} Gruppen)"
                      "<br>Backfill geplant bis %{customdata[3]}<extra></extra>",
    ))
    # Konkrete Prozentwerte als Label — erst ab Kontinent-Zoom lesbar
    if continent != "Welt":
        fig.add_trace(go.Scattergeo(
            locations=isos, locationmode="ISO-3", mode="text",
            text=[f"{v:.0f}" for v in z],
            textfont=dict(size=9, color="#1A1A1A"),
            customdata=custom,          # Klick auf Label = Klick aufs Land
            hoverinfo="skip", showlegend=False,
        ))

    fig.update_layout(
        geo=dict(showframe=False, showcoastlines=True, coastlinecolor="#CCCCCC",
                 showland=True, landcolor="#F0F0F0", **_MAP_GEO[continent]),
        margin=dict(l=0, r=0, t=10, b=25),
        height=650,
        paper_bgcolor="rgba(0,0,0,0)",
    )
    if unmapped:
        fig.add_annotation(
            text="Ohne Kartendarstellung: " + ", ".join(sorted(set(unmapped))),
            xref="paper", yref="paper", x=0, y=-0.03, showarrow=False,
            font=dict(size=10, color="#888888"),
        )
    return fig


def build_map_panel(fed_str: str | None):
    """Detail-Panel rechts neben der Karte für die angeklickte Föderation."""
    if not fed_str:
        return dbc.Card(dbc.CardBody(
            html.Div("Land auf der Karte anklicken für Details.",
                     className="text-muted small"),
        ), className="mt-1")

    feds = fed_str.split("+")
    rows = [r for r in store.query_laender_data() if r["_fed"] in feds]
    if not rows:
        return dbc.Card(dbc.CardBody(
            html.Div(f"Keine Queue-Daten für {fed_str}.", className="text-muted small"),
        ), className="mt-1")

    done    = sum(r["_r_done_g"] for r in rows)
    total   = sum(r["_r_total_g"] for r in rows)
    running = sum(r["_r_running_g"] for r in rows)
    scraped = sum(r["_r_fide_scraped"] for r in rows)
    active  = sum(r["_r_fide_total"] for r in rows)
    mb      = round(sum(r["_r_mb"] for r in rows), 1)
    pct     = round(done / total * 100, 1) if total else 0.0

    yp = [y for r in rows for y in (r["_r_yp0"], r["_r_yp1"]) if y is not None]
    yd = [y for r in rows for y in (r["_r_yd0"], r["_r_yd1"]) if y is not None]
    zeitraum_plan = f"{min(yp)} – {max(yp)}" if yp else "—"
    zeitraum_done = f"{min(yd)} – {max(yd)}" if yd else "—"

    def _stat(label, value):
        return html.Div([
            html.Span(label, className="text-muted small"),
            html.Span(value, className="small fw-semibold float-end"),
        ], className="d-flex justify-content-between border-bottom py-1")

    year_rows = []
    for y in store.query_federation_years(feds):
        y_pct = round(y["done"] / y["total"] * 100) if y["total"] else 0
        year_rows.append(dbc.Row([
            dbc.Col(html.Span(str(y["year"]), className="small text-muted"), width=3),
            dbc.Col(dbc.Progress(
                value=y_pct, color="success" if y_pct == 100 else "warning",
                style={"height": "10px"}, className="mt-1",
            ), width=6),
            dbc.Col(html.Span(f"{y['done']}/{y['total']}",
                              className="small text-muted"), width=3),
        ], className="g-1"))

    return dbc.Card(dbc.CardBody([
        html.H5(fed_str, className="mb-0"),
        html.Div(f"{pct} %", className="display-6 fw-bold",
                 style={"color": STATUS_COLOR["done"] if pct >= 100
                        else STATUS_COLOR["running"]}),
        html.Div("der Scraping-Gruppen abgeschlossen",
                 className="text-muted small mb-2"),
        _stat("Gruppen done/total", f"{done} / {total}"),
        _stat("Gruppen running", str(running)),
        _stat("Spieler-Abdeckung",
              f"{scraped} / {active}"
              + (f" ({round(scraped / active * 100, 1)} %)" if active else "")),
        _stat("Zeitraum Plan", zeitraum_plan),
        _stat("Zeitraum done", zeitraum_done),
        _stat("Download", f"{mb} MB"),
        html.Div("Fortschritt pro Jahr", className="text-muted small mt-3 mb-1"),
        html.Div(year_rows),
    ]), className="mt-1")


tab_map = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-map", interval=300_000, n_intervals=0),
    dbc.Row(dbc.Col([
        dbc.Label("Kontinent", className="small text-muted mb-1 me-3"),
        dbc.RadioItems(
            id="map-continent",
            options=[{"label": _MAP_LABELS[c], "value": c} for c in _MAP_CONTINENTS],
            value="Welt", inline=True,
        ),
    ]), className="mt-3 mb-2"),
    dbc.Row([
        dbc.Col(dcc.Graph(id="map-graph", config={"displayModeBar": False}), width=9),
        dbc.Col(html.Div(id="map-detail-panel"), width=3),
    ]),
], className="py-2")


# ---------------------------------------------------------------------------
# Tab 1 — Heatmap layout
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tab 1b — Übersicht Land (Federation×Jahr-Heatmap + Detail-Modal)
# ---------------------------------------------------------------------------
tab_land = dbc.Container(fluid=True, children=[

    dcc.Interval(id="interval-land", interval=10_000, n_intervals=0),
    dcc.Store(id="selected-group-id"),

    # Kontinent / Föderation-Auswahl
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
        ], width=3),
    ], className="mb-3 align-items-end g-2 mt-3"),

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

    html.Div(id="modal-apply-out", style={"display": "none"}),
], className="py-2")

# ---------------------------------------------------------------------------
# Tab 2 — Steuerung (Worker-Controls, Metric-Cards, DC-Threads)
# ---------------------------------------------------------------------------
tab_heatmap = dbc.Container(fluid=True, children=[

    # Auto-refresh
    dcc.Interval(id="interval-control", interval=10_000, n_intervals=0),

    # Metric cards
    dbc.Row([
        dbc.Col(metric_card("Total",   "stat-total",   "#333333"), width=2),
        dbc.Col(metric_card("Done",    "stat-done",    STATUS_COLOR["done"]),    width=2),
        dbc.Col(metric_card("Pending", "stat-pending", STATUS_COLOR["pending"]), width=2),
        dbc.Col(metric_card("Running", "stat-running", STATUS_COLOR["running"]), width=2),
        dbc.Col(metric_card("Failed",  "stat-failed",  STATUS_COLOR["failed"]),  width=2),
        dbc.Col(metric_card("Skipped", "stat-skipped", STATUS_COLOR["skipped"]), width=2),
    ], className="mb-3 g-2 mt-3"),

    # Globale Worker-Controls (kompakt)
    dbc.Row([
        dbc.Col([
            dbc.Label("Max Gruppen", className="small text-muted mb-1"),
            dbc.Input(
                id="input-max-groups", type="number", min=1, step=1,
                placeholder="∞", size="sm",
                style={"width": "90px"},
            ),
        ], width=2),
        dbc.Col([
            dbc.Label("Max Stunden", className="small text-muted mb-1"),
            dbc.Input(
                id="input-max-hours", type="number", min=0.5, step=0.5,
                placeholder="∞", size="sm",
                style={"width": "90px"},
            ),
        ], width=2),
        dbc.Col([
            dbc.Label("Worker", className="small text-muted mb-1"),
            html.Div([
                dbc.Button("▶ Start",    id="btn-start",   color="success", size="sm", className="me-1"),
                dbc.Button("⏹ Stop",     id="btn-stop",    color="danger",  size="sm", className="me-1"),
                dbc.Button("🔄 Neustart", id="btn-restart", color="info",    size="sm",
                           title="Profile & Toggle-Änderungen laden (wirksam nach Neustart)"),
            ]),
        ], width=4),
    ], className="mb-3 align-items-end g-2"),

    # Thread-Panels: Residential (oben kompakt) + Datacenter (unten, 2×4-Raster)
    dcc.Interval(id="interval-dc-status", interval=30_000, n_intervals=0),

    # Residential Threads + Datacenter Threads nebeneinander
    dbc.Row([
        # Residential Threads (kompakte 2×2-Box)
        dbc.Col(
            dbc.Card(
                dbc.CardBody([
                    html.Div([
                        html.Span("🔄 Residential Threads", className="fw-semibold me-3 small"),
                        html.Span("(Profil wirksam nach Neustart)", className="text-muted small"),
                    ], className="mb-2"),
                    html.Div(id="residential-threads-panel", style={
                        "display": "grid",
                        "gridTemplateColumns": "repeat(2, 1fr)",
                        "gap": "8px",
                        "maxWidth": "370px",
                    }),
                ], className="py-2 px-3"),
                className="h-100",
                style={"borderLeft": "3px solid #1976D2"},
            ),
            width="auto",
        ),

        # Datacenter Threads (5×2-Raster)
        dbc.Col(
            dbc.Card(
                dbc.CardBody([
                    html.Span("🖥 Datacenter Threads", className="fw-semibold small"),
                    html.Span(" · Zeiten in Ortszeit (timezone) · wirksam nach Neustart",
                              className="text-muted small ms-2"),
                    html.Div(id="dc-threads-panel", style={
                        "display": "grid",
                        "gridTemplateColumns": "repeat(5, 1fr)",
                        "gap": "8px",
                        "marginTop": "10px",
                    }),
                ], className="py-2 px-3"),
                className="h-100",
                style={"borderLeft": "3px solid #9C27B0"},
            ),
        ),
    ], className="mb-3 g-2"),

    # Feedback-Meldung nach Button-Klick
    html.Div(id="worker-cmd-out", className="mt-2"),
])

# ---------------------------------------------------------------------------
# Tab 2 — Queue layout
# ---------------------------------------------------------------------------
QUEUE_COLUMNS = [
    {"name": "Priorität",   "id": "priority",        "editable": True,  "type": "numeric"},
    {"name": "Gerät",       "id": "device",          "editable": True,  "presentation": "dropdown"},
    {"name": "Thread",      "id": "thread_affinity", "editable": False},
    {"name": "Föd.",        "id": "federation",      "editable": False},
    {"name": "Kontinent",   "id": "continent",       "editable": False},
    {"name": "Jahr",        "id": "year",            "editable": False, "type": "numeric"},
    {"name": "ELO-Band",    "id": "elo_band",        "editable": False},
    {"name": "Spieler",     "id": "player_count",    "editable": False, "type": "numeric"},
    {"name": "Status",      "id": "status",          "editable": False},
    {"name": "Versuche",    "id": "retries",         "editable": False, "type": "numeric"},
    {"name": "Letzter Lauf","id": "last_run_at",     "editable": False},
]

DEVICE_OPTIONS = [
    {"label": "— (beliebig)",  "value": ""},
    {"label": "mac_mini",      "value": "mac_mini"},
    {"label": "raspi",         "value": "raspi"},
    {"label": "vps",           "value": "vps"},
]

def _build_affinity_options() -> list[dict]:
    """{'Residential' + one entry per configured DC thread}, live from profiles.yaml."""
    _, _dc_id_labels = _dc_thread_maps()
    options = [{"label": "— (Residential)", "value": ""}]
    for thread_id, label in _dc_id_labels.items():
        options.append({"label": label, "value": thread_id})
    return options


AFFINITY_OPTIONS = _build_affinity_options()

tab_queue = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-queue", interval=15_000, n_intervals=0),

    # ── Kopfzeile: Titel · Badge ─────────────────────────────────────────
    dbc.Row([
        dbc.Col(html.H5("Scraping-Queue", className="text-secondary fw-bold my-3"), width="auto"),
        dbc.Col(
            dbc.Badge(id="queue-count", color="primary", className="ms-2 align-self-center"),
            width="auto",
        ),
    ], align="center", className="mb-1"),

    # ── Kategorie-Filter ─────────────────────────────────────────────────
    dbc.Row([
        dbc.Col(
            dbc.RadioItems(
                id="queue-category-filter",
                options=[
                    {"label": "Alle",          "value": "all"},
                    {"label": "🖥 Datacenter",  "value": "dc"},
                    {"label": "🔄 Residential", "value": "residential"},
                    {"label": "🍎 Mac Mini",    "value": "mac_mini"},
                    {"label": "🍓 Raspi",       "value": "raspi"},
                ],
                value="all",
                inline=True,
                className="small",
                inputClassName="me-1",
                labelClassName="me-3",
            ),
            width="auto",
        ),
        dbc.Col(
            dcc.Dropdown(
                id="queue-dc-filter",
                options=[{"label": "Alle DC-Threads", "value": "dc"}] + AFFINITY_OPTIONS[1:],
                value="dc",
                clearable=False,
                style={"width": "160px", "fontSize": "0.85rem", "display": "none"},
            ),
            width="auto",
            className="align-self-center",
        ),
    ], className="mb-2 align-items-center g-2"),

    dash_table.DataTable(
        id="queue-table",
        columns=QUEUE_COLUMNS,
        data=[],
        editable=True,
        row_selectable="single",
        dropdown={
            "device":           {"options": DEVICE_OPTIONS,   "clearable": True},
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
            {"if": {"column_id": "priority"},        "backgroundColor": "#FFFDE7"},
            {"if": {"column_id": "device"},          "backgroundColor": "#E8F5E9"},
            {"if": {"column_id": "thread_affinity"}, "backgroundColor": "#EDE7F6"},
            # DC-Zuweisung (DC-DE / DC-IN etc.)
            {"if": {"filter_query": '{thread_affinity} contains "DC"', "column_id": "thread_affinity"},
             "color": "#4527A0", "fontWeight": "bold"},
            # Läuft gerade (▶ T1 / ▶ DC-IN)
            {"if": {"filter_query": '{thread_affinity} contains "▶"', "column_id": "thread_affinity"},
             "backgroundColor": "#E3F2FD", "color": "#1565C0", "fontWeight": "bold"},
        ],
        tooltip_header={
            "priority":       "Klicken zum Bearbeiten — niedrigerer Wert = früher gescrapt",
            "device":         "Gerät zuweisen — leer = beliebiges Gerät",
            "thread_affinity":"Thread-Zuweisung (Dropdown) · ▶ = läuft gerade",
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
    {"name": "Größe (MB)",      "id": "mb"},
    {"name": "Dauer (h)",      "id": "duration_h"},
    {"name": "Rate/h",         "id": "rate_per_h"},
    {"name": "Thread",         "id": "thread_slot"},
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
            {"if": {"filter_query": '{thread_slot} = "T1"', "column_id": "thread_slot"},
             "backgroundColor": "#BBDEFB", "fontWeight": "bold", "color": "#0D47A1"},
            {"if": {"filter_query": '{thread_slot} = "T2"', "column_id": "thread_slot"},
             "backgroundColor": "#C8E6C9", "fontWeight": "bold", "color": "#1B5E20"},
            {"if": {"filter_query": '{thread_slot} = "T3"', "column_id": "thread_slot"},
             "backgroundColor": "#FFF9C4", "fontWeight": "bold", "color": "#F57F17"},
            {"if": {"filter_query": '{thread_slot} = "T4"', "column_id": "thread_slot"},
             "backgroundColor": "#B2EBF2", "fontWeight": "bold", "color": "#006064"},
            {"if": {"filter_query": '{thread_slot} contains "DC"', "column_id": "thread_slot"},
             "backgroundColor": "#EDE7F6", "fontWeight": "bold", "color": "#4527A0"},
        ],
        tooltip_header={
            "thread_slot": "Thread der diesen Job bearbeitet hat (T1–T4 = Residential, DC-XX = Datacenter)",
        },
    ),
], className="py-3")

# ---------------------------------------------------------------------------
# Tab: Bericht — tägliche MB nach Thread
# ---------------------------------------------------------------------------

# Slot → Label-Mapping (inkl. neue DC-Scraper).
# Residential: Slots 0–3 → Anzeige als T1–T4 (1-basiert, wie in Steuerung)
# Datacenter:  Slots 99+ (nach Bedarf erweiterbar)
_REPORT_SLOT_LABELS = {
    0:   "T1",
    1:   "T2",
    2:   "T3",
    3:   "T4",
    50:  "Pi",
    **_dc_thread_maps()[0],   # live aus profiles.yaml, inkl. dc_update_1/2/3
}
_RESIDENTIAL_SLOTS = {0, 1, 2, 3, 50}   # alle niedrigen Slots = Residential; 50 = Pi

_DE_MONTHS = ["Jan", "Feb", "Mär", "Apr", "Mai", "Jun",
              "Jul", "Aug", "Sep", "Okt", "Nov", "Dez"]

def _fmt_day_de(day_str: str) -> str:
    """'2026-05-26' → '26 Mai 2026'"""
    try:
        y, m, d = day_str.split("-")
        return f"{int(d)} {_DE_MONTHS[int(m)-1]} {y}"
    except Exception:
        return day_str

_REPORT_COLORS = {
    "T1":    "#1565C0",
    "T2":    "#42A5F5",
    "T3":    "#26A69A",
    "T4":    "#78909C",
    "Pi":    "#880E4F",
    "DC-DE": "#E65100",
    "DC-IN": "#FF8F00",
    "DC-UK": "#2E7D32",
    "DC-US": "#66BB6A",
    "DC-HK": "#6A1B9A",
    "DC-ES": "#C62828",
    "DC-MX": "#00838F",
    "DC-AE": "#4E342E",
    "DC-DACH":     "#9E9D24",
    "DC-UPDATE-1": "#5D4037",
}

tab_bericht = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-bericht", interval=60_000, n_intervals=0),
    dbc.Row(dbc.Col(html.H5("Scraping-Bericht — tägliches Datenvolumen",
                            className="text-secondary fw-bold my-3"))),
    html.Div(id="bericht-table"),
], className="py-3")

# ── Bericht-Länder: statische Spalten & Styling ─────────────────────────────
_B2_COLS = [
    {"name": ["",         "Föd."],          "id": "_fed"},
    {"name": ["Gruppen",  "Abs."],          "id": "_gruppen"},
    {"name": ["Gruppen",  "%"],             "id": "_gruppen_pct"},
    {"name": ["Zeitraum", "Geplant"],       "id": "_zeitraum_plan"},
    {"name": ["Zeitraum", "Gescraped"],     "id": "_zeitraum_done"},
    {"name": ["Zeitraum", "Laufend"],       "id": "_laufend"},
    {"name": ["Spieler",  "Abs."],           "id": "_fide_aktiv"},
    {"name": ["Spieler",  "%"],             "id": "_fide_aktiv_pct"},
    {"name": ["",         "MB"],            "id": "_mb"},
]
_B2_GRP_STARTS = ["_gruppen", "_zeitraum_plan", "_fide_aktiv", "_mb"]
_B2_SDC = [
    # Zebra nur für country-Zeilen
    {"if": {"row_index": "odd", "filter_query": '{_row_type} = "country"'},
     "backgroundColor": "#f9fbfd"},
    # 🌍 Welt — dunkelgrau, fett
    {"if": {"filter_query": '{_row_type} = "world"'},
     "backgroundColor": "#dee2e6", "fontWeight": "bold", "color": "#212529"},
    # Kontinent — hellblau, fett, Trennlinie oben; cursor: pointer
    {"if": {"filter_query": '{_row_type} = "continent"'},
     "backgroundColor": "#e8f0fe", "fontWeight": "bold", "color": "#1a3a6b",
     "borderTop": "2px solid #adb5bd", "cursor": "pointer"},
    # Subgruppe 🔄 In Arbeit — grün-weiss; cursor: pointer
    {"if": {"filter_query": '{_row_type} = "subgroup_scraped"'},
     "backgroundColor": "#f0fdf4", "fontWeight": "600", "color": "#198754",
     "cursor": "pointer"},
    # Subgruppe ○ Ohne Daten — hellgrau; cursor: pointer
    {"if": {"filter_query": '{_row_type} = "subgroup_nodata"'},
     "backgroundColor": "#f5f5f5", "fontWeight": "600", "color": "#777",
     "cursor": "pointer"},
    # Laufend-Spalte: orange wenn befüllt
    {"if": {"filter_query": '{_laufend} != ""', "column_id": "_laufend"},
     "backgroundColor": "#FFF3CD", "color": "#856404", "fontWeight": "600"},
    # Gruppen %: grün wenn 100 %
    {"if": {"filter_query": '{_gruppen_pct} = "100.0 %"', "column_id": "_gruppen_pct"},
     "color": "#198754", "fontWeight": "600"},
    # Spieler %: grün wenn 100 %
    {"if": {"filter_query": '{_fide_aktiv_pct} = "100.0 %"',
            "column_id": "_fide_aktiv_pct"},
     "color": "#198754", "fontWeight": "600"},
]

tab_bericht2 = dbc.Container(fluid=True, children=[
    dcc.Interval(id="interval-bericht2", interval=300_000, n_intervals=0),
    # Expand-State: welche Kontinente/Subgruppen aufgeklappt sind
    dcc.Store(id="bericht2-expand-state",
              data={"continents": ["Europe"], "subgroups": {}}),
    # Rohdaten-Cache: wird vom Interval befüllt
    dcc.Store(id="bericht2-raw-data", data=[]),
    html.P(
        [html.B("Klick auf Kontinent- oder Untergruppen-Zeile"), " klappt sie auf/zu.  "
         "Zeitraum Gescraped = Jahres-Range der 'done'-Gruppen im VPS-Orchestrator "
         "(Mac-Mini-Backfills fließen hier nicht ein)."],
        style={"fontSize": "11px", "color": "#777", "marginBottom": "8px"},
    ),
    html.Div(
        dash_table.DataTable(
            id="bericht2-datatable",
            columns=_B2_COLS,
            data=[],
            merge_duplicate_headers=True,
            sort_action="none",
            page_size=500,
            style_table={"overflowX": "auto"},
            style_cell={
                "backgroundColor": "#FFFFFF",
                "color":           "#333",
                "border":          "1px solid #dee2e6",
                "fontFamily":      "monospace",
                "fontSize":        "15px",
                "padding":         "10px 14px",
                "textAlign":       "center",
                "whiteSpace":      "nowrap",
            },
            style_cell_conditional=[
                *[{"if": {"column_id": c}, "borderLeft": "2px solid #adb5bd"}
                  for c in _B2_GRP_STARTS],
                # Föd.-Spalte linksbündig (enthält Einrückung + [+]/[−])
                {"if": {"column_id": "_fed"}, "textAlign": "left",
                 "minWidth": "200px", "maxWidth": "320px"},
            ],
            style_header={
                "fontWeight": "bold", "color": "#444",
                "border": "1px solid #dee2e6", "fontSize": "13px",
                "textAlign": "center", "backgroundColor": "#f0f4f8",
                "padding": "10px 14px",
            },
            style_header_conditional=[
                *[{"if": {"column_id": c}, "backgroundColor": "#dbeafe"}
                  for c in ["_gruppen", "_gruppen_pct"]],
                *[{"if": {"column_id": c}, "backgroundColor": "#fef9c3"}
                  for c in ["_zeitraum_plan", "_zeitraum_done", "_laufend"]],
                *[{"if": {"column_id": c}, "backgroundColor": "#dcfce7"}
                  for c in ["_fide_aktiv", "_fide_aktiv_pct"]],
                *[{"if": {"column_id": c}, "borderLeft": "2px solid #adb5bd"}
                  for c in _B2_GRP_STARTS],
            ],
            style_data_conditional=_B2_SDC,
            style_as_list_view=True,
        ),
        style={
            "backgroundColor": "#FFFFFF",
            "border":          "1px solid #E0E0E0",
            "borderRadius":    "6px",
            "padding":         "12px 16px",
        },
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
        dbc.Tab(tab_overview, label="🌍 Übersicht",        tab_id="tab-overview"),
        dbc.Tab(tab_map,      label="🌐 Karte",             tab_id="tab-map"),
        dbc.Tab(tab_land,     label="🗺️ Übersicht Land",   tab_id="tab-land"),
        dbc.Tab(tab_heatmap,  label="⚙️ Steuerung",        tab_id="tab-heatmap"),
        dbc.Tab(tab_queue,    label="📋 Queue",             tab_id="tab-queue"),
        dbc.Tab(tab_completed,label="✅ Abgeschlossen",     tab_id="tab-completed"),
        dbc.Tab(tab_bericht,  label="📊 Bericht Scraper",    tab_id="tab-bericht"),
        dbc.Tab(tab_bericht2, label="🗺 Bericht Länder",     tab_id="tab-bericht2"),
    ], id="main-tabs", active_tab="tab-heatmap"),
], style={"backgroundColor": "#F8F9FA", "minHeight": "100vh", "paddingBottom": "40px"})


# ===========================================================================
# Callbacks — Tab 1 (Heatmap)
# ===========================================================================

_PINNED_FEDS = ["GER", "SUI", "AUT"]   # immer oben, unabhängig vom Kontinent

@app.callback(
    Output("dd-federation", "options"),
    Output("dd-federation", "value"),
    Input("dd-continent", "value"),
)
def update_federation_dropdown(continent):
    feds = query_federations(continent)

    # Favoriten oben (immer, unabhängig vom gewählten Kontinent)
    opts = [{"label": f"★ {f}", "value": f} for f in _PINNED_FEDS]
    opts.append({"label": "──────────────", "value": "__sep__", "disabled": True})
    # Kontinent-Liste ohne Dopplungen mit Favoriten
    opts += [{"label": f, "value": f} for f in feds if f not in _PINNED_FEDS]

    # Default: GER
    default = "GER"
    return opts, default


def _build_worker_status_widget(ws: dict) -> html.Div:
    """Shared helper: baut das Worker-Status-Widget aus worker_state.json."""
    import time as _time

    _SLOT_BADGE = ["primary", "success", "warning", "info"]
    _DC_SLOT_LABELS, _ = _dc_thread_maps()
    _PROFILE_ABBR = {
        "semi_aggressive":  "semi-aggr.",
        "aggressive":       "aggr.",
        "normal":           "normal",
        "semi_conservative":"semi-conv.",
        "conservative":     "conserv.",
    }

    def _speed_eta(started_at, c_done, c_total):
        if not started_at or not c_done:
            return ""
        elapsed = _time.time() - started_at
        if elapsed <= 0:
            return ""
        cph = c_done / elapsed * 3600
        s = f"{cph:.0f} c/h"
        if c_total and c_total > c_done:
            eta_sec = (c_total - c_done) / (c_done / elapsed)
            s += f" · ETA {int(eta_sec // 3600)}h{int((eta_sec % 3600) // 60):02d}m"
        return s

    cmd           = ws.get("command", "stopped")
    done          = ws.get("groups_done", 0)
    max_g         = ws.get("max_groups")
    max_h         = ws.get("max_hours")
    threads       = ws.get("threads", [])
    current_group = ws.get("current_group")
    done_total    = ws.get("groups_done", 0)
    max_w         = ws.get("max_workers", 1)

    limits = []
    if max_g: limits.append(f"{done}/{max_g} Gruppen")
    if max_h: limits.append(f"max {max_h}h")
    limit_str = f" · {', '.join(limits)}" if limits else ""

    status_children = [
        html.Div(
            f"Worker: {cmd}{limit_str}" + (f" · {max_w}×" if max_w > 1 else ""),
            className="fw-semibold mb-1",
        )
    ]

    if threads:
        thread_blocks = []
        for t in sorted(threads, key=lambda x: x.get("slot", 0)):
            slot       = t.get("slot", 0)
            t_profile  = t.get("profile", "?")
            grp        = t.get("current_group", "–")
            c_done     = t.get("combos_done", 0)
            c_total    = t.get("combos_total")
            n_players  = t.get("player_count")
            started_at = t.get("group_started_at")

            parts = grp.split("/")
            fed  = parts[0] if parts else grp
            year = parts[1] if len(parts) > 1 else ""
            elo  = parts[2] if len(parts) > 2 else ""

            combo_str  = f"{c_done}/{c_total}" if c_total else str(c_done)
            player_str = f"{n_players}P · " if n_players else ""
            perf_str   = _speed_eta(started_at, c_done, c_total)
            abbr       = _PROFILE_ABBR.get(t_profile, t_profile[:4].upper())
            is_sleeping = grp.startswith("💤")

            if slot in _DC_SLOT_LABELS:
                dc_label    = _DC_SLOT_LABELS[slot]
                badge_cls   = "badge bg-secondary me-1" + (" opacity-50" if is_sleeping else "")
                slot_label  = dc_label + (" 💤" if is_sleeping else "")
                badge_color = "secondary"
            else:
                badge_color = _SLOT_BADGE[slot % len(_SLOT_BADGE)]
                badge_cls   = f"badge bg-{badge_color} me-1" + (
                    " text-dark" if badge_color == "warning" else "")
                slot_label  = f"T{slot + 1}"

            grp_str = grp if is_sleeping else (f"{fed}/{year} · {elo}" if elo else grp)
            lines = [
                html.Div([
                    html.Span(slot_label, className=badge_cls),
                    html.Span(f"{abbr}  {grp_str}", className="fw-semibold"),
                ], className="lh-sm"),
                html.Div(
                    f"{player_str}{combo_str}" + (f"  {perf_str}" if perf_str else ""),
                    className="text-muted lh-sm",
                ),
            ]
            thread_blocks.append(html.Div(lines, style={
                "borderLeft": f"3px solid var(--bs-{badge_color})",
                "paddingLeft": "8px",
                "marginRight": "16px",
            }))

        status_children.append(
            html.Div(thread_blocks, className="d-flex flex-wrap align-items-start mt-1")
        )
        status_children.append(
            html.Div(f"{done_total} Gruppen ✓", className="text-muted mt-1")
        )

    elif current_group:
        profile_name = ws.get("current_profile", "?")
        c_done    = ws.get("combos_done", 0)
        c_total   = ws.get("combos_total")
        n_players = ws.get("player_count")
        started_at = ws.get("group_started_at")

        parts = current_group.split("/")
        fed  = parts[0] if parts else current_group
        year = ws.get("current_year", "")
        elo  = parts[2] if len(parts) > 2 else ""
        grp_str = f"{fed}/{year} · {elo}" if elo else current_group

        combo_str  = f"{c_done}/{c_total}" if c_total else str(c_done)
        player_str = f"{n_players}P · " if n_players else ""
        perf_str   = _speed_eta(started_at, c_done, c_total)

        status_children += [
            html.Div(html.Span(f"[{profile_name}]  {grp_str}", className="fw-semibold"),
                     className="mb-1 lh-sm"),
            html.Div(
                f"{player_str}{combo_str}" + (f"  {perf_str}" if perf_str else ""),
                className="text-muted",
            ),
            html.Div(f"{done_total} Gruppen ✓", className="text-muted mt-1 border-top pt-1"),
        ]

    return html.Div(status_children, className="small")


@app.callback(
    Output("grid", "figure"),
    Input("interval-land", "n_intervals"),
    Input("dd-federation", "value"),
    Input("main-tabs", "active_tab"),
)
def refresh_land_grid(_, federation, active_tab):
    """Aktualisiert die Federation×Jahr-Heatmap (Tab Übersicht Land)."""
    if active_tab != "tab-land":
        return dash.no_update
    if not federation:
        return dash.no_update
    return build_figure(federation)


@app.callback(
    Output("stat-total",    "children"),
    Output("stat-done",     "children"),
    Output("stat-pending",  "children"),
    Output("stat-running",  "children"),
    Output("stat-failed",   "children"),
    Output("stat-skipped",  "children"),
    Input("interval-control", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_control_stats(_, active_tab):
    """Aktualisiert Metric-Cards (Tab Steuerung)."""
    if active_tab != "tab-heatmap":
        return [dash.no_update] * 6
    s = query_global_stats()
    return (
        f"{s['total']:,}", f"{s['done']:,}", f"{s['pending']:,}",
        f"{s['running']:,}", f"{s['failed']:,}", f"{s['skipped']:,}",
    )


@app.callback(
    Output({"type": "dc-hours-start", "id": dash.ALL}, "value"),
    Input({"type": "dc-hours-start",  "id": dash.ALL}, "value"),
    State({"type": "dc-hours-end",    "id": dash.ALL}, "value"),
    State({"type": "dc-hours-start",  "id": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def save_dc_hours_start(starts, ends, ids):
    """Persist Startzeit für DC-Thread (liest End-Zeit aus State)."""
    triggered = callback_context.triggered_id
    if triggered and isinstance(triggered, dict):
        dc_id = triggered.get("id")
        for s, e, id_dict in zip(starts, ends, ids):
            if id_dict.get("id") == dc_id and s is not None and e is not None:
                _save_dc_thread_active_hours(dc_id, int(s), int(e))
                break
    return starts


@app.callback(
    Output({"type": "dc-hours-end",  "id": dash.ALL}, "value"),
    Input({"type": "dc-hours-end",   "id": dash.ALL}, "value"),
    State({"type": "dc-hours-start", "id": dash.ALL}, "value"),
    State({"type": "dc-hours-end",   "id": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def save_dc_hours_end(ends, starts, ids):
    """Persist Endzeit für DC-Thread (liest Start-Zeit aus State)."""
    triggered = callback_context.triggered_id
    if triggered and isinstance(triggered, dict):
        dc_id = triggered.get("id")
        for s, e, id_dict in zip(starts, ends, ids):
            if id_dict.get("id") == dc_id and s is not None and e is not None:
                _save_dc_thread_active_hours(dc_id, int(s), int(e))
                break
    return ends


@app.callback(
    Output("dc-threads-panel", "children"),
    Input("interval-dc-status", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_dc_threads_panel(_, active_tab):
    """Zeigt alle DC-Threads mit Status, individuellen Zeiten und Toggle."""
    threads    = _get_dc_thread_status()
    ws_threads = {t.get("slot"): t for t in read_worker_state().get("threads", [])}

    cards = []
    for t in threads:
        slot       = t["slot"]
        is_enabled = t["enabled"]
        has_creds  = t["has_credentials"]
        is_active  = t["is_active_hours"]
        local_time = t["local_time"]
        label      = t["label"]
        feds       = ", ".join(t["federations"]) if t["federations"] else "—"
        ws         = ws_threads.get(slot, {})
        is_running  = bool(ws.get("current_group"))
        is_sleeping = str(ws.get("current_group", "")).startswith("💤")
        h_start, h_end = t["active_hours"]

        # Status-Badge
        if not has_creds:
            status_badge_el = dbc.Badge("kein Proxy", color="light", text_color="secondary")
        elif is_sleeping:
            status_badge_el = dbc.Badge("💤 schläft", color="secondary")
        elif is_running:
            status_badge_el = dbc.Badge("▶ aktiv", color="success")
        elif is_enabled and is_active:
            status_badge_el = dbc.Badge("bereit", color="info")
        elif is_enabled and not is_active:
            status_badge_el = dbc.Badge("außerhalb", color="warning", text_color="dark")
        else:
            status_badge_el = dbc.Badge("aus", color="light", text_color="muted")

        # Toggle: aktiv wenn Credentials vorhanden
        toggle_el = dbc.Switch(
            id={"type": "dc-thread-toggle", "id": t["id"]},
            value=is_enabled,
            disabled=not has_creds,
            className="d-inline-block align-middle",
            style={"transform": "scale(0.8)"},
        )

        # Randfarbe: grün=aktiv, orange=außerhalb Zeitfenster, grau=kein Proxy/aus
        border_color = (
            "#4CAF50" if (has_creds and is_enabled and is_active)
            else ("#FF9800" if (has_creds and is_enabled and not is_active)
            else "#9E9E9E")
        )

        card = dbc.Card([
            dbc.CardBody([
                # Label + Toggle
                html.Div([
                    html.Span(label, className="fw-bold me-2 small"),
                    toggle_el,
                ], className="d-flex align-items-center mb-1"),
                # Ortszeit
                html.Div(
                    f"🕐 {local_time} ({t['timezone'].split('/')[-1]})",
                    className="text-muted small" + ("" if is_active else " text-warning"),
                ),
                # Status
                html.Div(status_badge_el, className="my-1"),
                # Föderationen
                html.Div(feds, className="text-muted", style={"fontSize": "0.72rem"}),
                # Von / Bis Zeitfenster (individuell pro Scraper)
                html.Div([
                    html.Span("Von", className="text-muted me-1",
                              style={"fontSize": "0.72rem"}),
                    dbc.Input(
                        id={"type": "dc-hours-start", "id": t["id"]},
                        type="number", min=0, max=23, step=1,
                        value=h_start, debounce=True, size="sm",
                        style={"width": "48px", "fontSize": "0.75rem",
                               "display": "inline-block"},
                    ),
                    html.Span("–", className="text-muted mx-1",
                              style={"fontSize": "0.72rem"}),
                    dbc.Input(
                        id={"type": "dc-hours-end", "id": t["id"]},
                        type="number", min=0, max=24, step=1,
                        value=h_end, debounce=True, size="sm",
                        style={"width": "48px", "fontSize": "0.75rem",
                               "display": "inline-block"},
                    ),
                    html.Span("Uhr", className="text-muted ms-1",
                              style={"fontSize": "0.72rem"}),
                ], className="d-flex align-items-center mt-1"),
            ], className="p-2"),
        ], style={"borderLeft": f"3px solid {border_color}"})
        cards.append(card)

    return cards


@app.callback(
    Output({"type": "dc-thread-toggle", "id": dash.ALL}, "value"),
    Input({"type": "dc-thread-toggle", "id": dash.ALL}, "value"),
    State({"type": "dc-thread-toggle", "id": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def toggle_dc_thread(values, ids):
    """Persist DC-Thread enabled-Flag per Toggle-Klick (runtime_settings.json)."""
    triggered = callback_context.triggered_id
    if triggered and isinstance(triggered, dict):
        dc_id = triggered.get("id")
        for val, id_dict in zip(values, ids):
            if id_dict.get("id") == dc_id:
                _save_dc_thread_enabled(dc_id, bool(val))
                break
    return values



@app.callback(
    Output("residential-threads-panel", "children"),
    Input("interval-dc-status", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_residential_threads_panel(_, active_tab):
    """Zeigt T0–T3 mit Toggle, Profil-Dropdown und laufender Gruppe."""
    import time as _time

    if active_tab != "tab-heatmap":
        return dash.no_update

    threads      = _get_residential_thread_status()
    profile_opts = [{"label": p, "value": p} for p in
                    ["semi_aggressive", "normal", "semi_conservative",
                     "aggressive", "conservative"]]

    cards = []
    for t in threads:
        slot        = t["slot"]
        is_enabled  = t["enabled"]
        badge_color = t["badge_color"]
        profile     = t["profile"]
        grp         = t["current_group"]
        is_running  = bool(grp)

        # Laufende Gruppe + Fortschritt
        if is_running:
            c_done    = t["combos_done"]
            c_total   = t["combos_total"]
            started   = t["started_at"]
            combo_str = f"{c_done}/{c_total}" if c_total else str(c_done)
            if started and c_done:
                elapsed   = _time.time() - started
                cph       = c_done / elapsed * 3600 if elapsed > 0 else 0
                speed_str = f"  {cph:.0f} c/h"
            else:
                speed_str = ""
            parts    = grp.split("/")
            grp_disp = f"{parts[0]}/{parts[1]} · {parts[2]}" if len(parts) > 2 else grp
            status_el = html.Div([
                html.Div(grp_disp, className="fw-semibold", style={"fontSize": "0.78rem"}),
                html.Div(combo_str + speed_str, className="text-muted",
                         style={"fontSize": "0.75rem"}),
            ])
        elif is_enabled:
            status_el = html.Div("bereit", className="text-info",
                                 style={"fontSize": "0.78rem"})
        else:
            status_el = html.Div("inaktiv", className="text-muted",
                                 style={"fontSize": "0.78rem"})

        border_color = f"var(--bs-{badge_color})" if is_enabled else "#9E9E9E"

        card = dbc.Card([
            dbc.CardBody([
                # Slot-Badge + Toggle (analog zu DC-Karten)
                html.Div([
                    html.Span(
                        t["label"],
                        className=f"badge bg-{badge_color} me-2"
                                  + (" text-dark" if badge_color == "warning" else ""),
                    ),
                    dbc.Switch(
                        id={"type": "residential-toggle", "slot": slot},
                        value=is_enabled,
                        className="d-inline-block align-middle",
                        style={"transform": "scale(0.8)"},
                    ),
                ], className="d-flex align-items-center mb-1"),
                # Profil-Dropdown
                dcc.Dropdown(
                    id={"type": "residential-profile-dd", "slot": slot},
                    options=profile_opts,
                    value=profile,
                    clearable=False,
                    style={"fontSize": "0.78rem", "minWidth": "130px"},
                    className="mb-1",
                ),
                # Status / laufende Gruppe
                status_el,
            ], className="p-2"),
        ], style={"minWidth": "155px", "maxWidth": "175px",
                  "borderLeft": f"3px solid {border_color}"})
        cards.append(card)

    return cards


@app.callback(
    Output({"type": "residential-profile-dd", "slot": dash.ALL}, "value"),
    Input({"type": "residential-profile-dd", "slot": dash.ALL}, "value"),
    State({"type": "residential-profile-dd", "slot": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def save_residential_profile(values, ids):
    """Persist Profil-Änderung eines Residential-Slots (runtime_settings.json)."""
    triggered = callback_context.triggered_id
    if triggered and isinstance(triggered, dict):
        slot = triggered.get("slot")
        for val, id_dict in zip(values, ids):
            if id_dict.get("slot") == slot and val:
                _save_worker_profile_for_slot(slot, val)
                break
    return values


@app.callback(
    Output({"type": "residential-toggle", "slot": dash.ALL}, "value"),
    Input({"type": "residential-toggle", "slot": dash.ALL}, "value"),
    State({"type": "residential-toggle", "slot": dash.ALL}, "id"),
    prevent_initial_call=True,
)
def toggle_residential_slot(values, ids):
    """Persist enabled-Flag eines Residential-Slots (runtime_settings.json)."""
    triggered = callback_context.triggered_id
    if triggered and isinstance(triggered, dict):
        slot = triggered.get("slot")
        for val, id_dict in zip(values, ids):
            if id_dict.get("slot") == slot:
                _save_residential_slot_enabled(slot, bool(val))
                break
    return values


@app.callback(
    Output("worker-cmd-out", "children"),
    Input("btn-start",   "n_clicks"),
    Input("btn-stop",    "n_clicks"),
    Input("btn-restart", "n_clicks"),
    State("input-max-groups", "value"),
    State("input-max-hours",  "value"),
    prevent_initial_call=True,
)
def handle_worker_buttons(start, stop, restart, max_groups, max_hours):
    triggered = callback_context.triggered_id
    if triggered == "btn-start":
        # write_state ist atomar und lässt max_groups/max_hours=None einen
        # evtl. vorhandenen alten Limit-Wert überschreiben (Key existiert dann).
        state_io.write_state(
            command="run",
            max_groups=int(max_groups) if max_groups else None,
            max_hours=float(max_hours) if max_hours else None,
            started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
            groups_done=0,
        )
        msg = ("✅ Worker gestartet", "success")
    elif triggered == "btn-stop":
        write_worker_state("stopped")
        msg = ("⏹ Stop-Befehl gesetzt — Threads beenden aktuelle Gruppe und stoppen", "warning")
    elif triggered == "btn-restart":
        write_worker_state("restart")
        # Aktuelle Konfiguration aus profiles.yaml lesen für Bestätigungstext
        cfg        = _get_concurrency_cfg()
        slots      = cfg.get("worker_slots", [])
        active_res = sum(1 for s in slots if s.get("enabled", False)) if slots else cfg.get("max_workers", 1)
        dc_threads = [t for t in cfg.get("datacenter_threads", []) if t.get("enabled", False)]
        dc_parts   = []
        for t in dc_threads:
            h = t.get("active_hours", [7, 23])
            dc_parts.append(f"{t['label']} {h[0]}–{h[1]}")
        dc_str = ", ".join(dc_parts) or "–"
        msg = (
            f"🔄 Neustart gesetzt — {active_res}× Residential | DC: {dc_str}",
            "info"
        )
    else:
        return ""
    return dbc.Alert(msg[0], color=msg[1], duration=8000,
                     className="small py-2 mb-0 mt-2", is_open=True)


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
    Output("queue-dc-filter", "style"),
    Input("queue-category-filter", "value"),
)
def toggle_dc_sub_filter(category):
    """DC-Sub-Dropdown nur anzeigen wenn 'Datacenter' aktiv."""
    if category == "dc":
        return {"width": "160px", "fontSize": "0.85rem", "display": "inline-block"}
    return {"width": "160px", "fontSize": "0.85rem", "display": "none"}


@app.callback(
    Output("queue-table", "data"),
    Output("queue-count", "children"),
    Input("interval-queue", "n_intervals"),
    Input("main-tabs", "active_tab"),
    Input("queue-category-filter", "value"),
    Input("queue-dc-filter", "value"),
)
def refresh_queue(_, active_tab, category_filter, dc_sub_filter):
    if active_tab != "tab-queue":
        return dash.no_update, dash.no_update

    # DC-Sub-Filter: spezifischen DC-Thread oder alle DC
    if category_filter == "dc" and dc_sub_filter and dc_sub_filter != "dc":
        f = dc_sub_filter  # z.B. 'dc_in' → filtert WHERE thread_affinity='dc_in'
        _, _dc_id_labels = _dc_thread_maps()
        cat_label = _dc_id_labels.get(f, f)
    elif category_filter == "all" or not category_filter:
        f = None
        cat_label = "alle"
    else:
        f = category_filter
        cat_label = {"dc": "Datacenter", "residential": "Residential",
                     "mac_mini": "Mac Mini", "raspi": "Raspi"}.get(f, f)

    rows = query_queue(affinity_filter=f)
    return rows, f"{len(rows):,} Gruppen ({cat_label})"


@app.callback(
    Output("queue-save-out", "children"),
    Input("queue-table", "data"),
    State("queue-table", "data_previous"),
    prevent_initial_call=True,
)
def save_queue_edits(current_data, previous_data):
    """Persist priority, device and thread_affinity changes made directly in the table."""
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
        # thread_affinity ist read-only in der Tabelle → kein Speichern nötig
    return ""


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


# ===========================================================================
# Callbacks — Tab 0b (Karte)
# ===========================================================================

@app.callback(
    Output("map-graph", "figure"),
    Input("interval-map", "n_intervals"),
    Input("main-tabs", "active_tab"),
    Input("map-continent", "value"),
)
def refresh_map(_, active_tab, continent):
    if active_tab not in ("tab-map", None):
        return dash.no_update
    return build_map_figure(continent or "Welt")


@app.callback(
    Output("map-detail-panel", "children"),
    Input("map-graph", "clickData"),
)
def map_click(click_data):
    fed_str = None
    if click_data:
        point = click_data["points"][0]
        cd = point.get("customdata")
        if cd:
            fed_str = cd[0]
    return build_map_panel(fed_str)


# ===========================================================================
# Callbacks — Tab 5 (Bericht)
# ===========================================================================

def _query_bericht_data() -> list[dict]:
    """Tägliches MB-Volumen pro Thread-Slot — Slot-Labels aus der Config."""
    return store.query_bericht_data(_REPORT_SLOT_LABELS)


# Direkt aus store durchgereicht (Review #6): PG-Spielerzahlen (gecacht in
# store) und Länder-Aggregat.
_query_pg_players = store.query_pg_players
_query_laender_data = store.query_laender_data


def _make_subtotal_row(label: str, group: list, row_type: str) -> dict:
    """Baut eine Summenzeile (Kontinent oder Welt) aus einer Gruppe von Länder-Zeilen."""
    dg = sum(r["_r_done_g"]              for r in group)
    tg = sum(r["_r_total_g"]             for r in group)
    fs = sum(r.get("_r_fide_scraped", 0) for r in group)
    ft = sum(r.get("_r_fide_total",   0) for r in group)
    mb = round(sum(r["_r_mb"]            for r in group), 1)
    yp0 = min((r["_r_yp0"] for r in group if r["_r_yp0"]), default=None)
    yp1 = max((r["_r_yp1"] for r in group if r["_r_yp1"]), default=None)
    yd0 = min((r["_r_yd0"] for r in group if r["_r_yd0"]), default=None)
    yd1 = max((r["_r_yd1"] for r in group if r["_r_yd1"]), default=None)
    def _yr(y0, y1):
        if y0 is None: return "—"
        return str(y0) if y0 == y1 else f"{y0} – {y1}"
    return {
        "_fed":           label,
        "_gruppen":       f"{dg} / {tg}",
        "_gruppen_pct":   f"{round(dg / tg * 100, 1)} %" if tg else "—",
        "_zeitraum_plan": _yr(yp0, yp1),
        "_zeitraum_done": _yr(yd0, yd1),
        "_laufend":        "",
        "_fide_aktiv":     f"{fs} / {ft}" if ft else "—",
        "_fide_aktiv_pct": f"{round(fs / ft * 100, 1)} %" if ft else "—",
        "_mb":             mb,
        "_row_type":       row_type,
        # Rohwerte für verschachtelte Summierung
        "_r_fide_scraped": fs,
        "_r_fide_total":   ft,
    }


def _b2_build_table_data(raw_rows: list, expand_state: dict) -> list:
    """Baut die Tabellenzeilen für Bericht Länder inkl. Auf-/Zuklapp-Logik.

    Zeilen-Hierarchie:
      world        — immer sichtbar  (🌍 Welt)
      continent    — immer sichtbar; Klick in _fed → Auf-/Zuklappen
      subgroup_*   — nur wenn Kontinent expandiert; Klick → Auf-/Zuklappen
      country      — nur wenn Kontinent UND Subgruppe expandiert

    Expand-State (dcc.Store "bericht2-expand-state"):
      continents: list[str]          — z.B. ["Europe"]
      subgroups:  dict[str, bool]    — z.B. {"Europe/pend": True, "Europe/done": False}
    """
    expanded_conts = set(expand_state.get("continents", ["Europe"]))
    expanded_sgs   = expand_state.get("subgroups", {})

    all_conts = sorted(
        set(r["_continent"] for r in raw_rows),
        key=lambda c: (0 if c == "Europe" else 1, c),
    )

    # Welt-Zeile (immer oben)
    welt = _make_subtotal_row("🌍 Welt", raw_rows, "world")
    welt["_continent"] = ""
    welt["_sg_key"]    = ""
    table_data = [welt]

    for cont in all_conts:
        cont_rows = sorted(
            [r for r in raw_rows if r["_continent"] == cont],
            key=lambda r: r["_fed"],
        )
        is_open  = cont in expanded_conts
        icon     = "[−]" if is_open else "[+]"

        cont_sum = _make_subtotal_row(f"{icon} {cont}", cont_rows, "continent")
        cont_sum["_continent"] = cont
        cont_sum["_sg_key"]    = ""
        table_data.append(cont_sum)

        if not is_open:
            continue

        # Gescraped: mind. 1 Gruppe done ODER gerade running
        scraped_rows = [r for r in cont_rows
                        if r["_r_done_g"] > 0 or r["_r_running_g"] > 0]
        # Ohne Daten: kein einziger done/running-Lauf
        nodata_rows  = [r for r in cont_rows
                        if r["_r_done_g"] == 0 and r["_r_running_g"] == 0]

        for sg_type, sg_rows, sg_icon, sg_label in [
            ("scraped", scraped_rows, "🔄", "In Arbeit"),
            ("nodata",  nodata_rows,  "○",  "Ohne Daten"),
        ]:
            if not sg_rows:
                continue
            sg_key     = f"{cont}/{sg_type}"
            # Gescraped: standardmäßig auf; Ohne Daten: standardmäßig zu
            sg_is_open = expanded_sgs.get(sg_key, sg_type == "scraped")
            sg_icon2   = "[−]" if sg_is_open else "[+]"

            sg_sum = _make_subtotal_row(
                f"  {sg_icon2} {sg_icon} {sg_label} ({len(sg_rows)})",
                sg_rows,
                f"subgroup_{sg_type}",
            )
            sg_sum["_continent"] = cont
            sg_sum["_sg_key"]    = sg_key
            table_data.append(sg_sum)

            if not sg_is_open:
                continue

            # Innerhalb der Gruppe: absteigend nach Gruppen-Prozent sortieren
            sorted_rows = sorted(
                sg_rows,
                key=lambda r: r["_r_done_g"] / r["_r_total_g"] if r["_r_total_g"] else 0,
                reverse=True,
            )
            for r in sorted_rows:
                row = dict(r)
                row["_fed"]    = f"      {r['_fed']}"
                row["_sg_key"] = ""
                table_data.append(row)

    return table_data


@app.callback(
    Output("bericht-table", "children"),
    Input("interval-bericht", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def refresh_bericht(_, active_tab):
    if active_tab != "tab-bericht":
        return dash.no_update

    data = _query_bericht_data()

    if not data:
        return html.P("Noch keine Daten vorhanden.",
                      style={"color": "#888", "fontSize": "0.9rem"})

    # ── Alle Tage + geordnete Labels ──────────────────────────────────────
    all_days = sorted({d["day"] for d in data})

    # Residential: alle Slots in _RESIDENTIAL_SLOTS, Canonical-Reihenfolge
    _ordered_res = ["T1", "T2", "T3", "T4", "Pi"]
    # Live aus profiles.yaml statt hartkodiert — sonst fällt ein umbenannter/neuer
    # DC-Thread (z.B. DC-UPDATE → DC-UPDATE-1) stillschweigend aus Tabelle UND
    # Tagessummen raus, obwohl seine scrape_runs-Zeilen weiter mitgezählt werden sollten.
    _dc_thread_cfgs = _get_concurrency_cfg().get("datacenter_threads", [])
    _ordered_dc = [t["label"] for t in _dc_thread_cfgs if "label" in t]
    # DACH-Vollbackfill + alle dc_update-Pool-Threads immer anzeigen (auch ohne bisherige Daten)
    _always_show_dc = {t["label"] for t in _dc_thread_cfgs
                        if t.get("id") == "dc_dach" or str(t.get("id", "")).startswith("dc_update")}

    present_labels = {d["slot_label"] for d in data}
    res_labels = _ordered_res                                # immer T1–T4 anzeigen
    dc_labels  = [l for l in _ordered_dc  if l in present_labels or l in _always_show_dc]

    all_data_labels = res_labels + dc_labels

    # Pivot: {label → {day → mb}}
    pivot: dict[str, dict[str, float]] = {l: {} for l in all_data_labels}
    for d in data:
        if d["slot_label"] in pivot:
            pivot[d["slot_label"]][d["day"]] = d["mb"]

    # ── 3-stufige Spaltenköpfe ─────────────────────────────────────────────
    # Ebene 1: "" | "Details" | "Gesamt"
    # Ebene 2: "" | "Residential" / "Datacenter" | "Residential" / "Datacenter" / "Total"
    # Ebene 3: Spaltenname
    # merge_duplicate_headers=True fasst benachbarte gleiche Einträge je Ebene zusammen.
    DET = "Details"
    GSM = "Gesamt"
    RES = "Residential"
    DC  = "Datacenter"

    table_cols = [{"name": ["", "", "Datum"], "id": "_day"}]

    for l in res_labels:
        table_cols.append({"name": [DET, RES, l], "id": l})
    for l in dc_labels:
        table_cols.append({"name": [DET, DC, l],  "id": l})

    table_cols.append({"name": [GSM, RES, "%"],   "id": "_res_pct"})
    table_cols.append({"name": [GSM, DC,  "%"],   "id": "_dc_pct"})
    table_cols.append({"name": [GSM, RES, "MB"],  "id": "_res_mb"})
    table_cols.append({"name": [GSM, DC,  "MB"],  "id": "_dc_mb"})
    table_cols.append({"name": [GSM, "Total", "MB"], "id": "_total"})

    # ── Zeilen befüllen ────────────────────────────────────────────────────
    table_rows = []
    for day in reversed(all_days):
        row: dict[str, str] = {"_day": _fmt_day_de(day)}
        res_sum = sum(pivot[l].get(day, 0.0) for l in res_labels)
        dc_sum  = sum(pivot[l].get(day, 0.0) for l in dc_labels)
        total   = res_sum + dc_sum

        for l in all_data_labels:
            mb = pivot[l].get(day, 0.0)
            row[l] = f"{mb:.1f}" if mb else "–"

        row["_res_mb"]  = f"{res_sum:.1f}" if res_sum else "–"
        row["_res_pct"] = f"{res_sum / total * 100:.0f} %" if total else "–"
        row["_dc_mb"]   = f"{dc_sum:.1f}"  if dc_sum  else "–"
        row["_dc_pct"]  = f"{dc_sum / total * 100:.0f} %" if total else "–"
        row["_total"]   = f"{total:.1f}"   if total   else "–"
        table_rows.append(row)

    # ── Spalten-Styling ────────────────────────────────────────────────────
    style_cell_cond = [
        # Datum: breit genug für "26 Mai 2026" in einer Zeile
        {"if": {"column_id": "_day"},
         "textAlign": "center", "fontWeight": "600",
         "width": "105px", "minWidth": "105px", "maxWidth": "105px",
         "fontSize": "0.78rem", "color": "#555"},

        # %-Block: Res % | DC % — Trennlinie links am Block-Start
        {"if": {"column_id": "_res_pct"},
         "fontWeight": "500", "color": "#0D47A1",
         "backgroundColor": "#EAF4FF",
         "borderLeft": "3px solid #90CAF9"},
        {"if": {"column_id": "_dc_pct"},
         "fontWeight": "500", "color": "#BF360C",
         "backgroundColor": "#FFF0EA"},

        # MB-Block: Res MB | DC MB — Trennlinie links am Block-Start
        {"if": {"column_id": "_res_mb"},
         "fontWeight": "700", "color": "#0D47A1",
         "backgroundColor": "#DDEEFF",
         "borderLeft": "3px solid #AAAAAA"},
        {"if": {"column_id": "_dc_mb"},
         "fontWeight": "700", "color": "#BF360C",
         "backgroundColor": "#FFE0D0"},

        # Gesamt Total: fett blau, starke Trennlinie links
        {"if": {"column_id": "_total"},
         "fontWeight": "700", "color": "#1565C0",
         "borderLeft": "3px solid #42A5F5"},
    ]

    # Trennlinie zwischen Details-Residential und Details-Datacenter
    if dc_labels:
        style_cell_cond.append(
            {"if": {"column_id": dc_labels[0]},
             "borderLeft": "3px solid #CFD8DC"}
        )
    # Trennlinie am Anfang des Gesamt-Blocks (erste Res-Subtotal-Spalte)
    # wird bereits durch _res_pct borderLeft abgedeckt

    table = dash_table.DataTable(
        data=table_rows,
        columns=table_cols,
        merge_duplicate_headers=True,
        page_size=30,
        page_action="native",
        sort_action="none",
        style_table={"overflowX": "auto"},
        style_header={
            "backgroundColor": "#EEEEEE",
            "fontWeight":      "600",
            "fontSize":        "0.80rem",
            "textAlign":       "center",
            "padding":         "4px 8px",
            "borderBottom":    "1px solid #CCCCCC",
        },
        style_cell={
            "fontSize":   "0.82rem",
            "padding":    "4px 8px",
            "textAlign":  "center",
            "color":      "#333",
            "whiteSpace": "nowrap",
        },
        style_cell_conditional=style_cell_cond,
        style_data_conditional=[
            {"if": {"row_index": "odd"}, "backgroundColor": "#F7F7F7"},
            # Subtotal-Spalten behalten ihre Farbe in ungeraden Zeilen
            *[{"if": {"row_index": "odd", "column_id": cid}, "backgroundColor": bg}
              for cid, bg in [
                  ("_res_pct", "#EAF4FF"), ("_res_mb",  "#DDEEFF"),
                  ("_dc_pct",  "#FFF0EA"), ("_dc_mb",   "#FFE0D0"),
              ]],
        ],
        style_as_list_view=True,
    )

    return html.Div([
        html.H6("Tagesdetails (MB je Thread)",
                className="text-secondary mt-3 mb-2",
                style={"fontSize": "0.85rem", "fontWeight": "600"}),
        html.Div(style={
            "backgroundColor": "#FFFFFF",
            "border": "1px solid #E0E0E0",
            "borderRadius": "6px",
            "padding": "12px 16px",
        }, children=[table]),
    ])


# ===========================================================================
# Callbacks — Tab 7 (Bericht Länder)
# ===========================================================================

@app.callback(
    Output("bericht2-raw-data", "data"),
    Input("interval-bericht2", "n_intervals"),
    Input("main-tabs", "active_tab"),
)
def update_bericht2_raw(_, active_tab):
    """Lädt Rohdaten aus der Queue-DB in den Store (alle 5 Min oder bei Tab-Wechsel)."""
    if active_tab != "tab-bericht2":
        return dash.no_update
    return _query_laender_data()


@app.callback(
    Output("bericht2-datatable", "data"),
    Input("bericht2-raw-data", "data"),
    Input("bericht2-expand-state", "data"),
)
def render_bericht2_table(raw_rows, expand_state):
    """Rendert Tabellenzeilen basierend auf Rohdaten + Expand-State."""
    if not raw_rows:
        return []
    return _b2_build_table_data(raw_rows, expand_state or {})


@app.callback(
    Output("bericht2-expand-state", "data"),
    Output("bericht2-datatable",    "active_cell"),
    Input("bericht2-datatable",     "active_cell"),
    State("bericht2-datatable",     "data"),
    State("bericht2-expand-state",  "data"),
    prevent_initial_call=True,
)
def toggle_bericht2_expand(active_cell, table_data, expand_state):
    """Klappt Kontinent- oder Subgruppen-Zeile auf/zu bei Klick in der Föd.-Spalte."""
    import copy

    if not active_cell or not table_data:
        return dash.no_update, None

    row_idx = active_cell.get("row", -1)

    # Jede Zelle der Zeile löst Toggle aus (nicht nur _fed)
    if row_idx < 0 or row_idx >= len(table_data):
        return dash.no_update, None

    clicked  = table_data[row_idx]
    row_type = clicked.get("_row_type", "")

    state = copy.deepcopy(expand_state) or {"continents": ["Europe"], "subgroups": {}}

    if row_type == "continent":
        cont  = clicked.get("_continent", "")
        if not cont:
            return dash.no_update, None
        conts = state.setdefault("continents", [])
        if cont in conts:
            conts.remove(cont)
        else:
            conts.append(cont)
        return state, None

    if row_type in ("subgroup_scraped", "subgroup_nodata"):
        sg_key = clicked.get("_sg_key", "")
        if not sg_key:
            return dash.no_update, None
        sgs     = state.setdefault("subgroups", {})
        default = sg_key.endswith("/scraped")   # scraped = standard auf, nodata = zu
        sgs[sg_key] = not sgs.get(sg_key, default)
        return state, None

    return dash.no_update, None


# ---------------------------------------------------------------------------
if __name__ == "__main__":
    debug = os.getenv("FLASK_DEBUG", "false").lower() == "true"
    app.run(debug=debug, host="0.0.0.0", port=8050)
