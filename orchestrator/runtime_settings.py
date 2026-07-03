"""Laufzeit-Einstellungen des Orchestrators — getrennt von profiles.yaml.

Hintergrund (Architektur-Review 2026-07-03, Punkt #4): profiles.yaml vermischte
statische Konfiguration (Profile, fuzzy_weights, Thread-Topologie) mit
Laufzeit-State, den das Dashboard zur Laufzeit umschreibt (enabled-Flags,
active_hours, max_hours, active_profile). Folgen: Kommentare in der YAML
unmöglich (jeder UI-Klick schrieb die Datei neu), nicht-atomare Writes, und
die cp-n-Volume-Seeding-Landmine (Git-Version drifte von /data/profiles.yaml
weg).

Jetzt: profiles.yaml ist statisch (read-only gemountet, Git = Wahrheit),
alles Veränderliche lebt in runtime_settings.json im Daten-Volume — atomar
geschrieben (tmp + rename, wie worker_state.json), thread-sicher gelockt.

Struktur der Datei (alle Felder optional — fehlende Werte fallen auf die
Defaults aus profiles.yaml zurück, eine fehlende Datei ist der Normalzustand
nach frischem Deploy):

    {
      "active_profile": "normal",
      "dc_threads":   {"dc_uk": {"enabled": false, "active_hours": [8, 21],
                                  "max_hours": null}, ...},
      "worker_slots": {"0": {"enabled": true, "profile": "normal",
                              "max_hours": null}, ...}
    }

effective_concurrency() liefert die gemergte Sicht (YAML-Topologie +
Runtime-Overrides) und ist die einzige Quelle, aus der Worker und Dashboard
die concurrency-Konfiguration lesen sollten.
"""

import json
import os
import threading
from pathlib import Path
from typing import Any

import yaml

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
SETTINGS_PATH = _DATA_DIR / "runtime_settings.json"

_lock = threading.Lock()

# Felder, die pro DC-Thread bzw. Residential-Slot zur Laufzeit überschreibbar
# sind — alles andere (id, label, slot, federations, timezone, pool_file,
# username_env, ...) ist Topologie und bleibt allein in profiles.yaml.
DC_MUTABLE = ("enabled", "active_hours", "max_hours")
SLOT_MUTABLE = ("enabled", "profile", "max_hours")


def load() -> dict:
    """Return the raw settings dict ({} if file missing/unreadable)."""
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def _write_atomic(data: dict) -> None:
    content = json.dumps(data, indent=2)
    tmp = SETTINGS_PATH.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(SETTINGS_PATH)  # atomic on POSIX


def set_value(key: str, value: Any) -> None:
    """Set a top-level key (e.g. active_profile) atomically."""
    with _lock:
        data = load()
        data[key] = value
        _write_atomic(data)


def update_dc_thread(dc_id: str, **fields) -> None:
    """Merge mutable fields for one DC thread (e.g. enabled=False)."""
    _update_section("dc_threads", dc_id, DC_MUTABLE, fields)


def update_worker_slot(slot: int, **fields) -> None:
    """Merge mutable fields for one residential slot."""
    _update_section("worker_slots", str(slot), SLOT_MUTABLE, fields)


def _update_section(section: str, entry_key: str, allowed: tuple, fields: dict) -> None:
    unknown = set(fields) - set(allowed)
    if unknown:
        raise ValueError(f"Nicht laufzeit-änderbar: {sorted(unknown)} (erlaubt: {allowed})")
    with _lock:
        data = load()
        entry = data.setdefault(section, {}).setdefault(entry_key, {})
        entry.update(fields)
        _write_atomic(data)


# ---------------------------------------------------------------------------
# Merged view: profiles.yaml topology + runtime overrides
# ---------------------------------------------------------------------------

def effective_concurrency(profiles_path: Path | None = None) -> dict:
    """[concurrency] aus profiles.yaml, mit Runtime-Overrides gemergt.

    Liefert dieselbe Struktur wie das rohe YAML (datacenter_threads-Liste,
    worker_slots-Liste, ...), nur dass enabled/active_hours/max_hours/profile
    aus runtime_settings.json gewinnen, wo vorhanden. Fehler beim YAML-Lesen
    ergeben {} (wie bisher in worker._load_concurrency_config).
    """
    from orchestrator.profile_manager import PROFILES_PATH
    path = profiles_path or PROFILES_PATH
    try:
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f).get("concurrency", {}) or {}
    except Exception:
        return {}

    overrides = load()
    dc_over = overrides.get("dc_threads", {})
    slot_over = overrides.get("worker_slots", {})

    merged = dict(cfg)
    merged["datacenter_threads"] = [
        {**t, **{k: v for k, v in dc_over.get(t.get("id", ""), {}).items() if k in DC_MUTABLE}}
        for t in cfg.get("datacenter_threads", [])
    ]
    if "worker_slots" in cfg:
        merged["worker_slots"] = [
            {**s, **{k: v for k, v in slot_over.get(str(s.get("slot", "")), {}).items() if k in SLOT_MUTABLE}}
            for s in cfg.get("worker_slots", [])
        ]
    return merged


def dc_thread_enabled(dc_id: str, profiles_path: Path | None = None) -> bool:
    """Live-Check des enabled-Flags eines DC-Threads (Override vor YAML).

    True im Fehlerfall (safe default: weiterlaufen) — gleiche Semantik wie
    der frühere worker._read_dc_thread_enabled.
    """
    override = load().get("dc_threads", {}).get(dc_id, {})
    if "enabled" in override:
        return bool(override["enabled"])
    for t in effective_concurrency(profiles_path).get("datacenter_threads", []):
        if t.get("id") == dc_id:
            return bool(t.get("enabled", True))
    return True


def active_profile_override() -> str | None:
    """Per Dashboard gesetztes active_profile, oder None (→ YAML-Wert gilt)."""
    value = load().get("active_profile")
    return value if isinstance(value, str) and value else None
