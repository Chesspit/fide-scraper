"""Datenzugriff des Dashboards — alle SQLite/PostgreSQL-Queries an einem Ort.

Extrahiert aus app.py (Review #6): app.py behält Layout, Callbacks und
Figures; dieses Modul kapselt jede Query gegen die Orchestrator-SQLite
(scrape_groups/scrape_runs) und die eine PG-Query (Spielerzahlen je
Föderation). Config-abhängige Werte (DC-Thread-Label-Maps, ELO-Grenzen,
Worker-Thread-Livestatus) werden als Parameter hineingereicht statt aus
app-Interna gelesen — dadurch ist die Bucket-/Aggregations-Logik erstmals
mit einer Wegwerf-SQLite testbar (tests/test_store.py).

Für Review #5 (Queue-Migration nach PostgreSQL) ist dieses Modul neben
queue_manager.py die einzige Stelle, die umgestellt werden muss.
"""

import sqlite3
import time
from datetime import datetime

from orchestrator.setup_db import DB_PATH, create_db


def get_conn(db_path=None):
    conn = create_db(db_path or DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def fmt_dt(s: str | None) -> str:
    if not s:
        return "–"
    try:
        return datetime.fromisoformat(s.replace("T", " ")[:16]).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return s


# ---------------------------------------------------------------------------
# Tab 1: Heatmap / Übersicht
# ---------------------------------------------------------------------------

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


def query_overview(fed_to_column: dict[str, str], elo_floor: int, elo_ceiling: int) -> list[dict]:
    """Scraping-Fortschritt pro (Spalte, 50er-ELO-Bucket).

    fed_to_column: Föderationscode → Heatmap-Spalte (Direktspalten mappen auf
    sich selbst, DC-aggregierte Föderationen auf ihr DC-Label; Aufbau siehe
    app.py). Breite ELO-Bänder werden auf alle 50-Punkte-Buckets zwischen
    elo_floor und elo_ceiling aufgeteilt, damit die Heatmap keine Lücken
    zeigt, obwohl ein breiter Bereich von einer einzigen Gruppe abgedeckt wird.
    """
    if not fed_to_column:
        return []

    placeholders = ",".join("?" * len(fed_to_column))
    conn = get_conn()
    rows = conn.execute(
        f"""SELECT federation, elo_min, elo_max, status
            FROM scrape_groups
            WHERE federation IN ({placeholders})""",
        list(fed_to_column),
    ).fetchall()
    conn.close()

    from collections import defaultdict
    bucket_data: dict[tuple, dict] = defaultdict(lambda: {"total": 0, "done": 0})

    for fed, elo_min, elo_max, status in rows:
        target = fed_to_column.get(fed)
        if target is None:
            continue
        lo_bucket = max((elo_min // 50) * 50, elo_floor)
        hi_bucket = (elo_max // 50) * 50
        for bucket in range(lo_bucket, hi_bucket + 50, 50):
            if bucket >= elo_ceiling:   # ≥ ceiling: läuft über P1-Monatsrefresh, nicht Backfill
                continue
            key = (target, bucket)
            bucket_data[key]["total"] += 1
            if status == "done":
                bucket_data[key]["done"] += 1

    return [
        {"federation": fed, "elo_bucket": bkt,
         "total": v["total"], "done_count": v["done"]}
        for (fed, bkt), v in bucket_data.items()
    ]


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
# Tab 2: Queue
# ---------------------------------------------------------------------------

def query_queue(
    affinity_filter: str | None,
    slot_labels: dict[int, str],
    id_labels: dict[str, str],
    worker_threads: list[dict],
) -> list[dict]:
    """Top 500 non-done, non-skipped groups sorted by priority (ascending = highest first).

    affinity_filter:
      None       → alle Gruppen
      'dc'       → nur Gruppen mit thread_affinity IS NOT NULL
      '<dc_id>'  → nur dieser DC-Thread (jeder Key aus id_labels)
      'residential' → thread_affinity IS NULL AND device IS NULL
      'mac_mini' / 'raspi' → device-Filter

    slot_labels/id_labels: DC-Thread-Maps (aus profiles.yaml, siehe app.py).
    worker_threads: live threads-Liste aus worker_state.json für die
    ▶-Laufanzeige.
    """
    conn = get_conn()

    if affinity_filter == "dc":
        where_extra = "AND thread_affinity IS NOT NULL"
    elif affinity_filter in id_labels:
        where_extra = f"AND thread_affinity = '{affinity_filter}'"
    elif affinity_filter == "residential":
        where_extra = "AND thread_affinity IS NULL AND (device IS NULL OR device = '')"
    elif affinity_filter == "mac_mini":
        where_extra = "AND device = 'mac_mini'"
    elif affinity_filter == "raspi":
        where_extra = "AND device = 'raspi'"
    else:
        where_extra = ""

    rows = conn.execute(
        f"""SELECT id, priority, federation, continent, year,
                  elo_min || '–' || elo_max AS elo_band,
                  player_count, status, retries,
                  COALESCE(device, '') AS device,
                  COALESCE(last_run_at, '–') AS last_run_at,
                  COALESCE(thread_affinity, '') AS thread_affinity
           FROM scrape_groups
           WHERE status IN ('pending', 'running', 'failed')
             {where_extra}
           ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END ASC,
                    priority ASC, federation ASC
           LIMIT 500""",
    ).fetchall()
    conn.close()
    result = [dict(r) for r in rows]

    # Thread-Anzeige: live Slot ODER konfigurierte Affinität → eine Spalte
    thread_map = {}  # group_label → "▶ T2" / "▶ DC-IN"
    for t in worker_threads:
        grp = t.get("current_group", "")
        if not grp or grp.startswith("💤"):
            continue
        slot = t.get("slot", 0)
        if slot >= 99:
            label = f"▶ {slot_labels.get(slot, 'DC')}"
        else:
            label = f"▶ T{slot + 1}"
        thread_map[grp] = label

    for row in result:
        row["last_run_at"] = fmt_dt(row.get("last_run_at"))
        grp_label = f"{row['federation']}/{row['year']}/{row['elo_band']}"
        if grp_label in thread_map:
            # Gruppe läuft gerade → live Thread anzeigen
            row["thread_affinity"] = thread_map[grp_label]
        else:
            # Wartend → konfigurierte Affinität als Label
            aff = row.get("thread_affinity", "")
            row["thread_affinity"] = id_labels.get(aff, "") if aff else ""
    return result


def update_group_thread_affinity(group_id: int, affinity: str) -> None:
    conn = get_conn()
    conn.execute(
        "UPDATE scrape_groups SET thread_affinity=? WHERE id=?",
        (affinity if affinity else None, group_id),
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
# Tab 3: Completed
# ---------------------------------------------------------------------------

def query_completed(slot_labels: dict[int, str]) -> list[dict]:
    """Done groups with scraping stats from scrape_runs."""
    conn = get_conn()
    _dc_slot_case = "\n".join(
        f"                           WHEN {slot} THEN '{label}'"
        for slot, label in sorted(slot_labels.items())
    )
    rows = conn.execute(
        f"""SELECT
               g.federation,
               g.continent,
               g.year,
               g.elo_min || '–' || g.elo_max               AS elo_band,
               g.player_count,
               g.records_found,
               r.profile_used,
               CASE
                   WHEN r.thread_slot >= 99 THEN
                       CASE r.thread_slot
{_dc_slot_case}
                           ELSE 'DC'
                       END
                   WHEN r.thread_slot IS NOT NULL THEN 'T' || (r.thread_slot + 1)
                   ELSE '–'
               END                                          AS thread_slot,
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
                      records_found, profile_used, proxy_used, thread_slot,
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
        row["last_run_at"] = fmt_dt(row.get("last_run_at"))
    return result


# ---------------------------------------------------------------------------
# Berichte (MB pro Tag/Thread, Länder-Aggregat, PG-Spielerzahlen)
# ---------------------------------------------------------------------------

def query_bericht_data(slot_label_map: dict[int, str]) -> list[dict]:
    """Tägliches Datenvolumen pro Thread-Slot aus scrape_runs.

    Gibt Liste von Dicts zurück:
        {"day": "2026-05-25", "slot_label": "T0", "mb": 13.52}
    Slots ohne Daten werden nicht zurückgegeben (fehlende Bars = 0).
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            DATE(started_at)   AS day,
            thread_slot,
            ROUND(SUM(mb_downloaded), 2) AS mb
        FROM   scrape_runs
        WHERE  mb_downloaded > 0
          AND  started_at IS NOT NULL
        GROUP  BY day, thread_slot
        ORDER  BY day ASC, thread_slot ASC
        """
    ).fetchall()
    conn.close()
    result = []
    for r in rows:
        slot = r["thread_slot"]
        label = slot_label_map.get(slot, f"Slot-{slot}")
        result.append({"day": r["day"], "slot_label": label, "mb": r["mb"] or 0.0})
    return result


_pg_aktiv_cache: dict[str, tuple] = {}
_pg_aktiv_cache_ts: float = 0.0
_PG_AKTIV_TTL = 300.0   # 5 Min — selten nötig, PG nicht belasten


def query_pg_players() -> dict[str, tuple[int, int]]:
    """Liefert pro Föderation (scraped, active) aus PostgreSQL (gecacht, 5 Min TTL).

    scraped = COUNT(DISTINCT fide_id) aus scrape_periods WHERE status='ok'
    active  = COUNT(*) aus players WHERE active=TRUE

    Liefert {} wenn PG nicht erreichbar — Dashboard läuft weiter mit '—'-Werten.
    """
    global _pg_aktiv_cache, _pg_aktiv_cache_ts
    if time.time() - _pg_aktiv_cache_ts < _PG_AKTIV_TTL:
        return _pg_aktiv_cache
    try:
        from scraper.config import get_database_url
        import psycopg2
        pg  = psycopg2.connect(get_database_url(), connect_timeout=5)
        cur = pg.cursor()

        # 1) Aktive Spieler pro Föd.
        cur.execute("""
            SELECT federation, COUNT(*) AS n
            FROM   players
            WHERE  active = TRUE AND federation IS NOT NULL
            GROUP  BY federation
        """)
        active = {row[0]: row[1] for row in cur.fetchall()}

        # 2) Gescrapte Spieler pro Föd. (mind. 1 erfolgreiche Periode)
        cur.execute("""
            SELECT p.federation, COUNT(DISTINCT sp.fide_id) AS n
            FROM   scrape_periods sp
            JOIN   players p ON p.fide_id = sp.fide_id
            WHERE  sp.status = 'ok' AND p.federation IS NOT NULL
            GROUP  BY p.federation
        """)
        scraped = {row[0]: row[1] for row in cur.fetchall()}

        pg.close()
        all_feds = set(active) | set(scraped)
        result   = {fed: (scraped.get(fed, 0), active.get(fed, 0))
                    for fed in all_feds}
        _pg_aktiv_cache    = result
        _pg_aktiv_cache_ts = time.time()
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("PG players query failed: %s", exc)
        return _pg_aktiv_cache   # stale cache ist besser als nichts


def query_laender_data() -> list[dict]:
    """Aggregiert scrape_groups + scrape_runs nach Föderation (VPS-Sicht).

    Datenquelle: SQLite scrape_groups/scrape_runs des VPS-Orchestrators.
    Mac-Mini-Backfills (global_XX, female) schreiben nur nach PostgreSQL
    und tauchen hier NICHT auf — alle Werte sind rein VPS-basiert.
    """
    conn = get_conn()
    rows = conn.execute(
        """
        SELECT
            sg.federation,
            MAX(sg.continent)                                                     AS continent,
            COUNT(sg.id)                                                          AS total_groups,
            SUM(CASE WHEN sg.status = 'done'    THEN 1 ELSE 0 END)               AS done_groups,
            SUM(CASE WHEN sg.status = 'running' THEN 1 ELSE 0 END)               AS running_groups,
            MIN(sg.year)                                                          AS year_plan_from,
            MAX(sg.year)                                                          AS year_plan_to,
            MIN(CASE WHEN sg.status = 'done'    THEN sg.year ELSE NULL END)      AS year_done_from,
            MAX(CASE WHEN sg.status = 'done'    THEN sg.year ELSE NULL END)      AS year_done_to,
            MIN(CASE WHEN sg.status = 'running' THEN sg.year ELSE NULL END)      AS year_running,
            SUM(CASE WHEN sg.status = 'done'    THEN sg.player_count ELSE 0 END) AS done_players,
            SUM(sg.player_count)                                                  AS total_players,
            COALESCE(SUM(sr.mb_per_group), 0.0)                                  AS total_mb
        FROM scrape_groups sg
        LEFT JOIN (
            SELECT group_id, SUM(mb_downloaded) AS mb_per_group
            FROM   scrape_runs
            WHERE  mb_downloaded > 0
            GROUP  BY group_id
        ) sr ON sr.group_id = sg.id
        GROUP BY sg.federation
        HAVING COUNT(sg.id) > 0
        ORDER BY continent, sg.federation
        """
    ).fetchall()
    conn.close()

    def _yrng(y0, y1):
        if y0 is None: return "—"
        return str(y0) if y0 == y1 else f"{y0} – {y1}"

    pg_aktiv = query_pg_players()   # {federation: (scraped, active)}

    result = []
    for r in rows:
        total_g   = r["total_groups"]   or 1
        done_g    = r["done_groups"]    or 0
        running_g = r["running_groups"] or 0
        mb        = round(r["total_mb"], 1)
        laufend   = str(r["year_running"]) if r["year_running"] is not None else ""
        fed              = r["federation"] or "—"
        scraped, total_a = pg_aktiv.get(fed, (0, 0))

        result.append({
            "_fed":            fed,
            "_gruppen":        f"{done_g} / {total_g}",
            "_gruppen_pct":    f"{round(done_g / total_g * 100, 1)} %" if total_g else "—",
            "_zeitraum_plan":  _yrng(r["year_plan_from"], r["year_plan_to"]),
            "_zeitraum_done":  _yrng(r["year_done_from"], r["year_done_to"]),
            "_laufend":        laufend,
            "_fide_aktiv":     f"{scraped} / {total_a}" if total_a else "—",
            "_fide_aktiv_pct": f"{round(scraped / total_a * 100, 1)} %" if total_a else "—",
            "_mb":             mb,
            "_row_type":       "country",
            # Rohwerte für Summen / Subgruppen-Split (nicht in columns → unsichtbar)
            "_continent":      r["continent"] or "—",
            "_r_done_g":       done_g,
            "_r_running_g":    running_g,
            "_r_total_g":      total_g,
            "_r_fide_scraped": scraped,
            "_r_fide_total":   total_a,
            "_r_yp0":          r["year_plan_from"],
            "_r_yp1":          r["year_plan_to"],
            "_r_yd0":          r["year_done_from"],
            "_r_yd1":          r["year_done_to"],
            "_r_mb":           mb,
        })
    return result
