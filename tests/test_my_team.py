from types import SimpleNamespace

from app.services import my_team as my_team_service
from app.services.my_team import linked_team_data
from app.config import Settings


class TeamClient:
    def __init__(self):
        self.entry_calls = 0
        self.bootstrap_calls = 0
        self.picks_events = []

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
        return {
            "events": [
                {"id": 3, "name": "Gameweek 3", "finished": True, "is_current": True, "is_next": False, "average_entry_score": 50},
                {"id": 4, "name": "Gameweek 4", "finished": False, "is_current": False, "is_next": True, "average_entry_score": 0},
            ]
        }

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


def test_my_team_remote_cache_refreshes_after_ten_minutes(monkeypatch):
    client = TeamClient()
    clock = iter((100.0, 400.0, 700.1))
    monkeypatch.setattr(my_team_service.time, "monotonic", lambda: next(clock))
    my_team_service._REMOTE_CACHE.clear()

    my_team_service._remote_team_data(client, 123)
    my_team_service._remote_team_data(client, 123)
    assert client.entry_calls == 1
    my_team_service._remote_team_data(client, 123)

    assert client.entry_calls == 2
    assert client.bootstrap_calls == 2


def test_my_team_falls_back_when_upcoming_picks_are_not_published(monkeypatch):
    client = UnpublishedUpcomingTeamClient()
    monkeypatch.setattr(my_team_service, "latest_rows", lambda db, season: _rows())
    my_team_service._REMOTE_CACHE.clear()
    settings = Settings(fpl_entry_id=123, current_season="2026/27")

    result = linked_team_data(None, client, settings)

    assert result["event"] == 3
    assert result["picks_available"] is True
    assert client.picks_events == [4, 3]


def test_manual_team_refresh_clears_the_cached_entry():
    my_team_service._REMOTE_CACHE[123] = (100.0, {"entry": {"id": 123}})
    clear_cache = getattr(my_team_service, "clear_remote_team_cache", None)

    assert callable(clear_cache)
    if callable(clear_cache):
        clear_cache(123)
        assert 123 not in my_team_service._REMOTE_CACHE
