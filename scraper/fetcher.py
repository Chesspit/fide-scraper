import logging
import os
import random
import time

import requests

from scraper.config import config

logger = logging.getLogger(__name__)

AJAX_URL = (
    "https://ratings.fide.com/a_indv_calculations.php"
    "?id_number={fide_id}&rating_period={period}&t=0"
)

REFERER_URL = (
    "https://ratings.fide.com/calculations.phtml"
    "?id_number={fide_id}&period={period}&rating=0"
)

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "X-Requested-With": "XMLHttpRequest",
}

# Pause duration when FIDE returns 429 (rate limited)
RATE_LIMIT_PAUSE_SECONDS = 45 * 60  # 45 minutes


class RateLimitedError(Exception):
    """FIDE returned HTTP 429 — we are being rate-limited."""


class BlockedError(Exception):
    """FIDE returned HTTP 403 — our IP appears to be blocked."""


def fetch_calculations(fide_id: int, period_str: str) -> str:
    """Fetch the calculations HTML fragment for a player/period from FIDE.

    Raises:
        RateLimitedError: On HTTP 429 — caller should pause and retry.
        BlockedError: On HTTP 403 — caller should stop completely.
        requests.RequestException: After max retries exhausted for other errors.
    """
    scraper_cfg = config["scraper"]
    max_attempts = scraper_cfg["retry"]["max_attempts"]
    backoff_base = scraper_cfg["retry"]["backoff_base"]
    timeout = scraper_cfg["timeout"]

    url = AJAX_URL.format(fide_id=fide_id, period=period_str)
    headers = {
        **HEADERS,
        "Referer": REFERER_URL.format(fide_id=fide_id, period=period_str),
    }

    proxy_url = os.getenv("FIDE_PROXY")
    proxies = {"http": proxy_url, "https": proxy_url} if proxy_url else None

    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout, proxies=proxies)

            if resp.status_code == 429:
                raise RateLimitedError(
                    f"HTTP 429 for fide_id={fide_id} period={period_str} — rate limited"
                )
            if resp.status_code == 403:
                raise BlockedError(
                    f"HTTP 403 for fide_id={fide_id} period={period_str} — IP blocked"
                )

            resp.raise_for_status()
            return resp.text

        except (RateLimitedError, BlockedError):
            raise  # propagate immediately — no retry

        except requests.RequestException as exc:
            last_exception = exc
            status = getattr(getattr(exc, "response", None), "status_code", None)

            if status and status < 429 and status not in range(500, 600):
                raise

            wait = backoff_base ** (attempt - 1)
            logger.warning(
                "Attempt %d/%d failed for fide_id=%s period=%s (status=%s). "
                "Retrying in %ds...",
                attempt, max_attempts, fide_id, period_str, status, wait,
            )
            time.sleep(wait)

    raise last_exception  # type: ignore[misc]


def sleep_between_requests(backfill: bool = False) -> None:
    """Sleep a human-like random interval between requests.

    Uses a beta distribution skewed towards the lower end (most pauses short,
    occasional longer ones) plus rare extra pauses to mimic human browsing.
    """
    if backfill:
        limits = config["scraper"]["backfill_rate_limit"]
    else:
        limits = config["scraper"]["rate_limit"]

    lo, hi = limits["min_sleep"], limits["max_sleep"]

    # Beta(2, 5): skewed left — most values near min, occasional longer pauses
    beta_sample = random.betavariate(2, 5)
    sleep_time = lo + beta_sample * (hi - lo)

    # ~8% chance of an extra human-like pause (4–6s extra)
    if random.random() < 0.08:
        sleep_time += random.uniform(4.0, 6.0)

    time.sleep(sleep_time)
