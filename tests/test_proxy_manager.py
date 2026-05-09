"""Tests for orchestrator.proxy_manager."""

import time
import pytest
from unittest.mock import patch

from orchestrator.proxy_manager import ProxyJetManager


@pytest.fixture
def pm():
    """ProxyJetManager with known credentials (no real network calls)."""
    with patch.dict("os.environ", {
        "PROXYJET_USERNAME": "testuser",
        "PROXYJET_PASSWORD": "testpass",
        "PROXYJET_HOST": "proxy-jet.io",
        "PROXYJET_PORT": "1010",
    }):
        yield ProxyJetManager()


class TestGetProxy:
    def test_returns_dict_without_country(self, pm):
        result = pm.get_proxy()
        assert result == {
            "http":  "http://testuser:testpass@proxy-jet.io:1010",
            "https": "http://testuser:testpass@proxy-jet.io:1010",
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
            "PROXYJET_USERNAME": "",
            "PROXYJET_PASSWORD": "",
        }):
            manager = ProxyJetManager()
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
