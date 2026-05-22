"""ProxyJet rotating residential proxy manager.

Country targeting: embed ISO-2 code in username as USERNAME-resi-CC
Example: 260509r9eG8-resi-DE:PASSWORD@proxy-jet.io:1010
"""

import os
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class ProxyJetManager:
    def __init__(
        self,
        username_env: str = "PROXYJET_USERNAME",
        password_env: str = "PROXYJET_PASSWORD",
        host_override: str | None = None,
    ):
        self._user = os.getenv(username_env, "")
        self._pw   = os.getenv(password_env, "")
        self._host = host_override or os.getenv("PROXYJET_HOST", "proxy-jet.io")
        self._port = os.getenv("PROXYJET_PORT", "1010")
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()  # thread-safe cooldown state

    def get_proxy(self, country: str | None = None) -> dict | None:
        """Return a requests-compatible proxy dict, or None during cooldown.

        Args:
            country: ISO-3166 alpha-2 country code for geo-targeting (e.g. "DE").
                     If None, ProxyJet picks any country automatically.
        Thread-safe: multiple scraper threads may call this concurrently.
        """
        with self._lock:
            if time.time() < self._cooldown_until:
                return None

        if not self._user or not self._pw:
            return None

        user = f"{self._user}-resi-{country.upper()}" if country else self._user
        url = f"http://{user}:{self._pw}@{self._host}:{self._port}"
        return {"http": url, "https": url}

    def report_block(self, cooldown_seconds: int = 60) -> None:
        """Call on HTTP 429 or connection error to pause proxy use."""
        with self._lock:
            self._cooldown_until = time.time() + cooldown_seconds

    def is_cooling_down(self) -> bool:
        with self._lock:
            return time.time() < self._cooldown_until

    def cooldown_remaining(self) -> float:
        """Seconds remaining in cooldown (0 if not cooling down)."""
        with self._lock:
            return max(0.0, self._cooldown_until - time.time())
