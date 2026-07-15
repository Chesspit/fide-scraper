"""Scraping orchestrator worker.

Reads the queue from PostgreSQL (Schema "orchestrator", seit Review #5),
fetches FIDE data via a rotating proxy (see orchestrator/proxy_manager.py),
and writes results to the same PostgreSQL database using the scraper's
parser and DB modules.

Run:
    python orchestrator/worker.py [--profile conservative|normal|aggressive]

Control (from dashboard or terminal):
    worker_state.json  {"command": "run"} | {"command": "pause"} | {"command": "stopped"}

Parallel mode (configured via profiles.yaml [concurrency]):
    max_workers > 1 spawns N threads, each claiming its own group from the queue.
    Each thread runs independently with its own PostgreSQL connections
    (Spieldaten + Queue).
"""

import logging
import os
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator import runtime_settings, state_io
from orchestrator.state_io import (
    WORKER_STATE_PATH,
    read_command,
    read_worker_state,
    write_state,
)
from orchestrator.monthly_refresh_tiers import TIER_FILTERS
from orchestrator.profile_manager import ProfileManager, PROFILES_PATH
from orchestrator.proxy_manager import ProxyManager
from orchestrator.queue_manager import AUTO_RETRY_MAX, Group, QueueManager, get_device_id
from scraper.db import (
    ensure_connection,
    get_connection,
    is_valid_fide_period,
    save_period,
    save_period_no_data,
)
from scraper.fetcher import AJAX_URL, HEADERS, REFERER_URL, BlockedError
from scraper.parser import parse_calculations

logger = logging.getLogger("orchestrator.worker")

_PAUSE_POLL_INTERVAL = 5      # seconds between pause-state polls
_EMPTY_QUEUE_SLEEP = 120      # seconds to wait when queue is empty (single-thread)
_THREAD_EMPTY_SLEEP = 30      # seconds to wait when queue is empty (per thread)
_CIRCUIT_BREAKER_THRESHOLD = 15  # consecutive double-failures before aborting group
_DC_SLEEP_CHECK_INTERVAL = 600  # seconds between timezone re-checks when DC thread sleeps


# ---------------------------------------------------------------------------
# DC thread timezone helpers
# ---------------------------------------------------------------------------

def _dc_is_active(dc_cfg: dict) -> bool:
    """Return True if current local time is within the DC thread's active_hours window."""
    try:
        tz = ZoneInfo(dc_cfg["timezone"])
        h = datetime.now(tz).hour
        h_start, h_end = dc_cfg.get("active_hours", [7, 23])
        return h_start <= h < h_end
    except Exception:
        return True  # Fehler → immer aktiv (fail-safe)


def _dc_seconds_until_active(dc_cfg: dict) -> float:
    """Seconds until the DC thread's next active window starts."""
    try:
        tz = ZoneInfo(dc_cfg["timezone"])
        now = datetime.now(tz)
        h_start = dc_cfg.get("active_hours", [7, 23])[0]
        target = now.replace(hour=h_start, minute=0, second=0, microsecond=0)
        if now.hour >= h_start:
            target += timedelta(days=1)
        return max(60.0, (target - now).total_seconds())
    except Exception:
        return 3600.0

# Rate-Limit für die "failed ohne Retry-Budget"-Warnung (einmal pro Stunde,
# egal wie viele Threads im Leerlauf darüber stolpern)
_EXHAUSTED_WARN_INTERVAL = 3600
_exhausted_warn_lock = threading.Lock()
_exhausted_warn_last = 0.0


def _idle_queue_maintenance(qm: QueueManager, log_prefix: str) -> int:
    """Auto-Retry im Leerlauf: failed-Gruppen mit Retry-Budget wieder einreihen.

    Wird von allen Thread-Loops aufgerufen, wenn die Queue leer ist — genau der
    Moment, in dem liegengebliebene failed-Gruppen die einzige offene Arbeit
    sind. Warnt (stündlich gedrosselt) über Gruppen ohne Retry-Budget, damit
    ein Provider-Ausfall nicht tagelang unbemerkt bleibt.

    Returns number of re-queued groups (caller should re-claim immediately).
    """
    global _exhausted_warn_last
    requeued = qm.requeue_failed()
    if requeued:
        logger.info("%s: Auto-Retry — %d failed-Gruppen zurück auf pending", log_prefix, requeued)

    with _exhausted_warn_lock:
        due = time.time() - _exhausted_warn_last >= _EXHAUSTED_WARN_INTERVAL
        if due:
            _exhausted_warn_last = time.time()
    if due:
        exhausted = qm.count_exhausted_failed()
        if exhausted:
            logger.warning(
                "%d failed-Gruppen ohne Retry-Budget (retries >= %d) — "
                "manuelle Prüfung nötig (Dashboard → Queue → failed)",
                exhausted, AUTO_RETRY_MAX,
            )
    return requeued


# ---------------------------------------------------------------------------
# State file helpers — seit Review #6 kanonisch in orchestrator/state_io.py
# (eine Implementierung für Worker + Dashboard; Aliase erhalten die alten
# modul-internen Namen an den ~30 Aufrufstellen unten)
# ---------------------------------------------------------------------------
_update_thread_slot = state_io.update_thread_slot
_clear_thread_slot = state_io.clear_thread_slot
_increment_global_stats = state_io.increment_global_stats


def _load_concurrency_config() -> dict:
    """[concurrency]-Sicht: profiles.yaml-Topologie + Runtime-Overrides."""
    return runtime_settings.effective_concurrency()


def _read_dc_thread_enabled(dc_id: str) -> bool:
    """Liest enabled-Flag eines DC-Threads live (Runtime-Override vor YAML).

    Wird zwischen Gruppen geprüft — so greift der UI-Toggle sofort nach
    Abschluss der aktuellen Gruppe, ohne einen Neustart zu benötigen.
    Gibt True zurück im Fehlerfall (safe default: weiterlaufen).
    """
    try:
        return runtime_settings.dc_thread_enabled(dc_id)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Player lookup
# ---------------------------------------------------------------------------

def get_fide_ids(pg_conn, federation: str, elo_min: int, elo_max: int,
                 update_only: bool = False) -> list[int]:
    """Return active player fide_ids matching the federation and ELO range.

    update_only=True restricts to players already scraped at least once
    (status='ok' in scrape_periods) — used for monthly Update-Batches, where
    rating drift must never pull in a never-scraped player (would trigger an
    unwanted full historical backfill instead of a quick monthly refresh).

    federation may also be a monthly-refresh tier sentinel ('P1'/'P2'/'P3',
    see orchestrator/monthly_refresh_tiers.py) — in that case the query pools
    across ALL federations using the tier's population filter instead of an
    exact federation match. Tier groups always imply update_only semantics.
    """
    scraped_filter = (
        "AND EXISTS (SELECT 1 FROM scrape_periods sp "
        "WHERE sp.fide_id = players.fide_id AND sp.status = 'ok')"
        if update_only else ""
    )
    with pg_conn.cursor() as cur:
        if federation in TIER_FILTERS:
            query = f"""
                SELECT fide_id FROM players
                WHERE active = TRUE
                  AND {TIER_FILTERS[federation]}
                  AND std_rating BETWEEN %s AND %s
                  {scraped_filter}
                ORDER BY fide_id
                """
            params = (elo_min, elo_max)
        else:
            query = f"""
                SELECT fide_id FROM players
                WHERE active = TRUE
                  AND federation = %s
                  AND std_rating BETWEEN %s AND %s
                  {scraped_filter}
                ORDER BY fide_id
                """
            params = (federation, elo_min, elo_max)
        cur.execute(query, params)
        return [row[0] for row in cur.fetchall()]


# ---------------------------------------------------------------------------
# Period helpers
# ---------------------------------------------------------------------------

def valid_periods_for_year(year: int) -> list[str]:
    """Return valid FIDE period strings for the given year, capped at last month."""
    today = date.today()
    # Cap at the first day of the previous month — current month has no FIDE data yet
    if today.month == 1:
        cutoff = date(today.year - 1, 12, 1)
    else:
        cutoff = date(today.year, today.month - 1, 1)

    periods = []
    for month in range(1, 13):
        d = date(year, month, 1)
        if d > cutoff:
            break
        if is_valid_fide_period(d):
            periods.append(d.isoformat())
    return periods


# ---------------------------------------------------------------------------
# HTTP fetch with proxy and profile-based retry
# ---------------------------------------------------------------------------

def _proxy_label(proxies: dict | None) -> str:
    """'host:port' from a proxy dict for logging, credentials stripped."""
    if not proxies:
        return "direct"
    url = proxies.get("http", "")
    return url.rsplit("@", 1)[-1] if "@" in url else url


def _direct_fallback_on_429() -> bool:
    """Darf nach einem 429 (oder im Pool-Cooldown) direkt ohne Proxy gefetcht werden?

    Maschinenspezifisch, daher Env-Var statt Profil-Flag: auf dem Mac Mini ist
    die eigene IP bei FIDE frei (direkt = sinnvoller Fallback), die VPS-IP ist
    dagegen geblockt — dort wäre jeder direkte Versuch zum Scheitern verurteilt
    und würde nur ein zusätzliches Signal von der gesperrten IP senden.
    Default true (bisheriges Verhalten); auf dem VPS via docker-compose.yml
    DIRECT_FALLBACK_ON_429=false gesetzt.
    """
    return os.getenv("DIRECT_FALLBACK_ON_429", "true").strip().lower() not in ("0", "false", "no")


def _fetch(
    fide_id: int,
    period_str: str,
    profile: dict,
    proxy_manager: "ProxyManager | None" = None,
) -> tuple[str | None, int]:
    """Fetch FIDE calculations HTML, drawing a fresh proxy on every retry attempt.

    Returns (html, bytes). html is None on non-fatal error; raises BlockedError on 403.
    On HTTP 429, returns (None, retry_after_seconds) where retry_after_seconds is taken
    from the Retry-After response header if present, else 0 (caller uses profile default).

    Pass proxy_manager=None to always fetch directly (no proxy) — used for the
    post-429 fallback attempt. Otherwise a new proxy is drawn per attempt: with a
    pool-based provider (many static IPs, some dead at any given time — see
    orchestrator/proxy_manager.py) reusing one proxy across all retries would waste
    the whole retry budget on a single doomed IP instead of failing over to another.
    """
    url = AJAX_URL.format(fide_id=fide_id, period=period_str)
    headers = {
        **HEADERS,
        "Referer": REFERER_URL.format(fide_id=fide_id, period=period_str),
    }
    max_retries = profile.get("max_retries", 3)
    timeout = profile.get("timeout_seconds", 20)
    use_proxy = profile.get("use_proxy", True) and proxy_manager is not None

    for attempt in range(1, max_retries + 1):
        proxies = proxy_manager.get_proxy() if use_proxy else None
        if use_proxy and proxies is None and not _direct_fallback_on_429():
            # Pool im Cooldown (get_proxy → None): NICHT stillschweigend direkt
            # fetchen — auf Maschinen mit FIDE-geblockter IP (VPS) wäre das ein
            # sicherer Fehlschlag. Stattdessen Cooldown aussitzen, dann neu ziehen.
            remaining = proxy_manager.cooldown_remaining()
            if remaining > 0:
                logger.info("Proxy-Pool im Cooldown — warte %.0fs statt direkt zu fetchen "
                            "(DIRECT_FALLBACK_ON_429=false)", remaining)
                time.sleep(remaining + 0.5)
            proxies = proxy_manager.get_proxy()
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)

            if resp.status_code == 403:
                raise BlockedError(f"HTTP 403 fide_id={fide_id} period={period_str}")
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                logger.warning("HTTP 429 fide_id=%s period=%s (attempt %d) Retry-After=%s",
                               fide_id, period_str, attempt, retry_after or "nicht gesetzt")
                return None, retry_after

            resp.raise_for_status()

            if not resp.text.strip():
                # Leerer 200er ist immer anomal: echte leere Perioden liefern seit dem
                # FIDE-Umbau 2026-07 den Text "No records found ...". Ein leerer Body
                # heißt Endpoint tot/umbenannt (so am 14.07.2026 passiert) — als
                # Fehlschlag werten, damit der Circuit-Breaker im Aufrufer greift,
                # statt massenhaft falsche no_data zu schreiben.
                if attempt == max_retries:
                    logger.error("Leere 200-Antwort für fide_id=%s period=%s nach %d Versuchen "
                                 "— FIDE-Endpoint defekt/umbenannt? Als Fehlschlag gewertet",
                                 fide_id, period_str, max_retries)
                    return None, 0
                backoff = 4 ** (attempt - 1)
                logger.warning("Leere 200-Antwort fide_id=%s period=%s (attempt %d/%d) proxy=%s "
                               "— retrying in %ds", fide_id, period_str, attempt, max_retries,
                               _proxy_label(proxies), backoff)
                time.sleep(backoff)
                continue

            if "calc_table" not in resp.text and "No records found" not in resp.text:
                logger.warning("Unerwartetes Antwortformat fide_id=%s period=%s (%d Bytes): weder "
                               "calc_table noch 'No records found' im Body — Format-Änderung bei FIDE?",
                               fide_id, period_str, len(resp.content))

            return resp.text, len(resp.content)

        except BlockedError:
            raise

        except requests.RequestException as exc:
            if attempt == max_retries:
                logger.error("Giving up on fide_id=%s period=%s after %d attempts (last proxy=%s): %s",
                             fide_id, period_str, max_retries, _proxy_label(proxies), exc)
                return None, 0
            backoff = 4 ** (attempt - 1)
            logger.warning("Attempt %d/%d failed for fide_id=%s period=%s proxy=%s — retrying in %ds",
                           attempt, max_retries, fide_id, period_str, _proxy_label(proxies), backoff)
            time.sleep(backoff)

    return None, 0


# ---------------------------------------------------------------------------
# Scrape a single group
# ---------------------------------------------------------------------------

def scrape_group(
    group: Group,
    pg_conn,
    proxy_manager: ProxyManager,
    profile: dict,
    qm: QueueManager,
    slot: int | None = None,
) -> tuple[int, object, float]:
    """Scrape all pending player-period combos for this group.

    Args:
        slot: Thread slot index (None = single-thread mode). Used to write
              per-thread progress to worker_state.json instead of global keys.

    Returns (records_found, pg_conn, mb_group).
    Raises BlockedError if IP gets hard-blocked (caller should abort worker).
    """
    fide_ids = get_fide_ids(pg_conn, group.federation, group.elo_min, group.elo_max,
                            update_only=bool(group.update_only))
    if not fide_ids:
        logger.info("Group %s/%d/%d-%d: no active players found — skipping",
                    group.federation, group.year, group.elo_min, group.elo_max)
        return 0, pg_conn, 0.0

    periods = valid_periods_for_year(group.year)
    if not periods:
        return 0, pg_conn, 0.0

    from scraper.db import get_pending_periods, save_period_no_data
    pending = get_pending_periods(pg_conn, periods, fide_ids=fide_ids)

    if not pending:
        logger.info("Group %s/%d: all %d player-periods already scraped",
                    group.federation, group.year, len(fide_ids) * len(periods))
        return 0, pg_conn, 0.0

    # Pre-filter: skip periods where num_games=0 in rating_history (no games played).
    # NULL means no TXT snapshot → must scrape. Only skip confirmed-zero months.
    with pg_conn.cursor() as cur:
        fide_ids = list({fid for fid, _ in pending})
        periods  = list({p   for _,  p in pending})
        cur.execute(
            """SELECT fide_id, period FROM rating_history
               WHERE fide_id = ANY(%s) AND period = ANY(%s) AND num_games = 0""",
            (fide_ids, periods)
        )
        skip_set = {(r[0], r[1]) for r in cur.fetchall()}

    if skip_set:
        import psycopg2.extras
        with pg_conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                """INSERT INTO scrape_periods (fide_id, period, status, scraped_at)
                   VALUES %s ON CONFLICT (fide_id, period) DO NOTHING""",
                [(fid, (p.isoformat() if hasattr(p, "isoformat") else p), "no_data", "NOW()")
                 for fid, p in skip_set],
            )
        pg_conn.commit()
        logger.info("Pre-filter: %d combos skipped (num_games=0 in TXT snapshot)", len(skip_set))
        pending = [(fid, p) for fid, p in pending if (fid, p) not in skip_set]

    if not pending:
        return 0, pg_conn, 0.0

    logger.info("Group %s/%d/%d-%d: %d pending combos (%d players × up to %d periods)",
                group.federation, group.year, group.elo_min, group.elo_max,
                len(pending), len(fide_ids), len(periods))

    # Progress init
    if slot is None:
        write_state(combos_total=len(pending), combos_done=0)
    else:
        _update_thread_slot(slot, combos_total=len(pending), combos_done=0)

    records_found = 0
    # MB tracking: thread-local (starts fresh per group) vs. session-cumulative (single-thread)
    if slot is None:
        _bytes_session = read_worker_state().get("mb_downloaded", 0.0) * 1024 * 1024
        _bytes_group_start = _bytes_session
    else:
        _bytes_session = 0.0   # thread-local: fresh per group, aggregated by caller
        _bytes_group_start = 0.0
    _consecutive_failures = 0

    for _combo_idx, (fide_id, period) in enumerate(pending, start=1):
        # Pause/stop check inside the loop
        cmd = read_command()
        if cmd in ("stopped", "restart"):
            # restart wie stopped behandeln: Gruppe wird vom Caller auf pending
            # zurückgesetzt, Thread bricht ab; danach exitet der Prozess und Docker
            # startet den Worker mit frischer Config neu. Ohne diesen Check würde der
            # Neustart-Button erst greifen, wenn alle Threads ihre (oft großen) Gruppen
            # fertig haben — bei Parallelbetrieb potenziell Stunden später.
            logger.info("%s-Befehl empfangen — Gruppe wird mitten im Scrape abgebrochen", cmd)
            raise InterruptedError(cmd)
        while cmd == "pause":
            logger.debug("Paused — waiting...")
            time.sleep(_PAUSE_POLL_INTERVAL)
            cmd = read_command()

        period_str = period.isoformat() if isinstance(period, date) else period

        # proxy_manager zieht pro Retry-Versuch eine frische IP (siehe _fetch-Docstring)
        html, nbytes = _fetch(fide_id, period_str, profile, proxy_manager)
        _bytes_session += nbytes

        if html is None:
            # HTTP 429 — nbytes enthält Retry-After (0 = Header nicht gesetzt)
            retry_after = nbytes
            cooldown = retry_after if retry_after > 0 else profile.get("cooldown_on_429", 60)
            proxy_manager.report_block(cooldown)
            allow_direct = _direct_fallback_on_429()
            logger.warning("Backing off %ds (%s), dann %s",
                           cooldown,
                           f"Retry-After={retry_after}s" if retry_after > 0 else "Profil-Default",
                           "direkter Retry ohne Proxy" if allow_direct
                           else "erneuter Versuch über den Pool (frische IP)")
            time.sleep(cooldown)
            html, nbytes2 = _fetch(fide_id, period_str, profile,
                                   None if allow_direct else proxy_manager)
            _bytes_session += nbytes2
            if html is None:
                _consecutive_failures += 1
                if _consecutive_failures >= _CIRCUIT_BREAKER_THRESHOLD:
                    raise RuntimeError(
                        f"Circuit breaker: {_consecutive_failures} aufeinanderfolgende "
                        f"Timeouts (Proxy + direkt) — FIDE blockt diese IP, Gruppe abgebrochen"
                    )
            else:
                _consecutive_failures = 0
        else:
            _consecutive_failures = 0

        if not html or not html.strip():
            pg_conn = save_period_no_data(pg_conn, fide_id, period_str)
            logger.debug("  → no data fide_id=%s period=%s", fide_id, period_str)
        else:
            games, k_factor, own_rating = parse_calculations(html, fide_id, period_str)
            if not games:
                pg_conn = save_period_no_data(pg_conn, fide_id, period_str)
                logger.debug("  → no games parsed fide_id=%s period=%s", fide_id, period_str)
            else:
                pg_conn = save_period(pg_conn, fide_id, period_str, games, k_factor, own_rating)
                records_found += len(games)

        # State alle 200 Combos schreiben (~10 Min bei Normalgeschwindigkeit)
        if _combo_idx % 200 == 0:
            if slot is None:
                write_state(combos_done=_combo_idx, mb_downloaded=_bytes_session / 1024 / 1024)
            else:
                _update_thread_slot(slot, combos_done=_combo_idx)

        wait = qm.get_wait_time(profile)
        time.sleep(wait)

    mb_group = (_bytes_session - _bytes_group_start) / 1024 / 1024
    if slot is None:
        write_state(combos_done=_combo_idx if pending else 0,
                    mb_downloaded=_bytes_session / 1024 / 1024)
    else:
        _update_thread_slot(slot, combos_done=_combo_idx if pending else 0)

    return records_found, pg_conn, mb_group


# ---------------------------------------------------------------------------
# Parallel worker: one thread per slot
# ---------------------------------------------------------------------------

def run_slot(
    slot: int,
    profile_name: str,
    device: str | None,
    proxy_manager: ProxyManager,
    stop_event: threading.Event,
) -> None:
    """Thread function: continuously claims and processes groups from the queue.

    Each thread has its own PostgreSQL connection, QueueManager, and
    ProfileManager instance — no shared mutable state except proxy_manager
    (which is thread-safe) and the worker_state.json (protected by _state_lock).
    """
    logger.info("Thread %d gestartet [%s]", slot, profile_name)
    pm_local = ProfileManager()
    qm_local = QueueManager()
    pg_conn = None

    try:
        pg_conn = get_connection()

        # Startup grace: on first loop iteration a transient "stopped" (e.g. from a
        # truncation-window race or a stale state from the previous container run)
        # should not kill the thread immediately.  We retry once after 1 s.
        _startup_grace = True

        while not stop_event.is_set():
            state = read_worker_state()
            cmd = state.get("command", "stopped")

            if cmd in ("stopped", "restart"):
                if _startup_grace:
                    logger.warning(
                        "Thread %d: '%s' beim Start — warte 1 s und prüfe nochmal",
                        slot, cmd,
                    )
                    time.sleep(1)
                    _startup_grace = False
                    continue
                logger.info("Thread %d: %s-Befehl empfangen", slot, cmd)
                stop_event.set()
                break

            _startup_grace = False  # once we see "run", grace period is over

            if cmd == "pause":
                time.sleep(_PAUSE_POLL_INTERVAL)
                continue

            # Limit checks (both threads read the same shared state)
            max_g  = state.get("max_groups")
            max_h  = state.get("max_hours")
            done   = state.get("groups_done", 0)
            start_at = state.get("started_at")

            if max_g and done >= max_g:
                logger.info("Thread %d: Gruppen-Limit %d erreicht — stoppe", slot, max_g)
                stop_event.set()
                break

            if max_h and start_at:
                try:
                    elapsed = time.time() - time.mktime(
                        time.strptime(start_at, "%Y-%m-%dT%H:%M:%S"))
                    if elapsed >= max_h * 3600:
                        logger.info("Thread %d: Zeit-Limit %.1fh erreicht — stoppe", slot, max_h)
                        stop_event.set()
                        break
                except Exception:
                    pass

            profile = pm_local.pick_fuzzy(override=profile_name)
            group   = qm_local.get_next_group(device=device)

            if group is None:
                if _idle_queue_maintenance(qm_local, f"Thread {slot}"):
                    continue  # Auto-Retry hat Gruppen eingereiht — sofort claimen
                logger.debug("Thread %d: Queue leer — warte %ds", slot, _THREAD_EMPTY_SLEEP)
                time.sleep(_THREAD_EMPTY_SLEEP)
                continue

            label = f"{group.federation}/{group.year}/{group.elo_min}–{group.elo_max}"
            logger.info("Thread %d: Starte Gruppe %s [%s]", slot, label, profile_name)

            _update_thread_slot(slot,
                profile=profile_name,
                current_group=label,
                combos_done=0,
                combos_total=None,
                player_count=group.player_count,
                group_started_at=time.time(),
            )

            run_started = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                records, pg_conn, mb_group = scrape_group(
                    group, pg_conn, proxy_manager, profile, qm_local, slot=slot
                )
                qm_local.mark_done(group.id, records)
                qm_local.log_run(group.id, run_started, "success",
                                 records_found=records,
                                 profile_used=profile_name,
                                 mb_downloaded=mb_group,
                                 thread_slot=slot)
                _increment_global_stats(mb_group)
                logger.info("Thread %d: Fertig %s — %d Partien", slot, label, records)

            except InterruptedError:
                qm_local.reset_to_pending(group.id)
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg="stopped by user", profile_used=profile_name,
                                 thread_slot=slot)
                break

            except BlockedError as exc:
                logger.error("Thread %d: IP geblockt: %s — Gruppe übersprungen", slot, exc)
                qm_local.mark_failed(group.id, str(exc))
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg=str(exc), profile_used=profile_name,
                                 thread_slot=slot)

            except Exception as exc:
                logger.exception("Thread %d: Fehler bei %s", slot, label)
                qm_local.mark_failed(group.id, str(exc))
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg=str(exc), profile_used=profile_name,
                                 thread_slot=slot)
                pg_conn = ensure_connection(pg_conn)

            _clear_thread_slot(slot)

    finally:
        if pg_conn:
            try:
                pg_conn.close()
            except Exception:
                pass
        try:
            qm_local.close()
        except Exception:
            pass
        logger.info("Thread %d gestoppt", slot)


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run(
    profile_name: str | None = None,
    max_groups: int | None = None,
    max_hours: float | None = None,
) -> None:
    """Main worker loop.

    Reads [concurrency] from profiles.yaml to decide between single-thread
    (max_workers=1, existing behaviour) and parallel mode (max_workers 2–4).

    Args:
        profile_name: Force a specific profile (overrides fuzzy selection).
                      In parallel mode, overrides the slot-0 profile only.
        max_groups:   Stop cleanly after this many groups are completed.
        max_hours:    Stop cleanly after this many hours of runtime.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    cfg = _load_concurrency_config()

    # Residential-Slots: worker_slots (neu) hat Priorität über max_workers
    worker_slots = cfg.get("worker_slots", [])
    if worker_slots:
        active_slots = [s for s in worker_slots if s.get("enabled", False)]
    else:
        # Backward-Compat: aus max_workers + worker_profiles ableiten
        max_w    = cfg.get("max_workers", 1)
        profiles = cfg.get("worker_profiles", ["normal"] * 4)
        active_slots = [
            {"slot": i, "enabled": True,
             "profile": profiles[i] if i < len(profiles) else "normal"}
            for i in range(max_w)
        ]
    max_w = len(active_slots)  # Anzahl aktiver Residential-Threads

    # DC-Modus + globale active_hours aus Config lesen
    dc_mode         = cfg.get("dc_mode", "auto")            # "auto" | "individual"
    dc_active_hours = cfg.get("dc_active_hours", [7, 23])   # gilt für alle DC-Threads im auto-Modus

    # Datacenter-Threads auswählen je nach Modus:
    #   auto:       ALLE Threads mit Credentials starten (Timezone entscheidet Aktivität)
    #   individual: nur explizit enabled=true Threads starten (kein Timezone-Check)
    all_dc = cfg.get("datacenter_threads", [])
    # Das enabled-Flag gilt in BEIDEN Modi: ein in der UI deaktivierter Thread wird
    # gar nicht erst gestartet. auto vs. individual unterscheiden sich nur im
    # Timezone-Gating (active_hours), nicht in der Thread-Anzahl. (Früher hat auto
    # alle Threads gespawnt und enabled ignoriert → UI-Reduzierung wirkte nicht.)
    dc_thread_cfgs_raw = [t for t in all_dc if t.get("enabled", True)]

    dc_thread_cfgs = []
    for t in dc_thread_cfgs_raw:
        t_copy = dict(t)
        t_copy["dc_mode"]      = dc_mode
        t_copy["active_hours"] = t.get("active_hours", dc_active_hours)
        dc_thread_cfgs.append(t_copy)

    # Backward-Kompatibilität: altes datacenter-Block (single DC)
    if not dc_thread_cfgs and cfg.get("datacenter", {}).get("enabled", False):
        logger.warning("Altes datacenter-Format erkannt — bitte auf datacenter_threads migrieren")
        old = cfg["datacenter"]
        dc_thread_cfgs = [{
            "id": "dc_de", "enabled": True, "label": "DC", "slot": 99,
            "host": None, "port": 1010,
            "username_env": "PROXY_DC_USERNAME",
            "password_env": "PROXY_PASSWORD",
            "timezone": "Europe/Berlin", "active_hours": [7, 23],
            "profile": old.get("profile", "semi_conservative"),
            "federations": [],
        }]

    if active_slots or dc_thread_cfgs:
        _run_parallel_loop(active_slots, dc_thread_cfgs, profile_name, max_groups, max_hours)
    else:
        _run_single_loop(profile_name, max_groups, max_hours)


def _run_parallel_loop(
    active_slots: list[dict],
    dc_thread_cfgs: list[dict],
    profile_override: str | None,
    max_groups: int | None,
    max_hours: float | None,
) -> None:
    """Parallel mode: spawn enabled residential slots + enabled datacenter threads."""
    proxy_manager = ProxyManager()
    device        = os.getenv("WORKER_DEVICE")
    max_w         = len(active_slots)

    # Reset stale groups from previous run (nur eigene Claims, siehe claimed_by)
    qm_main = QueueManager()
    logger.info("Queue-Identität: claimed_by='%s'", get_device_id())
    reset_count = qm_main.reset_stale_running()
    if reset_count:
        logger.info("Startup: %d unterbrochene running-Gruppen → pending zurückgesetzt", reset_count)
    requeued = qm_main.requeue_failed()
    if requeued:
        logger.info("Startup: Auto-Retry — %d failed-Gruppen zurück auf pending", requeued)
    qm_main.close()

    # Datacenter-Threads konfigurieren (je eigener Host/Pool + Credentials)
    active_dc: list[tuple[dict, ProxyManager]] = []
    for dc_cfg in dc_thread_cfgs:
        dc_proxy = ProxyManager(
            username_env=dc_cfg["username_env"],
            password_env=dc_cfg.get("password_env", "PROXY_PASSWORD"),
            host_override=dc_cfg.get("host") or None,
            pool_file=dc_cfg.get("pool_file") or None,
        )
        if not dc_proxy._user:
            logger.warning("DC-Thread %s: %s fehlt — übersprungen",
                           dc_cfg["label"], dc_cfg["username_env"])
            continue
        active_dc.append((dc_cfg, dc_proxy))

    total_threads = max_w + len(active_dc)

    if device:
        logger.info("Gerät-Filter: '%s'", device)
    res_info = ", ".join(f"T{s['slot']}={s.get('profile','normal')}" for s in active_slots)
    dc_info  = " | ".join(f"{d['label']}={d['profile']}" for d, _ in active_dc)
    logger.info("Parallel-Modus: %d Threads (%d residential + %d DC) — %s%s",
                total_threads, max_w, len(active_dc), res_info,
                f" | DC: {dc_info}" if dc_info else "")
    if max_groups:
        logger.info("Limit: %d Gruppen (gesamt über alle Threads)", max_groups)
    if max_hours:
        logger.info("Limit: %.1f Stunden", max_hours)

    write_state(
        command="run",
        max_workers=max_w,
        groups_done=0,
        mb_downloaded=0.0,
        threads=[],
        started_at=time.strftime("%Y-%m-%dT%H:%M:%S"),
        max_groups=max_groups,
        max_hours=max_hours,
    )

    stop_event = threading.Event()

    with ThreadPoolExecutor(max_workers=total_threads, thread_name_prefix="scraper") as executor:
        futures = []
        for slot_cfg in active_slots:
            slot         = slot_cfg["slot"]
            profile_name = profile_override if (slot == active_slots[0]["slot"] and profile_override) \
                           else slot_cfg.get("profile", "normal")
            futures.append(executor.submit(
                run_slot, slot, profile_name, device, proxy_manager, stop_event,
            ))
        for dc_cfg, dc_proxy in active_dc:
            futures.append(executor.submit(run_dc_slot, dc_cfg, dc_proxy, stop_event))

        for fut in futures:
            try:
                fut.result()
            except Exception:
                logger.exception("Unbehandelte Exception in Thread-Pool")

    # Restart-Command: Worker-Prozess beenden → Docker startet ihn neu mit neuer Config
    final_cmd = read_worker_state().get("command")
    if final_cmd == "restart":
        logger.info("Restart-Befehl: Worker-Prozess exitiert — Docker-Neustart erwartet")
        write_state(command="run", threads=[])   # nach Neustart sofort loslegen
        sys.exit(0)

    write_state(command="stopped", threads=[])
    logger.info("Alle %d Threads beendet", max_w)


def run_dc_slot(
    dc_cfg: dict,
    dc_proxy: ProxyManager,
    stop_event: threading.Event,
) -> None:
    """DC-Thread: scrapet nur Gruppen mit passender thread_affinity,
    pausiert außerhalb der konfigurierten Ortszeit (active_hours).
    """
    slot         = dc_cfg["slot"]
    profile_name = dc_cfg["profile"]
    affinity     = dc_cfg["id"]
    label        = dc_cfg["label"]

    logger.info("DC-Thread %s (Slot %d) gestartet [%s, %s, aktiv %s–%s Uhr]",
                label, slot, profile_name, dc_cfg.get("timezone", "?"),
                *dc_cfg.get("active_hours", [7, 23]))
    pm_local = ProfileManager()
    qm_local = QueueManager()
    pg_conn  = None

    try:
        pg_conn = get_connection()

        # Startup grace: on first loop iteration a transient "stopped" (e.g. from a
        # truncation-window race or a stale state from the previous container run)
        # should not kill the thread immediately.  We retry once after 1 s.
        _startup_grace = True

        while not stop_event.is_set():
            state = read_worker_state()
            cmd   = state.get("command", "stopped")

            if cmd in ("stopped", "restart"):
                if _startup_grace:
                    logger.warning(
                        "DC-Thread %s: '%s' beim Start — warte 1 s und prüfe nochmal",
                        label, cmd,
                    )
                    time.sleep(1)
                    _startup_grace = False
                    continue
                logger.info("DC-Thread %s: %s-Befehl empfangen", label, cmd)
                stop_event.set()
                break

            _startup_grace = False  # once we see "run", grace period is over

            if cmd == "pause":
                time.sleep(_PAUSE_POLL_INTERVAL)
                continue

            # Limit-Checks
            max_g    = state.get("max_groups")
            max_h    = state.get("max_hours")
            done     = state.get("groups_done", 0)
            start_at = state.get("started_at")

            if max_g and done >= max_g:
                logger.info("DC-Thread %s: Gruppen-Limit %d erreicht — stoppe", label, max_g)
                stop_event.set()
                break

            if max_h and start_at:
                try:
                    elapsed = time.time() - time.mktime(
                        time.strptime(start_at, "%Y-%m-%dT%H:%M:%S"))
                    if elapsed >= max_h * 3600:
                        logger.info("DC-Thread %s: Zeit-Limit %.1fh erreicht — stoppe", label, max_h)
                        stop_event.set()
                        break
                except Exception:
                    pass

            # Enabled-Check ZUERST: ein in der UI deaktivierter Thread stoppt sofort —
            # auch wenn er sonst (außerhalb active_hours) nur schlafen würde. Stünde
            # dieser Check nach dem Timezone-Sleep, bliebe ein schlafender Thread als
            # 💤-Slot hängen und würde das Deaktivieren nie bemerken.
            if not _read_dc_thread_enabled(affinity):
                logger.info("DC-Thread %s: per UI-Toggle deaktiviert — stoppe", label)
                _clear_thread_slot(slot)
                break

            # Timezone-Check: nur im auto-Modus aktiv
            dc_mode = dc_cfg.get("dc_mode", "auto")
            if dc_mode == "auto" and not _dc_is_active(dc_cfg):
                secs = _dc_seconds_until_active(dc_cfg)
                h_start = dc_cfg.get("active_hours", [7, 23])[0]
                logger.info("DC-Thread %s [auto]: außerhalb Aktivzeiten — schlafe %.0f s (bis %02d:00 Uhr %s)",
                            label, secs, h_start, dc_cfg.get("timezone", ""))
                _update_thread_slot(slot,
                    profile=profile_name,
                    current_group=f"💤 bis {h_start:02d}:00 Uhr",
                    combos_done=0,
                    combos_total=None,
                    player_count=None,
                    group_started_at=None,
                )
                stop_event.wait(timeout=min(secs, _DC_SLEEP_CHECK_INTERVAL))
                continue

            profile = pm_local.pick_fuzzy(override=profile_name)
            group   = qm_local.get_next_group(dc_affinity=affinity)

            if group is None:
                if _idle_queue_maintenance(qm_local, f"DC-Thread {label}"):
                    continue  # Auto-Retry hat Gruppen eingereiht — sofort claimen
                logger.debug("DC-Thread %s: Queue leer — warte %ds", label, _THREAD_EMPTY_SLEEP)
                _clear_thread_slot(slot)
                time.sleep(_THREAD_EMPTY_SLEEP)
                continue

            grp_label = f"{group.federation}/{group.year}/{group.elo_min}–{group.elo_max}"
            logger.info("DC-Thread %s: Starte Gruppe %s [%s]", label, grp_label, profile_name)

            _update_thread_slot(slot,
                profile=profile_name,
                current_group=grp_label,
                combos_done=0,
                combos_total=None,
                player_count=group.player_count,
                group_started_at=time.time(),
            )

            run_started = time.strftime("%Y-%m-%dT%H:%M:%S")
            try:
                records, pg_conn, mb_group = scrape_group(
                    group, pg_conn, dc_proxy, profile, qm_local, slot=slot
                )
                qm_local.mark_done(group.id, records)
                qm_local.log_run(group.id, run_started, "success",
                                 records_found=records,
                                 profile_used=profile_name,
                                 mb_downloaded=mb_group,
                                 thread_slot=slot)
                _increment_global_stats(mb_group)
                logger.info("DC-Thread %s: Fertig %s — %d Partien", label, grp_label, records)

            except InterruptedError:
                qm_local.reset_to_pending(group.id)
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg="stopped by user", profile_used=profile_name,
                                 thread_slot=slot)
                break

            except BlockedError as exc:
                logger.error("DC-Thread %s: IP geblockt: %s — Gruppe übersprungen", label, exc)
                qm_local.mark_failed(group.id, str(exc))
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg=str(exc), profile_used=profile_name,
                                 thread_slot=slot)

            except Exception as exc:
                logger.exception("DC-Thread %s: Fehler bei %s", label, grp_label)
                qm_local.mark_failed(group.id, str(exc))
                qm_local.log_run(group.id, run_started, "failed",
                                 error_msg=str(exc), profile_used=profile_name,
                                 thread_slot=slot)
                pg_conn = ensure_connection(pg_conn)

            _clear_thread_slot(slot)

    finally:
        if pg_conn:
            try:
                pg_conn.close()
            except Exception:
                pass
        try:
            qm_local.close()
        except Exception:
            pass
        logger.info("DC-Thread %s (Slot %d) gestoppt", label, slot)


def _run_single_loop(
    profile_name: str | None,
    max_groups: int | None,
    max_hours: float | None,
) -> None:
    """Single-thread mode — original sequential worker behaviour, unchanged."""
    pm = ProfileManager()
    proxy_manager = ProxyManager()
    qm = QueueManager()
    device = os.getenv("WORKER_DEVICE")

    # Beim Start: eigene unterbrochene 'running'-Gruppen zurücksetzen (Worker-Neustart nach Crash/Redeploy)
    logger.info("Queue-Identität: claimed_by='%s'", get_device_id())
    reset_count = qm.reset_stale_running()
    if reset_count:
        logger.info("Startup: %d unterbrochene running-Gruppen → pending zurückgesetzt", reset_count)
    requeued = qm.requeue_failed()
    if requeued:
        logger.info("Startup: Auto-Retry — %d failed-Gruppen zurück auf pending", requeued)

    if profile_name:
        pm.set_active(profile_name)

    if device:
        logger.info("Gerät-Filter: '%s'", device)
    if max_groups:
        logger.info("Limit: %d Gruppen", max_groups)
    if max_hours:
        logger.info("Limit: %.1f Stunden", max_hours)

    # CLI-Limits: explizit gesetzt → immer als "run" starten und Limits schreiben
    if max_groups or max_hours:
        write_state(command="run", current_group=None,
                    max_groups=max_groups, max_hours=max_hours,
                    started_at=time.strftime("%Y-%m-%dT%H:%M:%S"), groups_done=0)
    elif not WORKER_STATE_PATH.exists():
        # Erster Start (kein State) → auto-run
        write_state(command="run", current_group=None)
    else:
        # Neustart nach Crash/Reboot: bestehenden command beibehalten, nur stale group löschen
        write_state(current_group=None)

    pg_conn = get_connection()

    try:
        while True:
            state = read_worker_state()

            # ── Limit-Checks (aus worker_state.json, live aktualisierbar) ─
            _max_g = state.get("max_groups")
            _max_h = state.get("max_hours")
            _done  = state.get("groups_done", 0)
            _start = state.get("started_at")

            if _max_g and _done >= _max_g:
                logger.info("Gruppen-Limit erreicht (%d) — Worker beendet sich", _max_g)
                write_state(command="stopped")
                break
            if _max_h and _start:
                try:
                    elapsed = (time.time() -
                               time.mktime(time.strptime(_start, "%Y-%m-%dT%H:%M:%S")))
                    if elapsed >= _max_h * 3600:
                        logger.info("Zeit-Limit erreicht (%.1fh) — Worker beendet sich", _max_h)
                        write_state(command="stopped")
                        break
                except Exception:
                    pass

            # ── Dashboard-Command ─────────────────────────────────────────
            cmd = state.get("command", "stopped")
            if cmd == "restart":
                logger.info("Restart-Befehl: Worker-Prozess exitiert — Docker-Neustart erwartet")
                write_state(command="run", current_group=None)  # nach Neustart sofort loslegen
                sys.exit(0)
            # "stopped" und "pause" → warten (Container läuft weiter, wartet auf "run")
            if cmd in ("stopped", "pause"):
                write_state(current_group=None)
                time.sleep(_PAUSE_POLL_INTERVAL)
                continue

            # ── Nächste Gruppe holen ──────────────────────────────────────
            group = qm.get_next_group(device=device)
            if group is None:
                if _idle_queue_maintenance(qm, "Worker"):
                    continue  # Auto-Retry hat Gruppen eingereiht — sofort claimen
                logger.info("Queue leer — warte %ds", _EMPTY_QUEUE_SLEEP)
                write_state(current_group=None)
                time.sleep(_EMPTY_QUEUE_SLEEP)
                continue

            # Fuzzy-Profilwahl: Gewichtung aus profiles.yaml, Override per Gruppe möglich
            profile = pm.pick_fuzzy(override=group.profile)

            label = f"{group.federation}/{group.year}/{group.elo_min}–{group.elo_max}"
            logger.info("Starte Gruppe: %s (Priorität %d, Profil: %s)",
                        label, group.priority, profile["name"])
            write_state(
                current_group=label,
                current_year=group.year,
                current_profile=profile["name"],
                player_count=group.player_count,
                combos_total=None,
                combos_done=0,
                group_started_at=time.time(),
            )
            run_started = time.strftime("%Y-%m-%dT%H:%M:%S")

            try:
                records, pg_conn, mb_group = scrape_group(group, pg_conn, proxy_manager, profile, qm)
                qm.mark_done(group.id, records)
                qm.log_run(group.id, run_started, "success",
                           records_found=records, profile_used=profile.get("name", ""),
                           mb_downloaded=mb_group)
                new_done = state.get("groups_done", 0) + 1
                write_state(groups_done=new_done)
                _max_g = state.get("max_groups")
                remaining = f"({new_done}/{_max_g} Gruppen)" if _max_g else f"({new_done} Gruppen)"
                logger.info("Fertig: %s — %d Partien %s", label, records, remaining)

            except InterruptedError:
                qm.reset_to_pending(group.id)
                qm.log_run(group.id, run_started, "failed",
                           error_msg="stopped by user", profile_used=profile.get("name", ""))
                # Nicht break — Hauptloop prüft command und wartet auf "run"

            except BlockedError as exc:
                logger.error("IP geblockt: %s — Gruppe übersprungen, Worker läuft weiter", exc)
                qm.mark_failed(group.id, str(exc))
                qm.log_run(group.id, run_started, "failed",
                           error_msg=str(exc), profile_used=profile.get("name", ""))

            except Exception as exc:
                logger.exception("Fehler bei %s", label)
                qm.mark_failed(group.id, str(exc))
                qm.log_run(group.id, run_started, "failed",
                           error_msg=str(exc), profile_used=profile.get("name", ""))
                pg_conn = ensure_connection(pg_conn)

            write_state(current_group=None)

    finally:
        try:
            pg_conn.close()
        except Exception:
            pass
        logger.info("Worker stopped")


# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="Orchestrator worker",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Beispiele:
  python orchestrator/worker.py                        # Endlosschleife
  python orchestrator/worker.py --max-groups 10        # Stopp nach 10 Gruppen
  python orchestrator/worker.py --max-hours 4          # Stopp nach 4 Stunden
  python orchestrator/worker.py --max-hours 2 --max-groups 5   # was zuerst eintritt
  python orchestrator/worker.py --profile conservative  # Profil erzwingen

Mac Mini (lokal, Tunnel muss laufen):
  WORKER_DEVICE=mac_mini python orchestrator/worker.py --max-hours 8

Parallel-Modus wird automatisch aktiviert wenn profiles.yaml [concurrency] max_workers > 1.
        """,
    )
    parser.add_argument(
        "--profile", choices=["conservative", "normal", "aggressive"],
        help="Scrape-Profil erzwingen (überschreibt Fuzzy-Auswahl)",
    )
    parser.add_argument(
        "--max-groups", type=int, metavar="N",
        help="Stopp nach N abgeschlossenen Gruppen",
    )
    parser.add_argument(
        "--max-hours", type=float, metavar="H",
        help="Stopp nach H Stunden Laufzeit",
    )
    args = parser.parse_args()
    run(
        profile_name=args.profile,
        max_groups=args.max_groups,
        max_hours=args.max_hours,
    )
