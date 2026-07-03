"""Kanonische I/O für worker_state.json — von Worker UND Dashboard genutzt.

Vor der Extraktion (Review #6) hatte app.py eigene, NICHT-atomare Kopien von
read/write_worker_state (Path.write_text trunkiert vor dem Schreiben — ein
konkurrierender Reader sieht kurz eine leere Datei und fällt auf
{"command": "stopped"} zurück; genau dieses Race hatte früher frisch
gestartete Threads sofort sterben lassen, siehe Startup-Grace in worker.py).
Jetzt gibt es genau eine Implementierung: atomar (tmp + rename), gelockt.
"""

import json
import os
import threading
from pathlib import Path

_DATA_DIR = Path(os.getenv("ORCHESTRATOR_DATA_DIR", Path(__file__).resolve().parent))
WORKER_STATE_PATH = _DATA_DIR / "worker_state.json"

# Lock protecting all read-modify-write operations on worker_state.json
_state_lock = threading.Lock()


def _write_state_atomic(data: dict) -> None:
    """Write state dict to worker_state.json via a temp file + atomic rename.

    Path.write_text() truncates the file before writing; any concurrent read
    during that window sees an empty file and falls back to {"command": "stopped"},
    which can cause freshly-started threads to stop immediately. Using rename(2)
    (atomic on POSIX) eliminates that window entirely.
    """
    content = json.dumps(data, indent=2)
    tmp = WORKER_STATE_PATH.with_suffix(".tmp")
    tmp.write_text(content)
    tmp.replace(WORKER_STATE_PATH)  # atomic on POSIX


def read_worker_state() -> dict:
    """Return the full worker_state.json as a dict."""
    try:
        return json.loads(WORKER_STATE_PATH.read_text())
    except Exception:
        return {}


def read_command() -> str:
    """Return the current command from worker_state.json ('run'/'pause'/'stopped')."""
    return read_worker_state().get("command", "stopped")


def write_state(command: str | None = None, **extra) -> None:
    """Update worker_state.json, preserving existing keys (partial update).
    Thread-safe: acquires _state_lock before read-modify-write.
    Uses atomic rename to avoid truncation-window race with concurrent readers.
    """
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        if command is not None:
            data["command"] = command
        for k, v in extra.items():
            if v is not None or k in data:
                data[k] = v
        _write_state_atomic(data)


def update_thread_slot(slot: int, **kwargs) -> None:
    """Update the state entry for a specific thread slot (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        threads = data.setdefault("threads", [])
        entry = next((t for t in threads if t.get("slot") == slot), None)
        if entry is None:
            entry = {"slot": slot}
            threads.append(entry)
        entry.update(kwargs)
        data["threads"] = sorted(threads, key=lambda t: t.get("slot", 0))
        _write_state_atomic(data)


def clear_thread_slot(slot: int) -> None:
    """Remove a thread slot from the threads list (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        data["threads"] = [t for t in data.get("threads", []) if t.get("slot") != slot]
        _write_state_atomic(data)


def increment_global_stats(mb_group: float) -> None:
    """Atomically increment groups_done and mb_downloaded (thread-safe)."""
    with _state_lock:
        try:
            data = json.loads(WORKER_STATE_PATH.read_text()) if WORKER_STATE_PATH.exists() else {}
        except Exception:
            data = {}
        data["groups_done"] = data.get("groups_done", 0) + 1
        data["mb_downloaded"] = round(data.get("mb_downloaded", 0.0) + mb_group, 2)
        _write_state_atomic(data)
