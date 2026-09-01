"""Datenzugriff des Dashboards — alle Queue- und PG-Queries an einem Ort.

Extrahiert aus app.py (Review #6): app.py behält Layout, Callbacks und
Figures; dieses Modul kapselt jede Query gegen die Orchestrator-Queue
(scrape_groups/scrape_runs, seit Review #5 im Schema "orchestrator" der
fidedb statt in der SQLite scraper.db) und die PG-Query auf Spielerzahlen
je Föderation. Config-abhängige Werte (DC-Thread-Label-Maps, ELO-Grenzen,
Worker-Thread-Livestatus) werden als Parameter hineingereicht statt aus
app-Interna gelesen — dadurch ist die Bucket-/Aggregations-Logik mit einer
Wegwerf-PG-Testdatenbank testbar (tests/test_store.py).

Verbindung: pro Aufruf eine frische Autocommit-Verbindung (setup_db.connect,
search_path=orchestrator,public). Dashboard-Callbacks dürfen bei DB-Ausfall
nicht minutenlang hängen, daher retries=1.
"""

import time
from datetime import datetime

import psycopg2.extras

from orchestrator.setup_db import connect


def get_conn(dsn=None):
    return connect(dsn, retries=1)


def _fetch_dicts(sql: str, params: tuple = ()) -> list[dict]:
    conn = get_conn()
    try:
        with conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor) as cur:
            cur.execute(sql, params)
            return [dict(r) for r in cur.fetchall()]
    finally:
        conn.close()


def _fetch_rows(sql: str, params: tuple = ()) -> list[tuple]:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()
    finally:
        conn.close()


def _execute(sql: str, params: tuple = ()) -> None:
    conn = get_conn()
    try:
        with conn.cursor() as cur:
            cur.execute(sql, params)
    finally:
        conn.close()


def fmt_dt(value) -> str:
    """ISO-String, datetime oder None → 'DD.MM.YYYY HH:MM' (PG liefert datetime)."""
    if not value:
        return "–"
    if isinstance(value, datetime):
        return value.strftime("%d.%m.%Y %H:%M")
    try:
        return datetime.fromisoformat(str(value).replace("T", " ")[:16]).strftime("%d.%m.%Y %H:%M")
    except Exception:
        return str(value)


# ---------------------------------------------------------------------------
# Tab 1: Heatmap / Übersicht
# ---------------------------------------------------------------------------

def query_continents() -> list[str]:
    rows = _fetch_rows(
        "SELECT DISTINCT continent FROM scrape_groups ORDER BY continent"
    )
    return [r[0] for r in rows]


def query_federations(continent: str) -> list[str]:
    rows = _fetch_rows(
        "SELECT DISTINCT federation FROM scrape_groups WHERE continent=%s ORDER BY federation",
        (continent,),
    )
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

    rows = _fetch_rows(
        """SELECT federation, elo_min, elo_max, status
           FROM scrape_groups
           WHERE federation = ANY(%s)""",
        (list(fed_to_column),),
    )

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


def _iso(value) -> str | None:
    """PG-datetime → ISO-String wie ihn SQLite lieferte (Anzeige + JSON-sicher)."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%dT%H:%M:%S")
    return value


def query_grid(federation: str) -> list[dict]:
    rows = _fetch_dicts(
        """SELECT year, elo_min, elo_max, status, player_count,
                  records_found, retries, last_run_at, id
           FROM scrape_groups
           WHERE federation = %s
           ORDER BY elo_min DESC, year""",
        (federation,),
    )
    for r in rows:
        r["last_run_at"] = _iso(r["last_run_at"])
    return rows


def query_global_stats() -> dict:
    rows = _fetch_rows(
        "SELECT status, COUNT(*) FROM scrape_groups GROUP BY status"
    )
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
    rows = _fetch_dicts(
        "SELECT * FROM scrape_groups WHERE id=%s", (group_id,)
    )
    if not rows:
        return None
    rows[0]["last_run_at"] = _iso(rows[0]["last_run_at"])
    return rows[0]


def update_group_status(group_id: int, new_status: str) -> None:
    _execute(
        "UPDATE scrape_groups SET status=%s WHERE id=%s", (new_status, group_id)
    )


def update_group_priority(group_id: int, new_priority: int) -> None:
    _execute(
        "UPDATE scrape_groups SET priority=%s WHERE id=%s", (new_priority, group_id)
    )


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
    where_params: tuple = ()
    if affinity_filter == "dc":
        where_extra = "AND thread_affinity IS NOT NULL"
    elif affinity_filter in id_labels:
        where_extra = "AND thread_affinity = %s"
        where_params = (affinity_filter,)
    elif affinity_filter == "residential":
        where_extra = "AND thread_affinity IS NULL AND (device IS NULL OR device = '')"
    elif affinity_filter == "mac_mini":
        where_extra = "AND device = 'mac_mini'"
    elif affinity_filter == "raspi":
        where_extra = "AND device = 'raspi'"
    else:
        where_extra = ""

    result = _fetch_dicts(
        f"""SELECT id, priority, federation, continent, year,
                  elo_min::text || '–' || elo_max::text AS elo_band,
                  player_count, status, retries,
                  COALESCE(device, '') AS device,
                  last_run_at,
                  COALESCE(thread_affinity, '') AS thread_affinity
           FROM scrape_groups
           WHERE status IN ('pending', 'running', 'failed')
             {where_extra}
           ORDER BY CASE WHEN status='running' THEN 0 ELSE 1 END ASC,
                    priority ASC, federation ASC
           LIMIT 500""",
        where_params,
    )

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
    _execute(
        "UPDATE scrape_groups SET thread_affinity=%s WHERE id=%s",
        (affinity if affinity else None, group_id),
    )


def update_group_device(group_id: int, device: str) -> None:
    _execute(
        "UPDATE scrape_groups SET device=%s WHERE id=%s",
        (device if device else None, group_id),
    )


# ---------------------------------------------------------------------------
# Tab 3: Completed
# ---------------------------------------------------------------------------

def query_completed(slot_labels: dict[int, str]) -> list[dict]:
    """Done groups with scraping stats from scrape_runs."""
    _dc_slot_case = "\n".join(
        f"                           WHEN {slot} THEN '{label}'"
        for slot, label in sorted(slot_labels.items())
    )
    result = _fetch_dicts(
        f"""SELECT
               g.federation,
               g.continent,
               g.year,
               g.elo_min::text || '–' || g.elo_max::text    AS elo_band,
               g.player_count,
               g.records_found,
               r.profile_used,
               CASE
                   WHEN r.thread_slot >= 99 THEN
                       CASE r.thread_slot
{_dc_slot_case}
                           ELSE 'DC'
                       END
                   WHEN r.thread_slot IS NOT NULL THEN 'T' || (r.thread_slot + 1)::text
                   ELSE '–'
               END                                          AS thread_slot,
               CASE
                   WHEN g.player_count > 0 AND g.records_found > 0
                   THEN ROUND(g.records_found::numeric / g.player_count, 1)::float
                   ELSE NULL
               END                                          AS partien_per_spieler,
               ROUND(COALESCE(r.mb_downloaded, 0)::numeric, 1)::float AS mb,
               g.last_run_at,
               ROUND(
                   (EXTRACT(EPOCH FROM (r.finished_at - r.started_at)) / 3600.0)::numeric, 2
               )::float                                     AS duration_h,
               CASE
                   WHEN r.finished_at IS NOT NULL
                        AND r.started_at IS NOT NULL
                        AND r.finished_at > r.started_at
                   THEN ROUND(
                       (r.records_found /
                        (EXTRACT(EPOCH FROM (r.finished_at - r.started_at)) / 3600.0))::numeric,
                       0)::float
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
    )
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
    rows = _fetch_dicts(
        """
        SELECT
            started_at::date   AS day,
            thread_slot,
            ROUND(SUM(mb_downloaded)::numeric, 2)::float AS mb
        FROM   scrape_runs
        WHERE  mb_downloaded > 0
          AND  started_at IS NOT NULL
        GROUP  BY day, thread_slot
        ORDER  BY day ASC, thread_slot ASC
        """
    )
    result = []
    for r in rows:
        slot = r["thread_slot"]
        label = slot_label_map.get(slot, f"Slot-{slot}")
        result.append({"day": r["day"].isoformat(), "slot_label": label, "mb": r["mb"] or 0.0})
    return result


_pg_aktiv_cache: dict[str, tuple] = {}
_pg_aktiv_cache_ts: float = 0.0
_PG_AKTIV_TTL = 300.0   # 5 Min — selten nötig, PG nicht belasten


def query_pg_players() -> dict[str, tuple[int, int, int]]:
    """Liefert pro Föderation (scraped, std_active, std_inactive) aus PostgreSQL
    (gecacht, 5 Min TTL).

    scraped     = COUNT(DISTINCT fide_id) aus scrape_periods WHERE status='ok'
    std_active/
    std_inactive = COUNT(*) aus rating_history für die zuletzt importierte
                      FIDE-Standardliste (published_rating IS NOT NULL), gesplittet
                      nach players.active — das ist unsere tatsächliche Scraping-
                      Zielpopulation (~566k Stand 09/2026, davon ein Teil laut
                      FIDE's eigenem i/wi-Flag inaktiv, siehe Diskussion 2026-09-01
                      zum std_rating=0-Bug: dieselbe Unterscheidung ist auch hier
                      relevant — ein am 18.04. als 'i' geflaggter Spieler zählt zur
                      Standardliste, ist aber kein sinnvolles Scraping-Ziel).
                      std_active ist der Vergleichsnenner für "% gescraped"
                      (NICHT std_active+std_inactive, und schon gar nicht
                      players.active global (~1,5 Mio., FIDE-Gesamtmitgliedschaft,
                      siehe vorherige Diskussion) — beide wären hier irreführend).

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

        # 1) Aktuelle Std-Ratingliste pro Föd., gesplittet nach players.active.
        #    Braucht idx_rating_history_period_pubrating (Migration 015) — ohne
        #    Index Full-Table-Scan (gemessen 12–75s), mit Index <1s.
        cur.execute("""
            SELECT p.federation, p.active, COUNT(*) AS n
            FROM   rating_history rh
            JOIN   players p ON p.fide_id = rh.fide_id
            WHERE  rh.period = (SELECT MAX(period) FROM rating_history
                                 WHERE published_rating IS NOT NULL)
              AND  rh.published_rating IS NOT NULL
              AND  p.federation IS NOT NULL
            GROUP  BY p.federation, p.active
        """)
        std_active   = {}
        std_inactive = {}
        for fed, active, n in cur.fetchall():
            (std_active if active else std_inactive)[fed] = n

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
        all_feds = set(std_active) | set(std_inactive) | set(scraped)
        result   = {fed: (scraped.get(fed, 0), std_active.get(fed, 0), std_inactive.get(fed, 0))
                    for fed in all_feds}
        _pg_aktiv_cache    = result
        _pg_aktiv_cache_ts = time.time()
        return result
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("PG players query failed: %s", exc)
        return _pg_aktiv_cache   # stale cache ist besser als nichts


def query_federation_years(feds: list[str]) -> list[dict]:
    """Gruppen-Fortschritt pro Jahr für eine oder mehrere Föderationen.

    Nimmt eine Liste, damit UK auf der Karte aggregiert abfragbar ist
    (GBR = ENG+SCO+WLS+NIR). Fürs Detail-Panel des Karten-Tabs.
    """
    return _fetch_dicts(
        """
        SELECT year,
               COUNT(*)                                        AS total,
               COUNT(*) FILTER (WHERE status = 'done')         AS done,
               COUNT(*) FILTER (WHERE status = 'running')      AS running
        FROM   scrape_groups
        WHERE  federation = ANY(%s)
          AND  status <> 'skipped'
        GROUP  BY year
        ORDER  BY year
        """,
        (feds,),
    )


def query_laender_data() -> list[dict]:
    """Aggregiert scrape_groups + scrape_runs nach Föderation (VPS-Sicht).

    Datenquelle: scrape_groups/scrape_runs des VPS-Orchestrators.
    Mac-Mini-Backfills (global_XX, female) schreiben nur nach PostgreSQL
    (public-Schema) und tauchen hier NICHT auf — alle Werte sind rein
    VPS-basiert.
    """
    rows = _fetch_dicts(
        """
        SELECT
            sg.federation,
            MAX(sg.continent)                                                     AS continent,
            -- Plan-Kennzahlen ohne 'skipped': seit den Jahreszielen (2026-07,
            -- set_backfill_targets.py) sind Alt-Jahre bewusst stillgelegt und
            -- zählen weder zum Plan-Zeitraum noch zum Fortschritts-Nenner.
            COUNT(sg.id)      FILTER (WHERE sg.status <> 'skipped')              AS total_groups,
            SUM(CASE WHEN sg.status = 'done'    THEN 1 ELSE 0 END)               AS done_groups,
            SUM(CASE WHEN sg.status = 'running' THEN 1 ELSE 0 END)               AS running_groups,
            MIN(sg.year)      FILTER (WHERE sg.status <> 'skipped')              AS year_plan_from,
            MAX(sg.year)      FILTER (WHERE sg.status <> 'skipped')              AS year_plan_to,
            MIN(CASE WHEN sg.status = 'done'    THEN sg.year ELSE NULL END)      AS year_done_from,
            MAX(CASE WHEN sg.status = 'done'    THEN sg.year ELSE NULL END)      AS year_done_to,
            MIN(CASE WHEN sg.status = 'running' THEN sg.year ELSE NULL END)      AS year_running,
            SUM(CASE WHEN sg.status = 'done'    THEN sg.player_count ELSE 0 END) AS done_players,
            SUM(sg.player_count) FILTER (WHERE sg.status <> 'skipped')           AS total_players,
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
        ORDER BY MAX(sg.continent), sg.federation
        """
    )

    def _yrng(y0, y1):
        if y0 is None: return "—"
        return str(y0) if y0 == y1 else f"{y0} – {y1}"

    pg_aktiv = query_pg_players()   # {federation: (scraped, std_active, std_inactive)}

    result = []
    for r in rows:
        total_g   = r["total_groups"]   or 1
        done_g    = r["done_groups"]    or 0
        running_g = r["running_groups"] or 0
        mb        = round(float(r["total_mb"]), 1)
        laufend   = str(r["year_running"]) if r["year_running"] is not None else ""
        fed                       = r["federation"] or "—"
        scraped, total_a, inaktiv = pg_aktiv.get(fed, (0, 0, 0))

        result.append({
            "_fed":            fed,
            "_gruppen":        f"{done_g} / {total_g}",
            "_gruppen_pct":    f"{round(done_g / total_g * 100, 1)} %" if total_g else "—",
            "_zeitraum_plan":  _yrng(r["year_plan_from"], r["year_plan_to"]),
            "_zeitraum_done":  _yrng(r["year_done_from"], r["year_done_to"]),
            "_laufend":        laufend,
            # Nenner ist std_active (aktuelle Standardliste, nur FIDE-aktiv
            # geflaggte Spieler) — nicht std_active+std_inaktiv und nicht
            # players.active global (~1,5 Mio.), siehe query_pg_players()-Docstring.
            "_fide_aktiv":     f"{scraped} / {total_a}" if total_a else "—",
            # gedeckelt bei 100 %: "gescraped" zählt jemals erfolgreich gescrapte
            # Spieler (Lifetime), "aktiv" ist der aktuelle Stand — wer zwischenzeitlich
            # von aktiv auf inaktiv gewechselt ist, bliebe sonst >100 % (z.B. FIN).
            "_fide_aktiv_pct": f"{min(100.0, round(scraped / total_a * 100, 1))} %" if total_a else "—",
            "_inaktiv":        f"{inaktiv:,}".replace(",", "."),
            "_mb":             mb,
            "_row_type":       "country",
            # Rohwerte für Summen / Subgruppen-Split (nicht in columns → unsichtbar)
            "_continent":      r["continent"] or "—",
            "_r_done_g":       done_g,
            "_r_running_g":    running_g,
            "_r_total_g":      total_g,
            "_r_fide_scraped": scraped,
            "_r_fide_total":   total_a,
            "_r_inaktiv":      inaktiv,
            "_r_yp0":          r["year_plan_from"],
            "_r_yp1":          r["year_plan_to"],
            "_r_yd0":          r["year_done_from"],
            "_r_yd1":          r["year_done_to"],
            "_r_mb":           mb,
        })
    return result
