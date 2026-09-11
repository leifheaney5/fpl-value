import logging

import httpx

from app.api.fpl_client import FPLClient
from app.config import Settings


def test_get_json_logs_successful_request_metadata(caplog):
    def respond(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"status": "ok"}, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = FPLClient(Settings(), http_client=http_client)

    with caplog.at_level(logging.INFO, logger="app.api.fpl_client"):
        payload = client._get_json(
            "https://fantasy.premierleague.com/api/bootstrap-static/",
            dataset="bootstrap",
        )

    assert payload == {"status": "ok"}
    assert "dataset=bootstrap" in caplog.text
    assert "endpoint=/api/bootstrap-static/" in caplog.text
    assert "status=200" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "retries=0" in caplog.text


def test_get_json_logs_retry_count_after_eventual_success(caplog, monkeypatch):
    responses = iter((503, 503, 200))

    def respond(request: httpx.Request) -> httpx.Response:
        status_code = next(responses)
        return httpx.Response(status_code, json={"attempt": status_code}, request=request)

    http_client = httpx.Client(transport=httpx.MockTransport(respond))
    client = FPLClient(Settings(), http_client=http_client)
    monkeypatch.setattr("app.api.fpl_client.time.sleep", lambda _: None)

    with caplog.at_level(logging.INFO, logger="app.api.fpl_client"):
        payload = client._get_json(
            "https://fantasy.premierleague.com/api/fixtures/",
            dataset="fixtures",
        )

    assert payload == {"attempt": 200}
    assert "dataset=fixtures" in caplog.text
    assert "endpoint=/api/fixtures/" in caplog.text
    assert "status=200" in caplog.text
    assert "duration_ms=" in caplog.text
    assert "retries=2" in caplog.text


def test_close_only_closes_client_owned_by_fpl_client():
    injected_http_client = httpx.Client(transport=httpx.MockTransport(lambda request: httpx.Response(200)))
    injected_client = FPLClient(Settings(), http_client=injected_http_client)
    owned_client = FPLClient(Settings())
    owned_http_client = owned_client._client

    injected_client.close()
    owned_client.close()

    assert not injected_http_client.is_closed
    assert owned_http_client.is_closed
