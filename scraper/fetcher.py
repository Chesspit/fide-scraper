import logging
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


def fetch_calculations(fide_id: int, period_str: str) -> str:
    """Fetch the calculations HTML fragment for a player/period from FIDE.

    Args:
        fide_id: FIDE player ID
        period_str: Period string in format "YYYY-MM-01"

    Returns:
        HTML fragment string

    Raises:
        requests.HTTPError: After max retries exhausted
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

    last_exception = None
    for attempt in range(1, max_attempts + 1):
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            resp.raise_for_status()
            return resp.text
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
    """Sleep a random interval between requests to be polite."""
    if backfill:
        limits = config["scraper"]["backfill_rate_limit"]
    else:
        limits = config["scraper"]["rate_limit"]
    time.sleep(random.uniform(limits["min_sleep"], limits["max_sleep"]))
