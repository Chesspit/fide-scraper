"""Gespeicherte Audit-Läufe (Tabelle qc_audit_runs, Migration 021).

Schreiben: scripts/audit_data.py --store (u. a. im Monatslauf).
Lesen: QC-Bereich im Orchestrator-Dashboard (orchestrator/qc_ui.py).

Ein Lauf wird vollständig als JSONB abgelegt: Kopfzahlen, alle Checks mit
Stichprobe/by_year/by_federation/worst_periods, Jahres- und Föderationstabelle.
So lässt sich der Markdown-Bericht jederzeit aus der DB nachbauen, und das
Dashboard kann Trends und Drill-downs zeigen, ohne neu zu rechnen.
"""

from __future__ import annotations

import json
from datetime import date, datetime

from psycopg2.extras import Json, RealDictCursor

from qc.audit import AuditResult, CheckResult

_COLUMNS = (
    "started_at", "duration_s", "source", "since_period", "until_period",
    "last_period", "federations", "layers", "params", "has_hard", "headline",
    "checks", "by_year", "by_federation", "skipped_periods",
)


def _plain(obj):
    """JSON-taugliche Kopie: Counter→dict, date→ISO, Tupel→Liste, Schlüssel→str."""
    return json.loads(json.dumps(obj, default=_default, ensure_ascii=False))


def _default(o):
    if isinstance(o, (date, datetime)):
        return o.isoformat()
    if isinstance(o, set):
        return sorted(o)
    return str(o)


def check_to_dict(c: CheckResult) -> dict:
    return {
        "id": c.id, "layer": c.layer, "title": c.title, "fix_hint": c.fix_hint,
        "severity": c.severity, "n_checked": c.n_checked, "n_findings": c.n_findings,
        "pct": c.pct, "note": c.note, "sample": c.sample,
        "by_year": dict(c.by_year), "by_federation": dict(c.by_federation),
        "worst_periods": [list(t) for t in c.worst_periods],
    }


def result_to_record(res: AuditResult, *, layers: set[int] | list[int],
                     started_at: datetime, duration_s: float,
                     params: dict | None = None, source: str = "manual") -> dict:
    """Spaltenwerte für qc_audit_runs (ohne id/finished_at)."""
    return {
        "started_at": started_at,
        "duration_s": round(duration_s, 1),
        "source": source,
        "since_period": res.since,
        "until_period": res.until,
        "last_period": res.periods[-1] if res.periods else None,
        "federations": res.federations,
        "layers": sorted(layers),
        "params": _plain(params or {}),
        "has_hard": res.has_hard(),
        "headline": _plain(res.headline),
        "checks": _plain([check_to_dict(c) for c in res.checks]),
        "by_year": _plain(res.by_year),
        "by_federation": _plain(res.by_federation),
        "skipped_periods": list(res.skipped_periods),
    }


def store_run(conn, res: AuditResult, **kwargs) -> int:
    """Lauf speichern, id zurück. kwargs wie result_to_record."""
    rec = result_to_record(res, **kwargs)
    values = [Json(rec[c]) if c in ("params", "headline", "checks", "by_year",
                                    "by_federation") else rec[c] for c in _COLUMNS]
    with conn.cursor() as cur:
        cur.execute(
            f"INSERT INTO qc_audit_runs ({', '.join(_COLUMNS)}) "
            f"VALUES ({', '.join(['%s'] * len(_COLUMNS))}) RETURNING id",
            values,
        )
        run_id = cur.fetchone()[0]
    if not conn.autocommit:
        conn.commit()
    return run_id


def list_runs(conn, limit: int = 50) -> list[dict]:
    """Neueste Läufe zuerst, ohne die großen JSONB-Spalten."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, started_at, finished_at, duration_s, source, since_period,
                      until_period, last_period, federations, layers, has_hard, headline
               FROM qc_audit_runs ORDER BY finished_at DESC, id DESC LIMIT %s""",
            (limit,),
        )
        return [dict(r) for r in cur.fetchall()]


def get_run(conn, run_id: int) -> dict | None:
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("SELECT * FROM qc_audit_runs WHERE id = %s", (run_id,))
        row = cur.fetchone()
    return dict(row) if row else None


def trend(conn, limit: int = 60) -> list[dict]:
    """Kopfzahlen der Gesamtläufe (alle Föderationen, alle Ebenen) über die Zeit.

    Nur Läufe ohne Föderationsfilter mit allen vier Ebenen sind vergleichbar;
    ältester zuerst (für Liniendiagramme).
    """
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute(
            """SELECT id, finished_at, since_period, last_period, has_hard, headline
               FROM qc_audit_runs
               WHERE federations IS NULL AND layers @> ARRAY[0,1,2,3]
               ORDER BY finished_at DESC LIMIT %s""",
            (limit,),
        )
        rows = [dict(r) for r in cur.fetchall()]
    return rows[::-1]


def run_to_result(run: dict) -> AuditResult:
    """Gespeicherten Lauf zurück in ein AuditResult (z. B. für den Markdown-Bericht)."""
    from collections import Counter
    checks = []
    for c in run["checks"]:
        checks.append(CheckResult(
            id=c["id"], layer=c["layer"], title=c["title"], fix_hint=c.get("fix_hint", ""),
            severity=c["severity"], n_checked=c["n_checked"], n_findings=c["n_findings"],
            note=c.get("note", ""), sample=c.get("sample", []),
            by_year=Counter({int(k): v for k, v in c.get("by_year", {}).items()}),
            by_federation=Counter(c.get("by_federation", {})),
            worst_periods=[tuple(t) for t in c.get("worst_periods", [])],
        ))
    periods = []
    if run.get("last_period"):
        periods = [run["since_period"], run["last_period"]]
    return AuditResult(
        since=run["since_period"], until=run.get("until_period"), periods=periods,
        federations=run.get("federations"), checks=checks, headline=run["headline"],
        by_year={int(k): v for k, v in run.get("by_year", {}).items()},
        by_federation=run.get("by_federation", {}),
        skipped_periods=list(run.get("skipped_periods") or []),
    )
