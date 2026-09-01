"""Queue manager for the scraping orchestrator.

Group selection is strictly priority-ordered (see TIER_WIDTH below): the
worker always claims the pending group with the lowest priority value. The
originally planned fuzzy ordering ("no recognizable scrape pattern") was
officially retired in 2026-07 — pattern obfuscation now comes from timing
jitter (get_wait_time) and the per-DC-thread active_hours windows instead.

Seit Review #5 liegt die Queue in PostgreSQL (Schema "orchestrator" der
fidedb, siehe setup_db.py) statt in der SQLite scraper.db. Die Verbindung
ist Autocommit; jeder Statuswechsel ist ein einzelnes atomares UPDATE, der
Claim bleibt optimistisch (UPDATE ... WHERE status='pending' + rowcount).
Bei Verbindungsabrissen (Tunnel-Drop, PG-Neustart) wird transparent neu
verbunden — Retry-Parameter in setup_db.connect().
"""

import os
import random
import socket
import time
from dataclasses import dataclass
from typing import Optional

import psycopg2
import psycopg2.extras

from orchestrator.setup_db import connect


def get_device_id() -> str:
    """Stabile Identität dieses Workers für claimed_by (Phase B, Multi-Device).

    WORKER_DEVICE_ID explizit setzen, wo der Hostname nicht stabil ist —
    Docker-Container bekommen bei jedem Recreate einen neuen Hash-Hostnamen,
    womit reset_stale_running() die eigenen Leichen nicht mehr fände
    (VPS-Compose setzt deshalb WORKER_DEVICE_ID=vps).
    """
    return os.getenv("WORKER_DEVICE_ID") or socket.gethostname()

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
                                         # 2 = nur NIE gescrapte Spieler (P0-Neuzugangs-Batch)


class QueueManager:
    def __init__(self, dsn: str | None = None, device_id: str | None = None):
        self._dsn = dsn
        self._conn = None
        self._device_id = device_id or get_device_id()

    def _connect(self):
        if self._conn is None or self._conn.closed:
            self._conn = connect(self._dsn)
        return self._conn

    def close(self) -> None:
        if self._conn is not None and not self._conn.closed:
            self._conn.close()
        self._conn = None

    def _execute(self, sql: str, params: tuple = ()):
        """Execute mit einmaligem Reconnect bei abgerissener Verbindung.

        Autocommit + Einzelstatements: nach einem Abriss geht kein
        Transaktionskontext verloren, Wiederholen ist gefahrlos (Status-
        UPDATEs sind idempotent, der Claim prüft ohnehin per rowcount).
        """
        for attempt in (1, 2):
            try:
                conn = self._connect()
                cur = conn.cursor(cursor_factory=psycopg2.extras.RealDictCursor)
                cur.execute(sql, params)
                return cur
            except (psycopg2.OperationalError, psycopg2.InterfaceError):
                self.close()
                if attempt == 2:
                    raise

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
            device: If set (e.g. 'raspi'), only picks groups assigned to exactly
                    this device. If None, only picks unassigned groups (device IS
                    NULL). Exklusiv in beide Richtungen (Phase B): auf der geteilten
                    PG-Queue überlappen sich die Prioritätsbänder der Geräte-Pools —
                    das frühere "OR device IS NULL" hätte den Pi die Residential-
                    Queue des VPS claimen lassen (und umgekehrt).
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

        # Thread-Affinitäts-Filter:
        # DC-Thread → nur Gruppen mit passender thread_affinity
        # Residential → nur Gruppen ohne thread_affinity (kein DC-Pool)
        if dc_affinity:
            affinity_filter = "AND thread_affinity = %s"
            affinity_params: tuple = (dc_affinity,)
        else:
            affinity_filter = "AND (thread_affinity IS NULL)"
            affinity_params = ()

        if device:
            device_filter = "AND device = %s"
            device_params: tuple = (device,)
        else:
            device_filter = "AND device IS NULL"
            device_params = ()

        all_params = affinity_params + device_params

        cur = self._execute(
            f"SELECT MIN(priority) AS p FROM scrape_groups WHERE status = 'pending' {affinity_filter} {device_filter}",
            all_params,
        )
        row = cur.fetchone()
        if row["p"] is None:
            return None  # queue empty

        tier_max = row["p"] + TIER_WIDTH

        candidates = self._execute(
            f"""
            SELECT id, federation, continent, year, elo_min, elo_max,
                   player_count, priority, device, profile, thread_affinity, update_only
            FROM scrape_groups
            WHERE status = 'pending' AND priority <= %s
              {affinity_filter} {device_filter}
            """,
            (tier_max,) + all_params,
        ).fetchall()

        if not candidates:
            return None

        weights = [max(1, r["player_count"]) for r in candidates]
        chosen = random.choices(candidates, weights=weights, k=1)[0]

        # Atomic claim: only succeeds if status is still 'pending'.
        # claimed_by = dieses Gerät — reset_stale_running() räumt nur eigene
        # Claims weg, damit mehrere Geräte dieselbe Queue teilen können.
        cur = self._execute(
            """UPDATE scrape_groups
               SET status='running', last_run_at=%s, claimed_by=%s
               WHERE id=%s AND status='pending'""",
            (_now(), self._device_id, chosen["id"]),
        )

        if cur.rowcount == 0:
            return None  # another worker was faster — caller retries

        return Group(**dict(chosen))

    def pending_count(self) -> int:
        return self._execute(
            "SELECT COUNT(*) AS n FROM scrape_groups WHERE status = 'pending'"
        ).fetchone()["n"]

    def done_count(self) -> int:
        return self._execute(
            "SELECT COUNT(*) AS n FROM scrape_groups WHERE status = 'done'"
        ).fetchone()["n"]

    def stats(self) -> dict:
        rows = self._execute(
            "SELECT status, COUNT(*) AS n FROM scrape_groups GROUP BY status"
        ).fetchall()
        return {r["status"]: r["n"] for r in rows}

    # ------------------------------------------------------------------
    # Status transitions  (atomare Einzel-UPDATEs, Autocommit)
    # ------------------------------------------------------------------

    def mark_running(self, group_id: int) -> None:
        self._execute(
            "UPDATE scrape_groups SET status='running', last_run_at=%s WHERE id=%s",
            (_now(), group_id),
        )

    def mark_done(self, group_id: int, records_found: int) -> None:
        self._execute(
            """UPDATE scrape_groups
               SET status='done', records_found=%s, last_run_at=%s
               WHERE id=%s""",
            (records_found, _now(), group_id),
        )

    def mark_failed(self, group_id: int, error_msg: str) -> None:
        self._execute(
            """UPDATE scrape_groups
               SET status='failed', retries=retries+1,
                   notes=%s, last_run_at=%s
               WHERE id=%s""",
            (error_msg[:500], _now(), group_id),
        )

    def reset_to_pending(self, group_id: int) -> None:
        """Re-queue a failed group for retry."""
        self._execute(
            "UPDATE scrape_groups SET status='pending' WHERE id=%s",
            (group_id,),
        )

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
        cur = self._execute(
            """UPDATE scrape_groups
               SET status='pending'
               WHERE status='failed'
                 AND retries < %s
                 AND (last_run_at IS NULL
                      OR last_run_at < localtimestamp - %s * interval '1 hour')""",
            (max_retries, min_age_hours),
        )
        return cur.rowcount

    def count_exhausted_failed(self, max_retries: int = AUTO_RETRY_MAX) -> int:
        """Failed groups without retry budget — these need a human."""
        return self._execute(
            "SELECT COUNT(*) AS n FROM scrape_groups WHERE status='failed' AND retries >= %s",
            (max_retries,),
        ).fetchone()["n"]

    def skip(self, group_id: int, reason: str = "") -> None:
        self._execute(
            "UPDATE scrape_groups SET status='skipped', notes=%s WHERE id=%s",
            (reason[:500], group_id),
        )

    def update_profile(self, group_id: int, profile: str | None) -> None:
        self._execute(
            "UPDATE scrape_groups SET profile=%s WHERE id=%s",
            (profile if profile else None, group_id),
        )

    def update_thread_affinity(self, group_id: int, thread_affinity: str | None) -> None:
        self._execute(
            "UPDATE scrape_groups SET thread_affinity=%s WHERE id=%s",
            (thread_affinity if thread_affinity else None, group_id),
        )

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
        self._execute(
            """INSERT INTO scrape_runs
               (group_id, started_at, finished_at, status,
                records_found, error_msg, proxy_used, profile_used, mb_downloaded, thread_slot)
               VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
            (group_id, started_at, _now(), status,
             records_found, error_msg[:500], proxy_used, profile_used, mb_downloaded, thread_slot),
        )

    # ------------------------------------------------------------------
    # Timing
    # ------------------------------------------------------------------

    def reset_stale_running(self) -> int:
        """Reset own stale 'running' groups to 'pending' (called on worker startup).

        Geräte-Scope (Phase B): jedes Gerät räumt nur die Gruppen weg, die es
        selbst geclaimt hat — mehrere Worker (VPS, Pi, Mac Mini) können damit
        dieselbe Queue teilen, ohne sich beim Start gegenseitig die laufenden
        Gruppen zurückzusetzen. claimed_by IS NULL wird mit abgeräumt: solche
        Zeilen stammen aus Claims von vor dem Phase-B-Patch (Übergang) — nach
        dem ersten Neustart aller Worker existieren sie nicht mehr.
        """
        cur = self._execute(
            """UPDATE scrape_groups SET status='pending'
               WHERE status='running'
                 AND (claimed_by = %s OR claimed_by IS NULL)""",
            (self._device_id,),
        )
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
