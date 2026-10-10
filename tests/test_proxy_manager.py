"""Tests for orchestrator.proxy_manager."""

import time
import pytest
from unittest.mock import patch

from orchestrator.proxy_manager import ProxyManager


@pytest.fixture
def pm():
    """ProxyManager in single-host mode (no real network calls)."""
    with patch.dict("os.environ", {
        "PROXY_USERNAME": "testuser",
        "PROXY_PASSWORD": "testpass",
        "PROXY_HOST": "proxy.example.com",
        "PROXY_PORT": "1010",
    }, clear=False):
        with patch.dict("os.environ", {"PROXY_POOL_FILE": ""}, clear=False):
            yield ProxyManager()


class TestGetProxy:
    def test_returns_dict_without_country(self, pm):
        result = pm.get_proxy()
        assert result == {
            "http":  "http://testuser:testpass@proxy.example.com:1010",
            "https": "http://testuser:testpass@proxy.example.com:1010",
        }

    def test_country_targeting_embeds_code_in_username(self, pm):
        result = pm.get_proxy(country="DE")
        assert "testuser-resi-DE" in result["http"]
        assert result["http"] == result["https"]

    def test_country_code_uppercased(self, pm):
        result = pm.get_proxy(country="de")
        assert "testuser-resi-DE" in result["http"]

    def test_returns_none_when_no_credentials(self):
        with patch.dict("os.environ", {
            "PROXY_USERNAME": "",
            "PROXY_PASSWORD": "",
            "PROXY_HOST": "proxy.example.com",
        }, clear=False):
            manager = ProxyManager()
            assert manager.get_proxy() is None

    def test_returns_none_during_cooldown(self, pm):
        pm.report_block(cooldown_seconds=60)
        assert pm.get_proxy() is None

    def test_returns_proxy_after_cooldown_expires(self, pm):
        pm.report_block(cooldown_seconds=0)
        time.sleep(0.01)
        assert pm.get_proxy() is not None


class TestCooldown:
    def test_is_cooling_down_after_block(self, pm):
        pm.report_block(cooldown_seconds=60)
        assert pm.is_cooling_down() is True

    def test_not_cooling_down_initially(self, pm):
        assert pm.is_cooling_down() is False

    def test_cooldown_remaining_positive_after_block(self, pm):
        pm.report_block(cooldown_seconds=60)
        assert pm.cooldown_remaining() > 0

    def test_cooldown_remaining_zero_initially(self, pm):
        assert pm.cooldown_remaining() == 0.0

    def test_cooldown_remaining_decreases_over_time(self, pm):
        pm.report_block(cooldown_seconds=5)
        r1 = pm.cooldown_remaining()
        time.sleep(0.05)
        r2 = pm.cooldown_remaining()
        assert r2 < r1


class TestPoolMode:
    """Pool mode: many IP:PORT entries sharing one credential pair (Webshare)."""

    @pytest.fixture
    def pool_file(self, tmp_path):
        f = tmp_path / "pool.txt"
        f.write_text("# comment line, ignored\n1.2.3.4:1000\n5.6.7.8:2000\n9.9.9.9:3000\n")
        return f

    @pytest.fixture
    def pool_pm(self, pool_file):
        with patch.dict("os.environ", {
            "PROXY_USERNAME": "testuser",
            "PROXY_PASSWORD": "testpass",
        }, clear=False):
            yield ProxyManager(pool_file=pool_file)

    def test_picks_entry_from_pool(self, pool_pm):
        result = pool_pm.get_proxy()
        assert result["http"] in {
            "http://testuser:testpass@1.2.3.4:1000",
            "http://testuser:testpass@5.6.7.8:2000",
            "http://testuser:testpass@9.9.9.9:3000",
        }
        assert result["http"] == result["https"]

    def test_pool_entries_vary_across_many_calls(self, pool_pm):
        seen = {pool_pm.get_proxy()["http"] for _ in range(50)}
        assert len(seen) > 1  # extremely unlikely to always pick the same of 3

    def test_comment_lines_ignored(self, pool_file, pool_pm):
        assert len(pool_pm._pool) == 3

    def test_missing_pool_file_falls_back_to_empty(self, tmp_path):
        with patch.dict("os.environ", {
            "PROXY_USERNAME": "testuser",
            "PROXY_PASSWORD": "testpass",
            "PROXY_HOST": "",
        }, clear=False):
            manager = ProxyManager(pool_file=tmp_path / "does_not_exist.txt")
            assert manager.get_proxy() is None


class TestPoolHotReload:
    """mtime-basiertes Hot-Reload: Pool-Datei-Sync ohne Worker-Neustart."""

    @pytest.fixture
    def pool_file(self, tmp_path):
        f = tmp_path / "pool.txt"
        f.write_text("1.2.3.4:1000\n")
        return f

    @pytest.fixture
    def pool_pm(self, pool_file, monkeypatch):
        monkeypatch.setattr(ProxyManager, "POOL_RELOAD_INTERVAL", 0.0)
        with patch.dict("os.environ", {
            "PROXY_USERNAME": "testuser",
            "PROXY_PASSWORD": "testpass",
        }, clear=False):
            yield ProxyManager(pool_file=pool_file)

    @staticmethod
    def _bump_mtime(path):
        import os
        st = path.stat()
        os.utime(path, (st.st_atime, st.st_mtime + 10))

    def test_reloads_on_mtime_change(self, pool_pm, pool_file):
        assert "1.2.3.4:1000" in pool_pm.get_proxy()["http"]
        pool_file.write_text("5.6.7.8:2000\n")
        self._bump_mtime(pool_file)
        assert "5.6.7.8:2000" in pool_pm.get_proxy()["http"]

    def test_no_reload_when_mtime_unchanged(self, pool_pm, pool_file):
        pool_pm.get_proxy()
        # Datei ändern, mtime künstlich auf den alten Wert zurücksetzen
        import os
        st = pool_file.stat()
        pool_file.write_text("5.6.7.8:2000\n")
        os.utime(pool_file, (st.st_atime, pool_pm._pool_mtime))
        assert "1.2.3.4:1000" in pool_pm.get_proxy()["http"]

    def test_empty_rewrite_keeps_old_pool(self, pool_pm, pool_file):
        """Halb geschriebene/leere Datei darf den Pool nicht wegreißen."""
        pool_file.write_text("# nur ein Kommentar, keine Einträge\n")
        self._bump_mtime(pool_file)
        assert "1.2.3.4:1000" in pool_pm.get_proxy()["http"]

    def test_deleted_file_keeps_old_pool(self, pool_pm, pool_file):
        pool_file.unlink()
        assert "1.2.3.4:1000" in pool_pm.get_proxy()["http"]

    def test_reload_check_throttled(self, pool_file, monkeypatch):
        """Innerhalb des Intervalls wird nicht erneut stat()/geparst."""
        monkeypatch.setattr(ProxyManager, "POOL_RELOAD_INTERVAL", 3600.0)
        with patch.dict("os.environ", {
            "PROXY_USERNAME": "testuser",
            "PROXY_PASSWORD": "testpass",
        }, clear=False):
            manager = ProxyManager(pool_file=pool_file)
        manager.get_proxy()  # setzt _pool_checked
        pool_file.write_text("5.6.7.8:2000\n")
        self._bump_mtime(pool_file)
        assert "1.2.3.4:1000" in manager.get_proxy()["http"]


# ---------------------------------------------------------------------------
# Plan-Wechsel (Fallback-Zugangsdaten)
# ---------------------------------------------------------------------------

from orchestrator import proxy_manager as pm_mod

_REJECTED = OSError("Unable to connect to proxy: Tunnel connection failed: 407 NO_USER")


@pytest.fixture
def fresh_fallback(monkeypatch, tmp_path):
    """Prozessweiten Fallback-Zustand isolieren (Marker im tmp-Verzeichnis)."""
    marker = tmp_path / "proxy_fallback_active"
    monkeypatch.setattr(pm_mod, "FALLBACK_MARKER", marker)
    monkeypatch.setattr(pm_mod, "_fallback_active", None)
    return marker


@pytest.fixture
def di_env(monkeypatch, tmp_path):
    monkeypatch.setenv("DI_USER", "plan1__cr.de")
    monkeypatch.setenv("DI_PW", "pw1")
    monkeypatch.setenv("DI_FB_LOGIN", "plan2")
    monkeypatch.setenv("DI_FB_PW", "pw2")
    pool = tmp_path / "gw.txt"
    pool.write_text("gw.example.com:823\n")
    return pool


def _di_manager(pool, with_fallback=True):
    kwargs = {"fallback_login_env": "DI_FB_LOGIN", "fallback_password_env": "DI_FB_PW"} \
        if with_fallback else {}
    return ProxyManager(username_env="DI_USER", password_env="DI_PW", pool_file=pool, **kwargs)


class TestPlanFallback:
    def test_detects_gateway_rejection(self):
        assert pm_mod.is_proxy_auth_failure(_REJECTED)
        assert not pm_mod.is_proxy_auth_failure(OSError("Read timed out"))
        assert not pm_mod.is_proxy_auth_failure(OSError("Tunnel connection failed: 502 Bad Gateway"))

    def test_uses_primary_until_switch(self, fresh_fallback, di_env):
        m = _di_manager(di_env)
        assert m.get_proxy()["https"] == "http://plan1__cr.de:pw1@gw.example.com:823"

    def test_switch_keeps_country_suffix_and_affects_all_managers(self, fresh_fallback, di_env):
        m1, m2 = _di_manager(di_env), _di_manager(di_env)
        assert m1.switch_to_fallback("407 NO_USER") is True
        assert m2.get_proxy()["https"] == "http://plan2__cr.de:pw2@gw.example.com:823"
        assert fresh_fallback.exists()

    def test_marker_survives_restart(self, fresh_fallback, di_env, monkeypatch):
        fresh_fallback.write_text("2026-10-10 test\n")
        assert _di_manager(di_env).using_fallback()

    def test_no_switch_without_fallback_config(self, fresh_fallback, di_env):
        m = _di_manager(di_env, with_fallback=False)
        assert m.switch_to_fallback("407") is False
        assert not fresh_fallback.exists()
        assert "plan1__cr.de:pw1" in m.get_proxy()["https"]
