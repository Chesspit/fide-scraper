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
