import threading
from types import SimpleNamespace

from app.services import my_team as my_team_service
from app.services.my_team import linked_team_data
from app.config import Settings


class TeamClient:
    def __init__(self):
        self.entry_calls = 0
        self.bootstrap_calls = 0
        self.picks_events = []
        self.events = [
            {"id": 3, "name": "Gameweek 3", "finished": True, "is_current": True, "is_next": False, "average_entry_score": 50},
            {"id": 4, "name": "Gameweek 4", "finished": False, "is_current": False, "is_next": True, "average_entry_score": 0},
        ]

    def entry(self, entry_id):
        self.entry_calls += 1
        return {
            "id": entry_id,
            "name": "Test XI",
            "player_first_name": "Ada",
            "player_last_name": "Example",
            "current_event": 3,
            "summary_overall_rank": 10,
            "summary_overall_points": 200,
        }

    def bootstrap(self):
        self.bootstrap_calls += 1
        return {"events": self.events}

    def entry_history(self, entry_id):
        return {"current": [{"event": 3, "points": 60, "total_points": 200}]}

    def entry_picks(self, entry_id, event_id):
        self.picks_events.append(event_id)
        return {"picks": [{"element": 10, "is_captain": True}]}


class UnpublishedUpcomingTeamClient(TeamClient):
    def entry_picks(self, entry_id, event_id):
        self.picks_events.append(event_id)
        if event_id == 4:
            raise RuntimeError("upcoming picks have not been published")
        return {"picks": [{"element": 10, "is_captain": True}]}


class ErroringBootstrapTeamClient(TeamClient):
    def bootstrap(self):
        raise RuntimeError("FPL is unavailable")


class BlockingBootstrapTeamClient(TeamClient):
    def __init__(self, started, release):
        super().__init__()
        self.started = started
        self.release = release

    def bootstrap(self):
        self.started.set()
        assert self.release.wait(timeout=1)
        return super().bootstrap()


def _rows():
    return [{
        "player": SimpleNamespace(id=10, full_name="Ada Example"),
        "snapshot": SimpleNamespace(captured_at=None),
    }]


def test_my_team_uses_the_upcoming_gameweek_for_current_picks(monkeypatch):
    client = TeamClient()
    monkeypatch.setattr(my_team_service, "latest_rows", lambda db, season: _rows())
    settings = Settings(fpl_entry_id=123, current_season="2026/27")

    result = linked_team_data(None, client, settings)

    assert result["event"] == 4
    assert result["picks_available"] is True
    assert client.picks_events == [4]
    assert result["squad"][0]["pick"]["is_captain"] is True


def test_forced_team_refresh_bypasses_a_valid_cached_snapshot():
    client = TeamClient()
    my_team_service.clear_remote_team_cache()

    cached = my_team_service._remote_team_data(client, 123)
    client.events = [
        *client.events,
        {"id": 5, "name": "Gameweek 5", "finished": False, "is_current": False, "is_next": False, "average_entry_score": 0},
    ]
    refreshed = my_team_service._remote_team_data(client, 123, force=True)

    assert cached["picks_event"] == 4
    assert refreshed["picks_event"] == 5
    assert client.picks_events == [4, 5]


def test_my_team_falls_back_when_upcoming_picks_are_not_published(monkeypatch):
    client = UnpublishedUpcomingTeamClient()
    monkeypatch.setattr(my_team_service, "latest_rows", lambda db, season: _rows())
    my_team_service.clear_remote_team_cache()
    settings = Settings(fpl_entry_id=123, current_season="2026/27")

    result = linked_team_data(None, client, settings)

    assert result["event"] == 3
    assert result["picks_available"] is True
    assert client.picks_events == [4, 3]


def test_my_team_uses_newest_available_picks_not_event_flags(monkeypatch):
    client = TeamClient()
    client.events = [
        *client.events,
        {"id": 5, "name": "Gameweek 5", "finished": False, "is_current": False, "is_next": False, "average_entry_score": 0},
    ]
    monkeypatch.setattr(my_team_service, "latest_rows", lambda db, season: _rows())
    my_team_service.clear_remote_team_cache()
    settings = Settings(fpl_entry_id=123, current_season="2026/27")

    result = linked_team_data(None, client, settings)

    assert result["event"] == 5
    assert result["picks_available"] is True
    assert client.picks_events == [5]


def test_fpl_error_keeps_the_last_valid_team_snapshot_as_stale():
    client = TeamClient()
    my_team_service.clear_remote_team_cache()
    my_team_service._remote_team_data(client, 123)

    stale = my_team_service._remote_team_data(ErroringBootstrapTeamClient(), 123, force=True)

    assert stale["picks_event"] == 4
    assert stale["_freshness"]["stale"] is True
    assert stale["_freshness"]["last_error"] == "FPL is unavailable"


def test_invalidated_inflight_snapshot_cannot_overwrite_the_newer_event_generation():
    my_team_service.clear_remote_team_cache()
    initial = TeamClient()
    my_team_service._remote_team_data(initial, 123)
    loader_started = threading.Event()
    release_loader = threading.Event()
    older_result = []
    older_client = BlockingBootstrapTeamClient(loader_started, release_loader)

    thread = threading.Thread(
        target=lambda: older_result.append(
            my_team_service._remote_team_data(older_client, 123, force=True)
        )
    )
    thread.start()
    assert loader_started.wait(timeout=1)
    my_team_service.clear_remote_team_cache(123)
    release_loader.set()
    thread.join(timeout=1)

    newer_client = TeamClient()
    newer_client.events = [
        *newer_client.events,
        {"id": 5, "name": "Gameweek 5", "finished": False, "is_current": False, "is_next": False, "average_entry_score": 0},
    ]
    refreshed = my_team_service._remote_team_data(newer_client, 123)
    cached = my_team_service._remote_team_data(TeamClient(), 123)

    assert not thread.is_alive()
    assert older_result[0]["picks_event"] == 4
    assert refreshed["picks_event"] == 5
    assert cached["picks_event"] == 5
    assert cached["_freshness"]["cache_hit"] is True
