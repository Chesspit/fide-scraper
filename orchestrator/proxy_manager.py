"""Generic rotating-proxy manager (provider-agnostic).

Two modes, chosen by what's configured:

1. Pool mode (current default, Webshare): a `pool_file` lists many
   `IP:PORT` pairs sharing one username/password. get_proxy() picks a random
   entry per call — this is how we get IP diversity from a provider that
   sells a static IP list rather than a single rotating gateway.
2. Single-host mode (legacy / any provider with a real rotating gateway):
   pass host_override (+ optionally PROXY_PORT) and every call reuses that
   one host:port; the provider itself handles IP rotation behind it.

Country targeting (single-host mode only): embed ISO-2 code in username as
USERNAME-resi-CC. Not currently called anywhere with country= — kept as an
optional feature for providers whose username-suffix syntax matches.
"""

import os
import random
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")


class ProxyManager:
    def __init__(
        self,
        username_env: str = "PROXY_USERNAME",
        password_env: str = "PROXY_PASSWORD",
        host_override: str | None = None,
        pool_file: str | Path | None = None,
    ):
        self._user = os.getenv(username_env, "")
        self._pw   = os.getenv(password_env, "")
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()  # thread-safe cooldown state + pool access

        pool_path = pool_file or os.getenv("PROXY_POOL_FILE")
        self._pool: list[tuple[str, str]] = []
        if pool_path:
            self._pool = self._load_pool(Path(pool_path))

        self._host = host_override or os.getenv("PROXY_HOST")
        self._port = os.getenv("PROXY_PORT", "1010")

    @staticmethod
    def _load_pool(path: Path) -> list[tuple[str, str]]:
        """Parse `IP:PORT` lines (one per proxy), '#' comments allowed."""
        if not path.is_absolute():
            path = Path(__file__).resolve().parent.parent / path
        if not path.exists():
            return []
        entries = []
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            host, _, port = line.partition(":")
            if host and port:
                entries.append((host, port))
        return entries

    def get_proxy(self, country: str | None = None) -> dict | None:
        """Return a requests-compatible proxy dict, or None during cooldown.

        Args:
            country: ISO-3166 alpha-2 country code for geo-targeting (e.g. "DE").
                     Only used in single-host mode; ignored in pool mode.
        Thread-safe: multiple scraper threads may call this concurrently.
        """
        with self._lock:
            if time.time() < self._cooldown_until:
                return None
            if self._pool:
                host, port = random.choice(self._pool)
            elif self._host:
                host, port = self._host, self._port
            else:
                return None

        if not self._user or not self._pw:
            return None

        user = f"{self._user}-resi-{country.upper()}" if (country and not self._pool) else self._user
        url = f"http://{user}:{self._pw}@{host}:{port}"
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
