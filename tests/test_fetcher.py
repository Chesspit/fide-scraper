"""Tests for scraper.fetcher — uses responses library to mock HTTP requests."""

import responses
import requests

from scraper.fetcher import fetch_calculations, AJAX_URL


FIDE_ID = 24171760
PERIOD = "2025-04-01"
EXPECTED_URL = AJAX_URL.format(fide_id=FIDE_ID, period=PERIOD)


class TestFetchCalculations:
    @responses.activate
    def test_successful_fetch(self):
        responses.add(responses.GET, EXPECTED_URL, body="<table>ok</table>", status=200)

        result = fetch_calculations(FIDE_ID, PERIOD)
        assert result == "<table>ok</table>"

    @responses.activate
    def test_sets_correct_headers(self):
        responses.add(responses.GET, EXPECTED_URL, body="ok", status=200)

        fetch_calculations(FIDE_ID, PERIOD)

        req = responses.calls[0].request
        assert "XMLHttpRequest" in req.headers.get("X-Requested-With", "")
        assert "Mozilla" in req.headers.get("User-Agent", "")
        assert "calculations.phtml" in req.headers.get("Referer", "")

    @responses.activate
    def test_retry_on_429(self):
        responses.add(responses.GET, EXPECTED_URL, status=429)
        responses.add(responses.GET, EXPECTED_URL, body="ok", status=200)

        result = fetch_calculations(FIDE_ID, PERIOD)
        assert result == "ok"
        assert len(responses.calls) == 2

    @responses.activate
    def test_retry_on_500(self):
        responses.add(responses.GET, EXPECTED_URL, status=500)
        responses.add(responses.GET, EXPECTED_URL, status=500)
        responses.add(responses.GET, EXPECTED_URL, body="ok", status=200)

        result = fetch_calculations(FIDE_ID, PERIOD)
        assert result == "ok"
        assert len(responses.calls) == 3

    @responses.activate
    def test_raises_after_max_retries(self):
        responses.add(responses.GET, EXPECTED_URL, status=500)
        responses.add(responses.GET, EXPECTED_URL, status=500)
        responses.add(responses.GET, EXPECTED_URL, status=500)

        try:
            fetch_calculations(FIDE_ID, PERIOD)
            assert False, "Should have raised"
        except requests.RequestException:
            pass

        assert len(responses.calls) == 3

    @responses.activate
    def test_no_retry_on_404(self):
        responses.add(responses.GET, EXPECTED_URL, status=404)

        try:
            fetch_calculations(FIDE_ID, PERIOD)
            assert False, "Should have raised"
        except requests.RequestException:
            pass

        assert len(responses.calls) == 1
