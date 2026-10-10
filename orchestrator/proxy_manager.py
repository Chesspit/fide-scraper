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

Plan-Wechsel (Fallback-Zugangsdaten): Ist ein Fallback konfiguriert (bei
DataImpulse: Login + Passwort eines zweiten Plans), schalten ALLE ProxyManager
gemeinsam um, sobald der Gateway den Zugang ablehnt (Tunnel-Fehler 4xx, z. B.
„407 NO_USER“, wenn Plan 1 aufgebraucht ist). Der Wechsel wird als Marker-Datei
im Datenverzeichnis festgehalten und überlebt so Worker-Neustarts. Zurück auf
die Primär-Zugangsdaten: Marker löschen + Worker neu erzeugen.
"""

import logging
import os
import random
import re
import threading
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

logger = logging.getLogger("orchestrator.proxy_manager")

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
FALLBACK_MARKER = _DATA_DIR / "proxy_fallback_active"

# Der Gateway selbst lehnt den CONNECT ab (Zugang/Guthaben) — FIDE-Antworten
# kommen erst nach einem erfolgreichen Tunnel und landen nie in diesem Text.
_TUNNEL_REJECTED = re.compile(r"Tunnel connection failed: 4\d\d")

_fallback_lock = threading.Lock()
_fallback_active: bool | None = None  # None = Marker noch nicht gelesen


def is_proxy_auth_failure(exc: BaseException) -> bool:
    """True, wenn der Proxy-Gateway den Zugang abgelehnt hat (z. B. 407 NO_USER)."""
    return bool(_TUNNEL_REJECTED.search(str(exc)))


def fallback_active() -> bool:
    """Ist der Plan-Wechsel aktiv? (prozessweit, gespeist aus der Marker-Datei)"""
    global _fallback_active
    with _fallback_lock:
        if _fallback_active is None:
            _fallback_active = FALLBACK_MARKER.exists()
        return _fallback_active


def _activate_fallback(reason: str) -> None:
    global _fallback_active
    with _fallback_lock:
        if _fallback_active:
            return
        _fallback_active = True
        try:
            FALLBACK_MARKER.write_text(f"{time.strftime('%Y-%m-%dT%H:%M:%S')} {reason}\n")
        except OSError as exc:
            logger.error("Marker %s nicht schreibbar (%s) — Wechsel gilt nur bis zum Neustart",
                         FALLBACK_MARKER, exc)
    logger.warning("PROXY-PLANWECHSEL: Primär-Zugang abgelehnt (%s) — alle Threads nutzen "
                   "ab jetzt die Fallback-Zugangsdaten. Marker: %s", reason, FALLBACK_MARKER)


class ProxyManager:
    # Mindestabstand zwischen zwei mtime-Checks der Pool-Datei (Sekunden).
    # Hot-Reload: nach einem IP-Tausch bei Webshare genügt es, die Pool-Datei
    # zu synchronisieren — kein Worker-Neustart mehr nötig.
    POOL_RELOAD_INTERVAL = 30.0
    def __init__(
        self,
        username_env: str = "PROXY_USERNAME",
        password_env: str = "PROXY_PASSWORD",
        host_override: str | None = None,
        pool_file: str | Path | None = None,
        fallback_login_env: str | None = None,
        fallback_password_env: str | None = None,
    ):
        self._user = os.getenv(username_env, "")
        self._pw   = os.getenv(password_env, "")
        # Fallback-Login ersetzt nur den Login-Teil; ein Länder-Suffix
        # (DataImpulse: LOGIN__cr.de) wird vom Primär-Usernamen übernommen.
        fb_login = os.getenv(fallback_login_env, "") if fallback_login_env else ""
        self._fb_pw = os.getenv(fallback_password_env, "") if fallback_password_env else ""
        _, sep, suffix = self._user.partition("__")
        self._fb_user = f"{fb_login}{sep}{suffix}" if fb_login else ""
        self._cooldown_until: float = 0.0
        self._lock = threading.Lock()  # thread-safe cooldown state + pool access

        pool_path = pool_file or os.getenv("PROXY_POOL_FILE")
        self._pool: list[tuple[str, str]] = []
        self._pool_path: Path | None = None
        self._pool_mtime: float = 0.0
        self._pool_checked: float = 0.0
        if pool_path:
            p = Path(pool_path)
            if not p.is_absolute():
                p = Path(__file__).resolve().parent.parent / p
            self._pool_path = p
            self._pool = self._load_pool(p)
            try:
                self._pool_mtime = p.stat().st_mtime
            except OSError:
                pass

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
            self._maybe_reload_pool()
            if self._pool:
                host, port = random.choice(self._pool)
            elif self._host:
                host, port = self._host, self._port
            else:
                return None

        base_user, pw = (self._fb_user, self._fb_pw) if self.using_fallback() else (self._user, self._pw)
        if not base_user or not pw:
            return None

        user = f"{base_user}-resi-{country.upper()}" if (country and not self._pool) else base_user
        url = f"http://{user}:{pw}@{host}:{port}"
        return {"http": url, "https": url}

    @property
    def has_fallback(self) -> bool:
        return bool(self._fb_user and self._fb_pw)

    def using_fallback(self) -> bool:
        return self.has_fallback and fallback_active()

    def switch_to_fallback(self, reason: str) -> bool:
        """Auf die Fallback-Zugangsdaten wechseln (für alle Threads).

        False, wenn kein Fallback konfiguriert ist — dann gibt es nichts zu wechseln.
        """
        if not self.has_fallback:
            return False
        _activate_fallback(reason)
        return True

    def _maybe_reload_pool(self) -> None:
        """Hot-Reload: Pool-Datei bei geänderter mtime neu einlesen.

        Muss mit gehaltenem self._lock aufgerufen werden. Höchstens alle
        POOL_RELOAD_INTERVAL Sekunden ein stat()-Call; ein leeres Parse-
        Ergebnis (z.B. halb geschriebene Datei mitten im Sync) ersetzt den
        bestehenden Pool bewusst NICHT.
        """
        if self._pool_path is None:
            return
        now = time.time()
        if now - self._pool_checked < self.POOL_RELOAD_INTERVAL:
            return
        self._pool_checked = now
        try:
            mtime = self._pool_path.stat().st_mtime
        except OSError:
            return  # Datei (vorübergehend) weg — bestehenden Pool behalten
        if mtime == self._pool_mtime:
            return
        new_pool = self._load_pool(self._pool_path)
        if new_pool:
            logger.info("Pool-Datei %s neu geladen: %d Einträge (vorher %d)",
                        self._pool_path.name, len(new_pool), len(self._pool))
            self._pool = new_pool
            self._pool_mtime = mtime
        else:
            logger.warning("Pool-Datei %s geändert, aber leer/unlesbar — behalte %d alte Einträge",
                           self._pool_path.name, len(self._pool))

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
