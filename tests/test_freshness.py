import threading

from app.services.freshness import (
    CURRENT_EVENT_FIXTURES_POLICY,
    HISTORICAL_DATA_POLICY,
    MY_TEAM_POLICY,
    PLAYER_MARKET_POLICY,
    FreshnessCache,
    FreshnessPolicy,
)


def test_named_policies_use_the_service_specific_freshness_windows():
    assert MY_TEAM_POLICY.ttl_seconds == 60
    assert CURRENT_EVENT_FIXTURES_POLICY.ttl_seconds == 300
    assert PLAYER_MARKET_POLICY.ttl_seconds == 600
    assert HISTORICAL_DATA_POLICY.ttl_seconds == 86_400


def test_cache_returns_metadata_and_reuses_fresh_value():
    calls = []
    cache = FreshnessCache(clock=lambda: 100.0)
    policy = FreshnessPolicy("team", ttl_seconds=60)

    first = cache.get("entry:7", lambda: calls.append(1) or {"event": 4}, policy)
    second = cache.get("entry:7", lambda: calls.append(1) or {"event": 5}, policy)

    assert first.value == {"event": 4}
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.expires_at == 160.0
    assert calls == [1]


def test_failed_revalidation_returns_last_valid_value_as_stale():
    now = [100.0]
    cache = FreshnessCache(clock=lambda: now[0])
    policy = FreshnessPolicy("team", ttl_seconds=60, stale_if_error=True)
    cache.get("entry:7", lambda: {"event": 4}, policy)
    now[0] = 161.0

    stale = cache.get(
        "entry:7",
        lambda: (_ for _ in ()).throw(RuntimeError("timeout")),
        policy,
    )

    assert stale.value == {"event": 4}
    assert stale.stale is True
    assert stale.last_error == "timeout"
    assert stale.fetched_at == 100.0


def test_force_invalidation_prevents_older_generation_from_winning():
    cache = FreshnessCache(clock=lambda: 100.0)
    policy = FreshnessPolicy("team", ttl_seconds=60)
    cache.get("entry:7", lambda: {"event": 4}, policy)
    cache.invalidate("entry:7")

    refreshed = cache.get("entry:7", lambda: {"event": 5}, policy)

    assert refreshed.value == {"event": 5}
    assert refreshed.generation == 1


def test_inflight_loader_cannot_overwrite_an_invalidated_generation():
    now = [100.0]
    cache = FreshnessCache(clock=lambda: now[0])
    policy = FreshnessPolicy("team", ttl_seconds=60)
    cache.get("entry:7", lambda: {"event": 4}, policy)
    now[0] = 161.0
    loader_started = threading.Event()
    release_loader = threading.Event()
    old_result = []

    def expired_loader():
        loader_started.set()
        assert release_loader.wait(timeout=1)
        return {"event": 5}

    thread = threading.Thread(
        target=lambda: old_result.append(cache.get("entry:7", expired_loader, policy))
    )
    thread.start()
    assert loader_started.wait(timeout=1)
    cache.invalidate("entry:7")
    release_loader.set()
    thread.join(timeout=1)

    refreshed = cache.get("entry:7", lambda: {"event": 6}, policy)
    cached = cache.get("entry:7", lambda: {"event": 7}, policy)

    assert not thread.is_alive()
    assert old_result[0].value == {"event": 5}
    assert refreshed.value == {"event": 6}
    assert cached.value == {"event": 6}
    assert cached.cache_hit is True
