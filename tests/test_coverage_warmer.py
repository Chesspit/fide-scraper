"""Vorberechnung des Abdeckungs-Tabs (store.start_coverage_warmer) ohne DB."""

import time

import pytest

from orchestrator import store


@pytest.fixture
def fake_compute(monkeypatch):
    calls = []

    def compute(key):
        calls.append(key)
        return {"k": key} if key[0] == "totals" else [{"k": key}]

    monkeypatch.setattr(store, "_coverage_compute", compute)
    monkeypatch.setattr(store, "_coverage_cache", {})
    monkeypatch.setattr(store, "_coverage_cache_ts", {})
    return calls


def _year():
    return time.localtime().tm_year


def test_warm_key_is_never_computed_on_click(fake_compute):
    assert store.query_coverage("band", 2020, _year()) == []
    assert fake_compute == []
    assert store.coverage_stand("band", 2020, _year()) is None


def test_warm_once_fills_all_default_keys(fake_compute):
    store.warm_coverage_once()
    assert len(fake_compute) == 1 + len(store.COVERAGE_DIMENSIONS)
    assert store.query_coverage("federation", 2020, _year()) == [
        {"k": ("rows", "federation", 2020, _year())}]
    assert store.query_coverage_totals(2020, _year())["k"][0] == "totals"
    assert len(fake_compute) == 1 + len(store.COVERAGE_DIMENSIONS)  # nur Cache gelesen


def test_warm_key_served_even_when_old(fake_compute):
    store.warm_coverage_once()
    key = ("rows", "band", 2020, _year())
    store._coverage_cache_ts[key] -= 10 * store._COVERAGE_TTL
    n = len(fake_compute)
    assert store.query_coverage("band", 2020, _year())
    assert len(fake_compute) == n


def test_other_range_computed_on_demand_and_cached(fake_compute):
    store.query_coverage("band", 2015, 2016)
    store.query_coverage("band", 2015, 2016)
    assert fake_compute == [("rows", "band", 2015, 2016)]
    key = ("rows", "band", 2015, 2016)
    store._coverage_cache_ts[key] -= store._COVERAGE_TTL + 1
    store.query_coverage("band", 2015, 2016)
    assert len(fake_compute) == 2


def test_failure_keeps_last_value(fake_compute, monkeypatch):
    store.query_coverage("band", 2015, 2016)
    key = ("rows", "band", 2015, 2016)
    store._coverage_cache_ts[key] -= store._COVERAGE_TTL + 1

    def boom(_key):
        raise RuntimeError("tunnel down")

    monkeypatch.setattr(store, "_coverage_compute", boom)
    assert store.query_coverage("band", 2015, 2016) == [{"k": key}]


def test_start_coverage_warmer_runs_in_background(fake_compute, monkeypatch):
    monkeypatch.setattr(store, "COVERAGE_WARM_INTERVAL", 3600)
    store.start_coverage_warmer()   # darf nicht blockieren oder werfen
    for _ in range(50):
        if len(fake_compute) >= 1 + len(store.COVERAGE_DIMENSIONS):
            break
        time.sleep(0.02)
    assert len(fake_compute) >= 1 + len(store.COVERAGE_DIMENSIONS)
