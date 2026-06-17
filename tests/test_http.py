"""Offline unit tests for the HTTP client retry policy (responses-mocked)."""
import time

import pytest
import requests
import responses

from gadata.infrastructure.http import HttpClient, RetryableHTTPError

URL = "https://example.test/endpoint"


@pytest.fixture(autouse=True)
def _no_sleep(monkeypatch):
    """Neutralise backoff/politeness sleeps so retry tests run instantly."""
    monkeypatch.setattr(time, "sleep", lambda *a, **k: None)


def _client():
    # Zero politeness delay and small backoff keep the suite fast.
    return HttpClient(max_attempts=4, backoff_base=0.0, politeness_delay=0.0)


@responses.activate
def test_no_retry_on_400():
    responses.add(responses.GET, URL, status=400)
    with pytest.raises(requests.HTTPError):
        _client().get(URL)
    assert len(responses.calls) == 1  # never retried


@responses.activate
def test_no_retry_on_404():
    responses.add(responses.GET, URL, status=404)
    with pytest.raises(requests.HTTPError):
        _client().get(URL)
    assert len(responses.calls) == 1


@responses.activate
def test_retry_on_503_then_success():
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, status=503)
    responses.add(responses.GET, URL, json={"ok": True}, status=200)
    resp = _client().get(URL)
    assert resp.status_code == 200
    assert resp.json() == {"ok": True}
    assert len(responses.calls) == 3


@responses.activate
def test_retry_exhausts_and_reraises():
    for _ in range(4):
        responses.add(responses.GET, URL, status=503)
    with pytest.raises(RetryableHTTPError):
        _client().get(URL)
    assert len(responses.calls) == 4  # capped at max_attempts


@responses.activate
def test_304_is_returned_not_raised():
    responses.add(responses.GET, URL, status=304)
    resp = _client().get(URL, headers={"If-None-Match": '"abc"'})
    assert resp.status_code == 304
    assert len(responses.calls) == 1


@responses.activate
def test_response_headers_exposed():
    responses.add(responses.GET, URL, status=200, headers={"ETag": '"xyz"'}, json={})
    resp = _client().get(URL)
    assert resp.headers["ETag"] == '"xyz"'


@responses.activate
def test_post_works():
    responses.add(responses.POST, URL, json={"posted": 1}, status=200)
    resp = _client().post(URL, data={"a": "b"})
    assert resp.json() == {"posted": 1}
