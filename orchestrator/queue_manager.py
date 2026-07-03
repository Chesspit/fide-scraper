"""Queue manager for the scraping orchestrator.

Group selection is strictly priority-ordered (see TIER_WIDTH below): the
worker always claims the pending group with the lowest priority value. The
originally planned fuzzy ordering ("no recognizable scrape pattern") was
officially retired in 2026-07 — pattern obfuscation now comes from timing
jitter (get_wait_time) and the per-DC-thread active_hours windows instead.
"""

import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator.setup_db import DB_PATH, create_db

# Breite des Prioritäts-Fensters für die Gruppen-Auswahl.
#
# ENTSCHEIDUNG 2026-07-03 (Architektur-Review, Punkt #8): bewusst 1 = strikt
# deterministische Abarbeitung nach Priorität. Die Prioritäten werden von den
# Batch-Generatoren lückenlos durchnummeriert, und der P1/P2/P3-Monatsrefresh
# verlangt, dass P1 vollständig vor P2 vor P3 läuft — ein Zufallsfenster würde
# das unterlaufen. Das ursprüngliche Design-Ziel "zufällige, nicht erkennbare
# Reihenfolge" (docs/scraping_orchestrator.md, Aufgabe 2) ist damit offiziell
# aufgegeben; zeitliche Verschleierung leisten Timing-Jitter (get_wait_time)
# und die active_hours/Timezone-Fenster der DC-Threads.
#
# TIER_WIDTH > 1 reaktiviert das gewichtete Zufalls-Sampling innerhalb des
# Fensters (Gewicht = player_count), falls das je wieder gewünscht ist.
TIER_WIDTH = 1

# Auto-Retry: failed-Gruppen mit Rest-Budget werden automatisch wieder
# eingereiht (siehe requeue_failed()). Nach AUTO_RETRY_MAX Fehlversuchen
# bleibt die Gruppe failed und braucht manuelle Prüfung (Dashboard → Queue).
AUTO_RETRY_MAX = 3
AUTO_RETRY_MIN_AGE_HOURS = 2.0  # Mindestabstand zum letzten Versuch


@dataclass
class Group:
    id: int
    federation: str
    continent: str
    year: int
    elo_min: int
    elo_max: int
    player_count: int
    priority: int
    device: str | None = None
    profile: str | None = None          # None = fuzzy selection
    thread_affinity: str | None = None  # None = residential, 'dc_de'/'dc_in'/... = DC-Thread
    update_only: int = 0                # 1 = nur bereits gescrapte Spieler (Update-Batch)


class QueueManager:
    def __init__(self, db_path: Path = DB_PATH):
        self._db_path = db_path
        self._conn: Optional[sqlite3.Connection] = None

    def _connect(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = create_db(self._db_path)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def close(self) -> None:
        if self._conn:
            self._conn.close()
            self._conn = None

    # ------------------------------------------------------------------
    # Group selection
    # ------------------------------------------------------------------

    def get_next_group(
        self,
        device: str | None = None,
        dc_affinity: str | None = None,
    ) -> Optional[Group]:
        """Return the next pending group using weighted random sampling.

        Race-condition-safe via optimistic locking: after fuzzy selection, an
        atomic UPDATE checks that the group is still 'pending'. If another worker
        claimed it first (0 rows updated), we retry transparently up to 5 times.

        Args:
            device: If set, only picks groups assigned to this device OR unassigned
                    (device IS NULL). If None, picks any pending group.
            dc_affinity: If set (e.g. 'dc_in'), only picks groups with that exact
                         thread_affinity. If None (residential/default mode), only
                         picks groups where thread_affinity IS NULL (avoids DC pool).
        """
        for _attempt in range(5):
            group = self._try_claim_next(device, dc_affinity)
            if group is not None:
                return group
            # Another worker claimed our choice — retry immediately
        return None

    def _try_claim_next(self, device: str | None, dc_affinity: str | None) -> Optional[Group]:
        """One attempt: fuzzy-select + atomic claim. Returns None if queue empty
        or if another worker claimed the chosen group between SELECT and UPDATE."""
        conn = self._connect()

        # Thread-Affinitäts-Filter:
        # DC-Thread → nur Gruppen mit passender thread_affinity
        # Residential → nur Gruppen ohne thread_affinity (kein DC-Pool)
        if dc_affinity:
            affinity_filter = "AND thread_affinity = ?"
            affinity_params: tuple = (dc_affinity,)
        else:
            affinity_filter = "AND (thread_affinity IS NULL)"
            affinity_params = ()

        if device:
            device_filter = "AND (device IS NULL OR device = ?)"
            device_params: tuple = (device,)
        else:
            device_filter = ""
            device_params = ()

        all_params = affinity_params + device_params

        row = conn.execute(
            f"SELECT MIN(priority) FROM scrape_groups WHERE status = 'pending' {affinity_filter} {device_filter}",
            all_params,
        ).fetchone()
        if row[0] is None:
            return None  # queue empty

        tier_max = row[0] + TIER_WIDTH

        candidates = conn.execute(
            f"""
            SELECT id, federation, continent, year, elo_min, elo_max,
                   player_count, priority, device, profile, thread_affinity, update_only
            FROM scrape_groups
            WHERE status = 'pending' AND priority <= ?
              {affinity_filter} {device_filter}
            """,
            (tier_max,) + all_params,
        ).fetchall()

        if not candidates:
            return None

        weights = [max(1, r["player_count"]) for r in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]

        # Atomic claim: only succeeds if status is still 'pending'
        cur = conn.execute(
            "UPDATE scrape_groups SET status='running', last_run_at=? WHERE id=? AND status='pending'",
            (_now(), chosen["id"]),
        )
        conn.commit()

        if cur.rowcount == 0:
            return None  # another worker was faster — caller retries

        return Group(**dict(chosen))

    def pending_count(self) -> int:
        return self._connect().execute(
            "SELECT COUNT(*) FROM scrape_groups WHERE status = 'pending'"
        ).fetchone()[0]

    def done_count(self) -> int:
        return self._connect().execute(
            "SELECT COUNT(*) FROM scrape_groups WHERE status = 'done'"
        ).fetchone()[0]

    def stats(self) -> dict:
        rows = self._connect().execute(
            "SELECT status, COUNT(*) AS n FROM scrape_groups GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------
    # Status transitions  (atomic SQLite updates)
    # ------------------------------------------------------------------

    def mark_running(self, group_id: int) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE scrape_groups SET status='running', last_run_at=? WHERE id=?",
            (_now(), group_id),
        )
        conn.commit()

    def mark_done(self, group_id: int, records_found: int) -> None:
        conn = self._connect()
        conn.execute(
            """UPDATE scrape_groups
               SET status='done', records_found=?, last_run_at=?
               WHERE id=?""",
            (records_found, _now(), group_id),
        )
        conn.commit()

    def mark_failed(self, group_id: int, error_msg: str) -> None:
        conn = self._connect()
        conn.execute(
            """UPDATE scrape_groups
               SET status='failed', retries=retries+1,
                   notes=?, last_run_at=?
               WHERE id=?""",
            (error_msg[:500], _now(), group_id),
        )
        conn.commit()

    def reset_to_pending(self, group_id: int) -> None:
        """Re-queue a failed group for retry."""
        conn = self._connect()
        conn.execute(
            "UPDATE scrape_groups SET status='pending' WHERE id=?",
            (group_id,),
        )
        conn.commit()

    def requeue_failed(
        self,
        max_retries: int = AUTO_RETRY_MAX,
        min_age_hours: float = AUTO_RETRY_MIN_AGE_HOURS,
    ) -> int:
        """Auto-Retry: re-queue failed groups that still have retry budget.

        Only groups whose last attempt is at least min_age_hours old are
        touched — a transient outage (proxy provider down, FIDE hiccup) gets
        time to clear instead of burning the whole budget in minutes.
        retries is NOT reset; mark_failed() keeps incrementing it, so after
        max_retries failures the group stays failed until someone looks at it.

        Returns the number of re-queued groups. Idempotent and safe to call
        from multiple threads (single atomic UPDATE).
        """
        conn = self._connect()
        cur = conn.execute(
            """UPDATE scrape_groups
               SET status='pending'
               WHERE status='failed'
                 AND retries < ?
                 AND (last_run_at IS NULL
                      OR last_run_at < datetime('now','localtime', ?))""",
            (max_retries, f"-{min_age_hours} hours"),
        )
        conn.commit()
        return cur.rowcount

    def count_exhausted_failed(self, max_retries: int = AUTO_RETRY_MAX) -> int:
        """Failed groups without retry budget — these need a human."""
        return self._connect().execute(
            "SELECT COUNT(*) FROM scrape_groups WHERE status='failed' AND retries >= ?",
            (max_retries,),
        ).fetchone()[0]

    def skip(self, group_id: int, reason: str = "") -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE scrape_groups SET status='skipped', notes=? WHERE id=?",
            (reason[:500], group_id),
        )
        conn.commit()

    def update_profile(self, group_id: int, profile: str | None) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE scrape_groups SET profile=? WHERE id=?",
            (profile if profile else None, group_id),
        )
        conn.commit()

    def update_thread_affinity(self, group_id: int, thread_affinity: str | None) -> None:
        conn = self._connect()
        conn.execute(
            "UPDATE scrape_groups SET thread_affinity=? WHERE id=?",
            (thread_affinity if thread_affinity else None, group_id),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Run logging
    # ------------------------------------------------------------------

    def log_run(
        self,
        group_id: int,
        started_at: str,
        status: str,
        records_found: int = 0,
        error_msg: str = "",
        proxy_used: str = "",
        profile_used: str = "",
        mb_downloaded: float = 0.0,
        thread_slot: int | None = None,
    ) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO scrape_runs
               (group_id, started_at, finished_at, status,
                records_found, error_msg, proxy_used, profile_used, mb_downloaded, thread_slot)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (group_id, started_at, _now(), status,
             records_found, error_msg[:500], proxy_used, profile_used, mb_downloaded, thread_slot),
        )
        conn.commit()

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    def reset_stale_running(self) -> int:
        """Reset any 'running' groups to 'pending' (called on worker startup)."""
        conn = self._connect()
        cur = conn.execute(
            "UPDATE scrape_groups SET status='pending' WHERE status='running'"
        )
        conn.commit()
        return cur.rowcount

    def get_wait_time(self, profile: dict) -> float:
        """Return a jittered wait time in seconds based on the active profile."""
        base = profile.get("base_wait_seconds", 3.0)
        jitter = profile.get("jitter", 0.4)
        wait = base * (1 + random.uniform(-jitter, jitter))
        minimum = profile.get("min_wait_seconds", 1.0)
        return max(minimum, wait)


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
