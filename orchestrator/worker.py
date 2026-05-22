"""Scraping orchestrator worker.

Reads the queue from SQLite, fetches FIDE data via ProxyJet, and writes results
to the existing PostgreSQL database using the scraper's parser and DB modules.

Run:
    python orchestrator/worker.py [--profile conservative|normal|aggressive]

Control (from dashboard or terminal):
    worker_state.json  {"command": "run"} | {"command": "pause"} | {"command": "stopped"}

Parallel mode (configured via profiles.yaml [concurrency]):
    max_workers > 1 spawns N threads, each claiming its own group from the queue.
    Each thread runs independently with its own PostgreSQL + SQLite connection.
"""

import json
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
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.profile_manager import ProfileManager, PROFILES_PATH
from orchestrator.proxy_manager import ProxyJetManager
from orchestrator.queue_manager import Group, QueueManager
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

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
WORKER_STATE_PATH = _DATA_DIR / "worker_state.json"

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

# Lock protecting all read-modify-write operations on worker_state.json
_state_lock = threading.Lock()


# ---------------------------------------------------------------------------
# State file helpers
# ---------------------------------------------------------------------------

def read_worker_state() -> dict:
    """Return the full worker_state.json as a dict."""
    try:
        return json.loads(WORKER_STATE_PATH.read_text())
    except Exception:
        return {}


def read_command() -> str:
    """Return the current command from worker_state.json ('run'/'pause'/'stopped')."""
    return read_worker_state().get("command", "stopped")


def write_state(command: str | None = None, **extra) -> None:
    """Update worker_state.json, preserving existing keys (partial update).
    Thread-safe: acquires _state_lock before read-modify-write.
    """
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        if command is not None:
            data["command"] = command
        for k, v in extra.items():
            if v is not None or k in data:
                data[k] = v
        WORKER_STATE_PATH.write_text(json.dumps(data, indent=2))


def _update_thread_slot(slot: int, **kwargs) -> None:
    """Update the state entry for a specific thread slot (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        threads = data.setdefault("threads", [])
        entry = next((t for t in threads if t.get("slot") == slot), None)
        if entry is None:
            entry = {"slot": slot}
            threads.append(entry)
        entry.update(kwargs)
        data["threads"] = sorted(threads, key=lambda t: t.get("slot", 0))
        WORKER_STATE_PATH.write_text(json.dumps(data, indent=2))


def _clear_thread_slot(slot: int) -> None:
    """Remove a thread slot from the threads list (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        data["threads"] = [t for t in data.get("threads", []) if t.get("slot") != slot]
        WORKER_STATE_PATH.write_text(json.dumps(data, indent=2))


def _increment_global_stats(mb_group: float) -> None:
    """Atomically increment groups_done and mb_downloaded (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        data["groups_done"] = data.get("groups_done", 0) + 1
        data["mb_downloaded"] = round(data.get("mb_downloaded", 0.0) + mb_group, 2)
        WORKER_STATE_PATH.write_text(json.dumps(data, indent=2))


def _load_concurrency_config() -> dict:
    """Load the [concurrency] section from profiles.yaml."""
    try:
        with open(PROFILES_PATH, encoding="utf-8") as f:
            data = yaml.safe_load(f)
        return data.get("concurrency", {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Player lookup
# ---------------------------------------------------------------------------

def get_fide_ids(pg_conn, federation: str, elo_min: int, elo_max: int) -> list[int]:
    """Return active player fide_ids matching the federation and ELO range."""
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT fide_id FROM players
            WHERE active = TRUE
              AND federation = %s
              AND std_rating BETWEEN %s AND %s
            ORDER BY fide_id
            """,
            (federation, elo_min, elo_max),
        )
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

def _fetch(
    session: requests.Session,
    fide_id: int,
    period_str: str,
    profile: dict,
) -> tuple[str | None, int]:
    """Fetch FIDE calculations HTML using the given session (with proxy pre-set).

    Returns (html, bytes). html is None on non-fatal error; raises BlockedError on 403.
    On HTTP 429, returns (None, retry_after_seconds) where retry_after_seconds is taken
    from the Retry-After response header if present, else 0 (caller uses profile default).
    """
    url = AJAX_URL.format(fide_id=fide_id, period=period_str)
    headers = {
        **HEADERS,
        "Referer": REFERER_URL.format(fide_id=fide_id, period=period_str),
    }
    max_retries = profile.get("max_retries", 3)
    timeout = profile.get("timeout_seconds", 20)

    for attempt in range(1, max_retries + 1):
        try:
            resp = session.get(url, headers=headers, timeout=timeout)

            if resp.status_code == 403:
                raise BlockedError(f"HTTP 403 fide_id={fide_id} period={period_str}")
            if resp.status_code == 429:
                retry_after = int(resp.headers.get("Retry-After", 0))
                logger.warning("HTTP 429 fide_id=%s period=%s (attempt %d) Retry-After=%s",
                               fide_id, period_str, attempt, retry_after or "nicht gesetzt")
                return None, retry_after

            resp.raise_for_status()
            return resp.text, len(resp.content)

        except BlockedError:
            raise

        except requests.RequestException as exc:
            if attempt == max_retries:
                logger.error("Giving up on fide_id=%s period=%s after %d attempts: %s",
                             fide_id, period_str, max_retries, exc)
                return None, 0
            backoff = 4 ** (attempt - 1)
            logger.warning("Attempt %d/%d failed for fide_id=%s period=%s — retrying in %ds",
                           attempt, max_retries, fide_id, period_str, backoff)
            time.sleep(backoff)

    return None, 0


# ---------------------------------------------------------------------------
# Scrape a single group
# ---------------------------------------------------------------------------

def scrape_group(
    group: Group,
    pg_conn,
    proxy_manager: ProxyJetManager,
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
    fide_ids = get_fide_ids(pg_conn, group.federation, group.elo_min, group.elo_max)
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
        if cmd == "stopped":
            logger.info("Stop command received — aborting group mid-scrape")
            raise InterruptedError("stopped")
        while cmd == "pause":
            logger.debug("Paused — waiting...")
            time.sleep(_PAUSE_POLL_INTERVAL)
            cmd = read_command()

        period_str = period.isoformat() if isinstance(period, date) else period

        # Build session — proxy nur wenn Profil es erlaubt
        session = requests.Session()
        if profile.get("use_proxy", True):
            proxy = proxy_manager.get_proxy()
            if proxy:
                session.proxies.update(proxy)

        html, nbytes = _fetch(session, fide_id, period_str, profile)
        _bytes_session += nbytes

        if html is None:
            # HTTP 429 — nbytes enthält Retry-After (0 = Header nicht gesetzt)
            retry_after = nbytes
            cooldown = retry_after if retry_after > 0 else profile.get("cooldown_on_429", 60)
            proxy_manager.report_block(cooldown)
            logger.warning("Backing off %ds (%s), dann direkter Retry ohne Proxy",
                           cooldown, f"Retry-After={retry_after}s" if retry_after > 0 else "Profil-Default")
            time.sleep(cooldown)
            html, nbytes2 = _fetch(requests.Session(), fide_id, period_str, profile)
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
    proxy_manager: ProxyJetManager,
    stop_event: threading.Event,
) -> None:
    """Thread function: continuously claims and processes groups from the queue.

    Each thread has its own PostgreSQL connection, SQLite QueueManager, and
    ProfileManager instance — no shared mutable state except proxy_manager
    (which is thread-safe) and the worker_state.json (protected by _state_lock).
    """
    logger.info("Thread %d gestartet [%s]", slot, profile_name)
    pm_local = ProfileManager()
    qm_local = QueueManager()
    pg_conn = None

    try:
        pg_conn = get_connection()

        while not stop_event.is_set():
            state = read_worker_state()
            cmd = state.get("command", "stopped")

            if cmd in ("stopped", "restart"):
                logger.info("Thread %d: %s-Befehl empfangen", slot, cmd)
                stop_event.set()
                break

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

    cfg   = _load_concurrency_config()
    max_w = cfg.get("max_workers", 1)

    # Neues Format: datacenter_threads (Liste)
    dc_thread_cfgs = [t for t in cfg.get("datacenter_threads", []) if t.get("enabled", False)]

    # Backward-Kompatibilität: altes datacenter-Block (single DC)
    if not dc_thread_cfgs and cfg.get("datacenter", {}).get("enabled", False):
        logger.warning("Altes datacenter-Format erkannt — bitte auf datacenter_threads migrieren")
        old = cfg["datacenter"]
        dc_thread_cfgs = [{
            "id": "dc_de", "enabled": True, "label": "DC", "slot": 99,
            "host": None, "port": 1010,
            "username_env": "PROXYJET_DC_USERNAME",
            "password_env": "PROXYJET_PASSWORD",
            "timezone": "Europe/Berlin", "active_hours": [7, 23],
            "profile": old.get("profile", "semi_conservative"),
            "federations": [],
        }]

    if max_w > 1 or dc_thread_cfgs:
        _run_parallel_loop(max_w, cfg.get("worker_profiles", []),
                           dc_thread_cfgs, profile_name, max_groups, max_hours)
    else:
        _run_single_loop(profile_name, max_groups, max_hours)


def _run_parallel_loop(
    max_w: int,
    worker_profiles_cfg: list[str],
    dc_thread_cfgs: list[dict],
    profile_override: str | None,
    max_groups: int | None,
    max_hours: float | None,
) -> None:
    """Parallel mode: spawn max_w residential threads + enabled datacenter threads."""
    proxy_manager = ProxyJetManager()
    device = os.getenv("WORKER_DEVICE")

    # Determine profile per slot; CLI --profile overrides slot-0
    profiles_list = worker_profiles_cfg or (["normal"] * max_w)
    if profile_override:
        profiles_list = list(profiles_list)
        profiles_list[0] = profile_override

    # Reset stale groups from previous run
    qm_main = QueueManager()
    reset_count = qm_main.reset_stale_running()
    if reset_count:
        logger.info("Startup: %d unterbrochene running-Gruppen → pending zurückgesetzt", reset_count)
    qm_main.close()

    # Datacenter-Threads konfigurieren (je eigener Host + Credentials)
    active_dc: list[tuple[dict, ProxyJetManager]] = []
    for dc_cfg in dc_thread_cfgs:
        dc_proxy = ProxyJetManager(
            username_env=dc_cfg["username_env"],
            password_env=dc_cfg.get("password_env", "PROXYJET_PASSWORD"),
            host_override=dc_cfg.get("host") or None,
        )
        if not dc_proxy._user:
            logger.warning("DC-Thread %s: %s fehlt — übersprungen",
                           dc_cfg["label"], dc_cfg["username_env"])
            continue
        active_dc.append((dc_cfg, dc_proxy))

    total_threads = max_w + len(active_dc)

    if device:
        logger.info("Gerät-Filter: '%s'", device)
    dc_info = " | ".join(f"{d['label']}={d['profile']}" for d, _ in active_dc)
    logger.info("Parallel-Modus: %d Threads (%d residential + %d DC) — Residential: %s%s",
                total_threads, max_w, len(active_dc),
                ", ".join(f"T{i}={profiles_list[i % len(profiles_list)]}" for i in range(max_w)),
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
        futures = [
            executor.submit(
                run_slot,
                slot,
                profiles_list[slot % len(profiles_list)],
                device,
                proxy_manager,
                stop_event,
            )
            for slot in range(max_w)
        ]
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
    dc_proxy: ProxyJetManager,
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

        while not stop_event.is_set():
            state = read_worker_state()
            cmd   = state.get("command", "stopped")

            if cmd in ("stopped", "restart"):
                logger.info("DC-Thread %s: %s-Befehl empfangen", label, cmd)
                stop_event.set()
                break

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

            # Timezone-Check: nur innerhalb active_hours aktiv
            if not _dc_is_active(dc_cfg):
                secs = _dc_seconds_until_active(dc_cfg)
                h_start = dc_cfg.get("active_hours", [7, 23])[0]
                logger.info("DC-Thread %s: außerhalb Aktivzeiten — schlafe %.0f s (bis %02d:00 Uhr %s)",
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
    proxy_manager = ProxyJetManager()
    qm = QueueManager()
    device = os.getenv("WORKER_DEVICE")

    # Beim Start: unterbrochene 'running'-Gruppen zurücksetzen (Worker-Neustart nach Crash/Redeploy)
    reset_count = qm.reset_stale_running()
    if reset_count:
        logger.info("Startup: %d unterbrochene running-Gruppen → pending zurückgesetzt", reset_count)

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
