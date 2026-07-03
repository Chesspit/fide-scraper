"""Load and hot-swap scrape profiles from profiles.yaml.

The active profile can be changed at runtime (e.g. from the dashboard)
without restarting the worker — just call set_active() and the next
get_active() call returns the updated profile.
"""

import os
import random
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parent.parent / ".env", override=False)

PROFILES_PATH = Path(os.getenv("ORCHESTRATOR_PROFILES", str(Path(__file__).resolve().parent / "profiles.yaml")))


class ProfileManager:
    def __init__(self, path: Path = PROFILES_PATH):
        self._path = path
        self._data: dict = {}
        self._load()

    def _load(self) -> None:
        with open(self._path, encoding="utf-8") as f:
            self._data = yaml.safe_load(f)

    def get_active(self) -> dict[str, Any]:
        """Return the currently active profile dict.

        Ein per Dashboard gesetzter Override (runtime_settings.json) gewinnt
        über den active_profile-Default aus profiles.yaml — die YAML selbst
        wird seit Review #4 nie mehr umgeschrieben (read-only-Mount).
        """
        from orchestrator import runtime_settings
        name = runtime_settings.active_profile_override() \
            or self._data.get("active_profile", "normal")
        profiles = self._data.get("profiles", {})
        if name not in profiles:
            raise ValueError(f"Unknown profile: {name!r}")
        return {"name": name, **profiles[name]}

    def set_active(self, name: str) -> None:
        """Switch the active profile (persisted in runtime_settings.json)."""
        if name not in self._data.get("profiles", {}):
            raise ValueError(f"Unknown profile: {name!r}. "
                             f"Available: {self.available()}")
        from orchestrator import runtime_settings
        runtime_settings.set_value("active_profile", name)

    def available(self) -> list[str]:
        return list(self._data.get("profiles", {}).keys())

    def dashboard_settings(self) -> dict:
        """[dashboard]-Sektion aus profiles.yaml (Darstellungs-Konstanten)."""
        return self._data.get("dashboard", {}) or {}

    def all_profiles(self) -> dict[str, dict]:
        return self._data.get("profiles", {})

    def pick_fuzzy(self, override: str | None = None) -> dict[str, Any]:
        """Return a profile picked by fuzzy (weighted random) selection.

        Args:
            override: If set, use this specific profile instead of fuzzy picking.
                      Useful when a group has an explicit profile assignment.
        """
        if override and override in self._data.get("profiles", {}):
            profiles = self._data["profiles"]
            return {"name": override, **profiles[override]}

        weights_cfg = self._data.get("fuzzy_weights", {})
        available = self.available()
        weights = [max(0, weights_cfg.get(p, 1)) for p in available]
        name = random.choices(available, weights=weights, k=1)[0]
        profiles = self._data.get("profiles", {})
        return {"name": name, **profiles[name]}
