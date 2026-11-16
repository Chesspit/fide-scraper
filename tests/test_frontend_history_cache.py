"""Cache der Spielerliste für /c (frontend/data.py::load_2700_history).

Hintergrund: Nach einem Frontend-Neustart brauchte die DB-Abfrage am 16.11.2026
einmal 44 s — die Seite blieb so lange leer. Der Verlauf liegt deshalb als
Parquet-Datei vor und wird im Hintergrund erneuert. Kein DB-Zugriff: _fetch
wird ersetzt.
"""

import importlib.util
import os
import time
from pathlib import Path

import pandas as pd
import pytest

pytest.importorskip("pyarrow")

DATA_PY = Path(__file__).resolve().parent.parent / "frontend" / "data.py"


def _frame(rating=2750):
    return pd.DataFrame({
        "fide_id": [1503014, 1503014], "name": ["Carlsen, Magnus"] * 2,
        "federation": ["NOR"] * 2, "birth_year": [1990, 1990],
        "period": ["2024-01-01", "2024-02-01"], "rating": [rating, rating + 5],
    })


@pytest.fixture
def data(tmp_path, monkeypatch):
    spec = importlib.util.spec_from_file_location("frontend_data_under_test", DATA_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "HISTORY_CACHE_PATH", str(tmp_path / "history2700_cache.parquet"))
    mod.calls = []

    def fake_fetch(sql, params=None):
        mod.calls.append(sql)
        if "DISTINCT fide_id" in sql:
            return pd.DataFrame({"fide_id": [1503014]})
        return _frame(mod.rating)

    mod.rating = 2750
    monkeypatch.setattr(mod, "_fetch", fake_fetch)
    return mod


def _wait_refresh(mod, timeout=5.0):
    end = time.time() + timeout
    while time.time() < end:
        if mod._refresh_lock.acquire(blocking=False):
            mod._refresh_lock.release()
            if len(mod.calls) >= 2:
                return
        time.sleep(0.02)
    raise AssertionError("Hintergrund-Aktualisierung lief nicht")


def test_first_call_queries_db_and_writes_cache(data):
    df = data.load_2700_history()
    assert len(df) == 2 and len(data.calls) == 2
    assert os.path.exists(data.HISTORY_CACHE_PATH)
    assert str(df["period"].dtype).startswith("datetime64")
    data.load_2700_history()
    assert len(data.calls) == 2          # zweiter Aufruf aus dem Speicher


def test_fresh_process_serves_file_without_db(data):
    data.load_2700_history()
    data._cache.clear(); data.calls.clear()      # „Neustart“
    df = data.load_2700_history()
    assert len(df) == 2 and data.calls == []
    assert str(df["period"].dtype).startswith("datetime64")


def test_stale_file_is_served_then_refreshed_in_background(data):
    data.load_2700_history()
    old = time.time() - data.HISTORY_CACHE_MAX_AGE - 60
    os.utime(data.HISTORY_CACHE_PATH, (old, old))
    data._cache.clear(); data.calls.clear()
    data.rating = 2800                           # „neue Liste“ in der DB
    df = data.load_2700_history()
    assert df["rating"].max() == 2755            # sofort der alte Stand, kein Warten
    _wait_refresh(data)
    assert data.load_2700_history()["rating"].max() == 2805
    assert pd.read_parquet(data.HISTORY_CACHE_PATH)["rating"].max() == 2805


def test_failed_refresh_keeps_old_data(data, monkeypatch):
    data.load_2700_history()
    old = time.time() - data.HISTORY_CACHE_MAX_AGE - 60
    os.utime(data.HISTORY_CACHE_PATH, (old, old))
    data._cache.clear()

    def boom(sql, params=None):
        data.calls.append(sql)
        raise RuntimeError("canceling statement due to statement timeout")

    monkeypatch.setattr(data, "_fetch", boom)
    data.calls.clear()
    df = data.load_2700_history()
    assert len(df) == 2
    end = time.time() + 5
    while not data.calls and time.time() < end:
        time.sleep(0.02)
    time.sleep(0.1)
    n = len(data.calls)
    assert len(data.load_2700_history()) == 2    # alter Stand bleibt
    time.sleep(0.1)
    assert len(data.calls) == n                  # kein Dauerfeuer auf die DB


def test_read_only_folder_still_works(data, tmp_path, monkeypatch):
    """Öffentliche Edition: Code-Ordner nur lesend eingehängt."""
    monkeypatch.setattr(data, "HISTORY_CACHE_PATH", str(tmp_path / "fehlt" / "x.parquet"))
    df = data.load_2700_history()
    assert len(df) == 2 and not os.path.exists(data.HISTORY_CACHE_PATH)


def test_cache_with_missing_columns_is_ignored(data):
    pd.DataFrame({"fide_id": [1]}).to_parquet(data.HISTORY_CACHE_PATH, index=False)
    df = data.load_2700_history()
    assert len(df) == 2 and len(data.calls) == 2
