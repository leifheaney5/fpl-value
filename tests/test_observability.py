import logging
import re

from fastapi.testclient import TestClient

from app.main import app
from app.services.freshness import FreshnessCache, FreshnessPolicy


def test_request_completion_log_includes_route_status_and_duration(caplog, monkeypatch):
    monkeypatch.setattr(logging.getLogger("app.main"), "disabled", False)

    with caplog.at_level(logging.INFO, logger="app.main"):
        response = TestClient(app).get("/login")

    messages = [
        record.getMessage()
        for record in caplog.records
        if record.name == "app.main" and "request_complete" in record.getMessage()
    ]

    assert response.status_code == 200
    assert len(messages) == 1
    match = re.search(
        r"method=GET path=/login status=200 duration_ms=(\d+(?:\.\d+)?)",
        messages[0],
    )
    assert match is not None
    assert float(match.group(1)) >= 0


def test_freshness_logs_cache_hit_and_stale_without_payload_or_secret_values(
    caplog, monkeypatch
):
    monkeypatch.setattr(logging.getLogger("app.services.freshness"), "disabled", False)

    now = [100.0]
    cache = FreshnessCache(clock=lambda: now[0])
    policy = FreshnessPolicy("team", ttl_seconds=60, stale_if_error=True)
    payload = {
        "picks_event": 17,
        "response_body": "private-response-body",
        "session_secret": "private-setting-value",
    }

    with caplog.at_level(logging.INFO, logger="app.services.freshness"):
        cache.get("entry:7", lambda: payload, policy)
        cache.get("entry:7", lambda: payload, policy)
        now[0] = 161.0
        stale = cache.get(
            "entry:7",
            lambda: (_ for _ in ()).throw(RuntimeError("upstream unavailable")),
            policy,
        )

    messages = [record.getMessage() for record in caplog.records]

    assert stale.cache_hit is True
    assert stale.stale is True
    assert any(
        "dataset=team key=entry:7 cache_state=hit event=17 "
        "fetched_at=100.0 expires_at=160.0 cache_hit=True stale=False "
        "age_ms=0.0 remaining_ttl_ms=60000.0" in message
        for message in messages
    )
    assert any(
        "dataset=team key=entry:7 cache_state=stale event=17 "
        "fetched_at=100.0 expires_at=160.0 cache_hit=True stale=True "
        "age_ms=61000.0 remaining_ttl_ms=-1000.0" in message
        for message in messages
    )
    assert "private-response-body" not in caplog.text
    assert "private-setting-value" not in caplog.text
