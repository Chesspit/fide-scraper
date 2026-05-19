"""Queue manager for the scraping orchestrator.

Selects the next group to scrape using weighted random sampling within the
top priority tier — so the overall direction (newest year + highest ELO first)
is deterministic, but the federation order within that tier is unpredictable.
"""

import random
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from orchestrator.setup_db import DB_PATH, create_db

# How many priority values form one "tier" for fuzzy selection.
# Groups within the same tier are sampled randomly — creates unpredictable
# federation order while maintaining year/ELO direction.
# With sequential priorities (1, 2, 3 …): TIER_WIDTH=50 means the worker
# randomly picks from the top ~50 groups by priority.
TIER_WIDTH = 1


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
    profile: str | None = None  # None = fuzzy selection


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

    def get_next_group(self, device: str | None = None) -> Optional[Group]:
        """Return the next pending group using weighted random sampling.

        Race-condition-safe via optimistic locking: after fuzzy selection, an
        atomic UPDATE checks that the group is still 'pending'. If another worker
        claimed it first (0 rows updated), we retry transparently up to 5 times.

        Args:
            device: If set, only picks groups assigned to this device OR unassigned
                    (device IS NULL). If None, picks any pending group.
        """
        for _attempt in range(5):
            group = self._try_claim_next(device)
            if group is not None:
                return group
            # Another worker claimed our choice — retry immediately
        return None

    def _try_claim_next(self, device: str | None) -> Optional[Group]:
        """One attempt: fuzzy-select + atomic claim. Returns None if queue empty
        or if another worker claimed the chosen group between SELECT and UPDATE."""
        conn = self._connect()

        if device:
            device_filter = "AND (device IS NULL OR device = ?)"
            params_min: tuple = (device,)
        else:
            device_filter = ""
            params_min = ()

        row = conn.execute(
            f"SELECT MIN(priority) FROM scrape_groups WHERE status = 'pending' {device_filter}",
            params_min,
        ).fetchone()
        if row[0] is None:
            return None  # queue empty

        tier_max = row[0] + TIER_WIDTH

        if device:
            candidates = conn.execute(
                """
                SELECT id, federation, continent, year, elo_min, elo_max,
                       player_count, priority, device, profile
                FROM scrape_groups
                WHERE status = 'pending' AND priority <= ?
                  AND (device IS NULL OR device = ?)
                """,
                (tier_max, device),
            ).fetchall()
        else:
            candidates = conn.execute(
                """
                SELECT id, federation, continent, year, elo_min, elo_max,
                       player_count, priority, device, profile
                FROM scrape_groups
                WHERE status = 'pending' AND priority <= ?
                """,
                (tier_max,),
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
    ) -> None:
        conn = self._connect()
        conn.execute(
            """INSERT INTO scrape_runs
               (group_id, started_at, finished_at, status,
                records_found, error_msg, proxy_used, profile_used, mb_downloaded)
               VALUES (?,?,?,?,?,?,?,?,?)""",
            (group_id, started_at, _now(), status,
             records_found, error_msg[:500], proxy_used, profile_used, mb_downloaded),
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
