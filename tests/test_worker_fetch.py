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


# ---------------------------------------------------------------------------
# Plan-Wechsel bei abgelehntem Proxy-Zugang
# ---------------------------------------------------------------------------

from orchestrator import proxy_manager as pm_mod


@pytest.fixture
def di_pm(monkeypatch, tmp_path) -> ProxyManager:
    monkeypatch.setattr(pm_mod, "FALLBACK_MARKER", tmp_path / "proxy_fallback_active")
    monkeypatch.setattr(pm_mod, "_fallback_active", None)
    monkeypatch.setenv("DI_USER", "plan1__cr.de")
    monkeypatch.setenv("DI_PW", "pw1")
    monkeypatch.setenv("DI_FB_LOGIN", "plan2")
    monkeypatch.setenv("DI_FB_PW", "pw2")
    pool = tmp_path / "gw.txt"
    pool.write_text("gw.example.com:823\n")
    return ProxyManager(username_env="DI_USER", password_env="DI_PW", pool_file=pool,
                        fallback_login_env="DI_FB_LOGIN", fallback_password_env="DI_FB_PW")


def _gateway(monkeypatch, accepted_login: str | None) -> list:
    """requests.get stubben: der Gateway akzeptiert nur accepted_login."""
    seen = []

    def fake_get(url, headers=None, timeout=None, proxies=None):
        seen.append(proxies["https"])
        if accepted_login is None or f"//{accepted_login}__" not in proxies["https"]:
            raise worker.requests.exceptions.ProxyError(
                "Unable to connect to proxy: Tunnel connection failed: 407 NO_USER")
        return _Resp()

    monkeypatch.setattr(worker.requests, "get", fake_get)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    return seen


class TestPlanSwitch:
    def test_switches_to_plan2_without_losing_the_request(self, monkeypatch, di_pm):
        seen = _gateway(monkeypatch, accepted_login="plan2")

        html, _ = worker._fetch(123, "2026-06-01", _PROFILE, di_pm)  # max_retries=1

        assert html == _Resp.text
        assert "plan1__cr.de" in seen[0] and "plan2__cr.de" in seen[1]
        assert di_pm.using_fallback()

    def test_gives_up_when_plan2_rejected_too(self, monkeypatch, di_pm):
        seen = _gateway(monkeypatch, accepted_login=None)

        html, _ = worker._fetch(123, "2026-06-01", {**_PROFILE, "max_retries": 3}, di_pm)

        assert html is None
        assert len(seen) == 2, "nach abgelehntem Fallback nicht weiter probieren"
