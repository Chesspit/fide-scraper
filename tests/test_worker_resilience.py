"""Tests für die Worker-Härtung (Review F-06, F-15, F-20).

F-15  abgeschnittene Antworten werden wiederholt statt gespeichert
F-06  ein abgebrochener Thread startet neu; verwaiste Gruppen werden failed;
      nach einer Serie von Abbrüchen beendet sich der Prozess
F-20  nach HTTP 403 pausiert der Thread
"""

import threading

import pytest

from orchestrator import worker


class _Resp:
    status_code = 200
    headers: dict = {}

    def __init__(self, text: str):
        self.text = text
        self.content = text.encode()

    def raise_for_status(self):
        pass


def _serve(monkeypatch, bodies: list[str]) -> list:
    calls = []

    def fake_get(url, headers=None, timeout=None, proxies=None):
        calls.append(url)
        return _Resp(bodies[min(len(calls), len(bodies)) - 1])

    monkeypatch.setattr(worker.requests, "get", fake_get)
    monkeypatch.setattr(worker.time, "sleep", lambda s: None)
    return calls


_COMPLETE = '<table class="calc_table"><tr bgcolor=#efefef><td>x</td></tr></table>'
_TRUNCATED = '<table class="calc_table"><tr bgcolor=#efefef><td>x</td></tr><tr bgcolor=#efe'


class TestCompleteHtml:
    @pytest.mark.parametrize("html, expected", [
        (_COMPLETE, True),
        (_COMPLETE + _COMPLETE, True),
        ("No records found for this period", True),          # keine Tabelle = vollständig
        ("<TABLE class='calc_table'></TABLE>", True),          # Groß-/Kleinschreibung
        (_TRUNCATED, False),
        (_COMPLETE + _TRUNCATED, False),                      # zweite Tabelle abgeschnitten
    ])
    def test_table_tags_balanced(self, html, expected):
        assert worker.is_complete_html(html) is expected

    def test_real_fixtures_are_complete(self):
        from pathlib import Path
        fixtures = sorted((Path(__file__).parent / "fixtures").glob("calc_*.html"))
        assert fixtures
        for f in fixtures:
            assert worker.is_complete_html(f.read_text(encoding="utf-8")), f.name

    def test_truncated_then_complete_is_retried(self, monkeypatch):
        calls = _serve(monkeypatch, [_TRUNCATED, _COMPLETE])
        html, _ = worker._fetch(1, "2026-06-01", {"max_retries": 3, "use_proxy": False}, None)
        assert html == _COMPLETE
        assert len(calls) == 2

    def test_always_truncated_is_failure(self, monkeypatch):
        calls = _serve(monkeypatch, [_TRUNCATED])
        html, nbytes = worker._fetch(1, "2026-06-01", {"max_retries": 3, "use_proxy": False}, None)
        assert html is None and nbytes == 0
        assert len(calls) == 3


class _Event:
    """threading.Event-Ersatz ohne echte Wartezeit; merkt sich die Wartezeiten."""

    def __init__(self):
        self.waits = []
        self._set = False

    def is_set(self):
        return self._set

    def set(self):
        self._set = True

    def wait(self, timeout=None):
        self.waits.append(timeout)
        return self._set


class _FakeQM:
    instances: list = []
    fail = False

    def __init__(self):
        self.failed = []
        self.runs = []
        _FakeQM.instances.append(self)

    def mark_failed(self, group_id, msg):
        if _FakeQM.fail:
            raise RuntimeError("DB weg")
        self.failed.append((group_id, msg))

    def log_run(self, group_id, started, status, **kw):
        self.runs.append((group_id, status))

    def close(self):
        pass


@pytest.fixture
def supervised_env(monkeypatch):
    _FakeQM.instances = []
    _FakeQM.fail = False
    monkeypatch.setattr(worker, "QueueManager", _FakeQM)
    worker._claims.clear()
    exits = []

    def fake_exit(code):
        exits.append(code)
        raise SystemExit(code)

    monkeypatch.setattr(worker, "_hard_exit", fake_exit)
    yield exits
    worker._claims.clear()


class TestSupervised:
    def test_normal_return_ends_supervision(self, supervised_env):
        calls = []
        ev = _Event()
        worker._supervised(lambda: calls.append(1), 7, ev)
        assert calls == [1] and ev.waits == [] and supervised_env == []

    def test_restarts_after_crash_with_backoff(self, supervised_env):
        calls = []

        def target():
            calls.append(1)
            if len(calls) < 3:
                raise RuntimeError("boom")

        ev = _Event()
        worker._supervised(target, 7, ev)
        assert len(calls) == 3
        assert ev.waits == [60, 300]          # 1. Abbruch 1 min, 2. Abbruch 5 min
        assert supervised_env == []

    def test_process_exits_after_crash_series(self, supervised_env):
        def target():
            raise RuntimeError("boom")

        with pytest.raises(SystemExit):
            worker._supervised(target, 7, _Event())
        assert supervised_env == [1]

    def test_orphaned_group_is_marked_failed(self, supervised_env):
        calls = []

        def target():
            calls.append(1)
            if len(calls) == 1:
                worker._register_claim(7, 4711)   # Gruppe geclaimt, dann Absturz
                raise RuntimeError("DB kurz weg")

        worker._supervised(target, 7, _Event())
        marked = [m for qm in _FakeQM.instances for m in qm.failed]
        assert [gid for gid, _ in marked] == [4711]
        assert "Thread-Abbruch" in marked[0][1] and "DB kurz weg" in marked[0][1]
        assert 7 not in worker._claims
        assert len(calls) == 2

    def test_claim_stays_while_db_unreachable(self, supervised_env):
        _FakeQM.fail = True
        worker._register_claim(7, 4711)
        assert worker._settle_orphaned_claim(7, "x") is False
        assert worker._claims[7] == 4711
        _FakeQM.fail = False
        assert worker._settle_orphaned_claim(7, "x") is True
        assert 7 not in worker._claims

    def test_stop_event_ends_supervision(self, supervised_env):
        ev = _Event()

        def target():
            ev.set()
            raise RuntimeError("boom beim Stoppen")

        worker._supervised(target, 7, ev)   # kein Neustart, kein Exit
        assert supervised_env == []


class TestPauseAfterBlock:
    def test_waits_configured_time_and_shows_state(self, monkeypatch):
        shown = {}
        monkeypatch.setattr(worker, "_update_thread_slot", lambda slot, **kw: shown.update(kw))
        monkeypatch.setattr(worker, "_BLOCKED_PAUSE_SECONDS", 1800)
        ev = _Event()
        worker._pause_after_block(101, "semi_conservative", ev, "DC-Thread X")
        assert ev.waits == [1800]
        assert "403" in shown["current_group"]

    def test_real_event_wakes_on_stop(self, monkeypatch):
        monkeypatch.setattr(worker, "_update_thread_slot", lambda slot, **kw: None)
        ev = threading.Event()
        ev.set()
        worker._pause_after_block(101, "p", ev, "T")   # kehrt sofort zurück
