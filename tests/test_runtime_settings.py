"""Tests for orchestrator.runtime_settings — Merge-Logik YAML + Runtime-Overrides."""

import pytest

from orchestrator import runtime_settings as rs
from orchestrator.profile_manager import ProfileManager

_YAML = """
active_profile: normal
profiles:
  normal:    {base_wait_seconds: 3.0}
  aggressive: {base_wait_seconds: 1.0}
concurrency:
  datacenter_threads:
  - {id: dc_uk, label: DC-UK, slot: 101, enabled: true,
     active_hours: [8, 21], max_hours: null, timezone: Europe/London}
  - {id: dc_us, label: DC-US, slot: 102, enabled: false,
     active_hours: [7, 23], max_hours: null, timezone: America/New_York}
  worker_slots:
  - {slot: 0, enabled: false, profile: normal, max_hours: null}
"""


@pytest.fixture
def paths(tmp_path, monkeypatch):
    """Frische YAML + leere Runtime-Settings pro Test."""
    yaml_path = tmp_path / "profiles.yaml"
    yaml_path.write_text(_YAML)
    monkeypatch.setattr(rs, "SETTINGS_PATH", tmp_path / "runtime_settings.json")
    return yaml_path


class TestEffectiveConcurrency:
    def test_no_runtime_file_returns_yaml_values(self, paths):
        cfg = rs.effective_concurrency(paths)
        threads = {t["id"]: t for t in cfg["datacenter_threads"]}
        assert threads["dc_uk"]["enabled"] is True
        assert threads["dc_us"]["enabled"] is False
        assert cfg["worker_slots"][0]["profile"] == "normal"

    def test_override_wins_over_yaml(self, paths):
        rs.update_dc_thread("dc_uk", enabled=False, active_hours=[10, 18])
        rs.update_worker_slot(0, enabled=True, profile="aggressive")
        cfg = rs.effective_concurrency(paths)
        threads = {t["id"]: t for t in cfg["datacenter_threads"]}
        assert threads["dc_uk"]["enabled"] is False
        assert threads["dc_uk"]["active_hours"] == [10, 18]
        assert threads["dc_uk"]["timezone"] == "Europe/London"  # Topologie bleibt
        assert threads["dc_us"]["enabled"] is False             # unberührt
        assert cfg["worker_slots"][0]["enabled"] is True
        assert cfg["worker_slots"][0]["profile"] == "aggressive"

    def test_partial_override_keeps_other_fields(self, paths):
        rs.update_dc_thread("dc_uk", max_hours=4.0)
        threads = {t["id"]: t for t in rs.effective_concurrency(paths)["datacenter_threads"]}
        assert threads["dc_uk"]["max_hours"] == 4.0
        assert threads["dc_uk"]["enabled"] is True  # YAML-Wert unangetastet

    def test_missing_yaml_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(rs, "SETTINGS_PATH", tmp_path / "runtime_settings.json")
        assert rs.effective_concurrency(tmp_path / "nope.yaml") == {}


class TestUpdateValidation:
    def test_rejects_non_mutable_dc_field(self, paths):
        with pytest.raises(ValueError, match="Nicht laufzeit-änderbar"):
            rs.update_dc_thread("dc_uk", pool_file="hacked.txt")

    def test_rejects_non_mutable_slot_field(self, paths):
        with pytest.raises(ValueError):
            rs.update_worker_slot(0, badge_color="red")


class TestDcThreadEnabled:
    def test_yaml_fallback(self, paths):
        assert rs.dc_thread_enabled("dc_uk", paths) is True
        assert rs.dc_thread_enabled("dc_us", paths) is False

    def test_override(self, paths):
        rs.update_dc_thread("dc_uk", enabled=False)
        assert rs.dc_thread_enabled("dc_uk", paths) is False

    def test_unknown_thread_defaults_true(self, paths):
        assert rs.dc_thread_enabled("dc_nope", paths) is True


class TestActiveProfile:
    def test_profile_manager_uses_override(self, paths):
        pm = ProfileManager(path=paths)
        assert pm.get_active()["name"] == "normal"
        pm.set_active("aggressive")
        assert pm.get_active()["name"] == "aggressive"
        # YAML wurde dabei nicht angefasst (read-only-Verhalten)
        assert "active_profile: normal" in paths.read_text()

    def test_set_active_rejects_unknown(self, paths):
        pm = ProfileManager(path=paths)
        with pytest.raises(ValueError):
            pm.set_active("warp_speed")
