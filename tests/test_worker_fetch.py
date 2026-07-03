"""Tests for orchestrator.worker._fetch — 429-/Cooldown-Fallback-Verhalten.

Kern: auf Maschinen mit FIDE-geblockter IP (VPS) darf _fetch nie stillschweigend
direkt (ohne Proxy) fetchen — weder als expliziter 429-Fallback noch implizit,
wenn get_proxy() während eines Pool-Cooldowns None liefert.
Gesteuert über DIRECT_FALLBACK_ON_429 (Default true = altes Verhalten, Mac Mini).
"""

import pytest

from orchestrator import worker
from orchestrator.proxy_manager import ProxyManager


class _Resp:
    status_code = 200
    text = "<html>ok</html>"
    content = b"<html>ok</html>"
    headers: dict = {}

    def raise_for_status(self):
        pass


@pytest.fixture
def pm(monkeypatch, tmp_path) -> ProxyManager:
    """ProxyManager mit 1-Eintrag-Pool und Dummy-Credentials."""
    monkeypatch.setenv("PROXY_USERNAME", "testuser")
    monkeypatch.setenv("PROXY_PASSWORD", "testpass")
    pool = tmp_path / "pool.txt"
    pool.write_text("10.0.0.1:8080\n")
    return ProxyManager(pool_file=pool)


_PROFILE = {"max_retries": 1, "timeout_seconds": 5, "use_proxy": True}


def _capture_get(monkeypatch) -> list:
    """requests.get stubben; gesehene proxies-Argumente aufzeichnen."""
    seen = []

    def fake_get(url, headers=None, timeout=None, proxies=None):
        seen.append(proxies)
        return _Resp()

    monkeypatch.setattr(worker.requests, "get", fake_get)
    return seen


class TestDirectFallbackFlag:
    @pytest.mark.parametrize("value,expected", [
        ("true", True), ("1", True), ("yes", True), ("", True),
        ("false", False), ("0", False), ("no", False), ("False", False),
    ])
    def test_env_parsing(self, monkeypatch, value, expected):
        monkeypatch.setenv("DIRECT_FALLBACK_ON_429", value)
        assert worker._direct_fallback_on_429() is expected

    def test_default_is_true(self, monkeypatch):
        monkeypatch.delenv("DIRECT_FALLBACK_ON_429", raising=False)
        assert worker._direct_fallback_on_429() is True


class TestFetchCooldownBehaviour:
    def test_waits_for_cooldown_instead_of_direct(self, monkeypatch, pm):
        """Flag false + Pool im Cooldown → Cooldown aussitzen, dann MIT Proxy fetchen."""
        monkeypatch.setenv("DIRECT_FALLBACK_ON_429", "false")
        seen = _capture_get(monkeypatch)
        pm.report_block(cooldown_seconds=0.2)

        html, _ = worker._fetch(123, "2026-06-01", _PROFILE, pm)

        assert html == _Resp.text
        assert seen[0] is not None, "hat direkt gefetcht statt Cooldown auszusitzen"
        assert "10.0.0.1:8080" in seen[0]["http"]

    def test_goes_direct_during_cooldown_by_default(self, monkeypatch, pm):
        """Default (Flag true): Cooldown → direkter Fetch ohne Proxy (Mac-Mini-Verhalten)."""
        monkeypatch.delenv("DIRECT_FALLBACK_ON_429", raising=False)
        seen = _capture_get(monkeypatch)
        pm.report_block(cooldown_seconds=30)  # lang — darf NICHT abgewartet werden

        html, _ = worker._fetch(123, "2026-06-01", _PROFILE, pm)

        assert html == _Resp.text
        assert seen[0] is None, "hätte direkt (ohne Proxy) fetchen sollen"

    def test_uses_proxy_normally_without_cooldown(self, monkeypatch, pm):
        monkeypatch.setenv("DIRECT_FALLBACK_ON_429", "false")
        seen = _capture_get(monkeypatch)

        html, _ = worker._fetch(123, "2026-06-01", _PROFILE, pm)

        assert html == _Resp.text
        assert seen[0] is not None
