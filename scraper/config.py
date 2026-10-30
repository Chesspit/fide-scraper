from pathlib import Path

import yaml

# .env-Laden und DB-URL liegen seit Phase 2 in core.config; Re-Export für
# bestehende Aufrufer (scraper.config.get_database_url).
from core.config import PROJECT_ROOT, get_database_url  # noqa: F401

_DEFAULT_CONFIG = {
    "scraper": {
        "rate_limit": {"min_sleep": 1.2, "max_sleep": 2.5},
        "backfill_rate_limit": {"min_sleep": 2.0, "max_sleep": 4.0},
        "retry": {"max_attempts": 3, "backoff_base": 4},
        "timeout": 15,
    },
    "groups": {
        "female_top": {
            "sex": "F",
            "min_rating": 2400,
            "max_rating": 2600,
            "sample_size": "all",
        },
        "male_control": {
            "sex": "M",
            "min_rating": 2400,
            "max_rating": 2600,
            "sample_size": 130,
            "sampling": {"method": "age_matched", "seed": 42},
        },
    },
    "periods": {"mode": "latest"},
    "data": {"players_file": "data/players_list_foa_2026-04.txt"},
}


def _deep_merge(base: dict, override: dict) -> dict:
    """Recursively merge override into base, returning a new dict."""
    merged = base.copy()
    for key, value in override.items():
        if key in merged and isinstance(merged[key], dict) and isinstance(value, dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


def load_config(config_path: Path | None = None) -> dict:
    """Load config from YAML file, merged with defaults.

    Precedence: CLI overrides > config.yaml > defaults.
    Secrets (DATABASE_URL etc.) come from .env / environment variables.
    """
    if config_path is None:
        config_path = PROJECT_ROOT / "config.yaml"

    file_config = {}
    if config_path.exists():
        with open(config_path) as f:
            file_config = yaml.safe_load(f) or {}

    return _deep_merge(_DEFAULT_CONFIG, file_config)


# Singleton: loaded once on import
config = load_config()
