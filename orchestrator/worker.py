"""Scraping orchestrator worker.

Reads the queue from SQLite, fetches FIDE data via ProxyJet, and writes results
to the existing PostgreSQL database using the scraper's parser and DB modules.

Run:
    python orchestrator/worker.py [--profile conservative|normal|aggressive]

Control (from dashboard or terminal):
    worker_state.json  {"command": "run"} | {"command": "pause"} | {"command": "stopped"}
"""

import json
import logging
import os
import sys
import time
from datetime import date
from pathlib import Path

import requests

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from orchestrator.profile_manager import ProfileManager
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
_EMPTY_QUEUE_SLEEP = 120      # seconds to wait when queue is empty
_CIRCUIT_BREAKER_THRESHOLD = 15  # consecutive double-failures before aborting group


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
    """Update worker_state.json, preserving existing keys (partial update)."""
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
) -> tuple[int, object]:
    """Scrape all pending player-period combos for this group.

    Returns (records_found, pg_conn) — pg_conn may be reopened on tunnel drop.
    Raises BlockedError if IP gets hard-blocked (caller should abort worker).
    """
    fide_ids = get_fide_ids(pg_conn, group.federation, group.elo_min, group.elo_max)
    if not fide_ids:
        logger.info("Group %s/%d/%d-%d: no active players found — skipping",
                    group.federation, group.year, group.elo_min, group.elo_max)
        return 0, pg_conn

    periods = valid_periods_for_year(group.year)
    if not periods:
        return 0, pg_conn

    from scraper.db import get_pending_periods
    pending = get_pending_periods(pg_conn, periods, fide_ids=fide_ids)

    if not pending:
        logger.info("Group %s/%d: all %d player-periods already scraped",
                    group.federation, group.year, len(fide_ids) * len(periods))
        return 0, pg_conn

    logger.info("Group %s/%d/%d-%d: %d pending combos (%d players × up to %d periods)",
                group.federation, group.year, group.elo_min, group.elo_max,
                len(pending), len(fide_ids), len(periods))
    write_state(combos_total=len(pending), combos_done=0)

    records_found = 0
    _bytes_session = read_worker_state().get("mb_downloaded", 0.0) * 1024 * 1024
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
            write_state(combos_done=_combo_idx, mb_downloaded=_bytes_session / 1024 / 1024)

        wait = qm.get_wait_time(profile)
        time.sleep(wait)

    write_state(combos_done=_combo_idx if pending else 0,
                mb_downloaded=_bytes_session / 1024 / 1024)

    return records_found, pg_conn


# ---------------------------------------------------------------------------
# Main worker loop
# ---------------------------------------------------------------------------

def run(
    profile_name: str | None = None,
    max_groups: int | None = None,
    max_hours: float | None = None,
) -> None:
    """Main worker loop.

    Args:
        profile_name: Force a specific profile (overrides fuzzy selection).
        max_groups:   Stop cleanly after this many groups are completed.
        max_hours:    Stop cleanly after this many hours of runtime.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

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
            # "stopped" und "pause" → warten (Container läuft weiter, wartet auf "run")
            cmd = state.get("command", "stopped")
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
                records, pg_conn = scrape_group(group, pg_conn, proxy_manager, profile, qm)
                qm.mark_done(group.id, records)
                qm.log_run(group.id, run_started, "success",
                           records_found=records, profile_used=profile.get("name", ""))
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
