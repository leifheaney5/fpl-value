from datetime import datetime, timezone

from app import cli
from app.config import Settings


class _BeforeRefreshDateTime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 11, 9, 0, tzinfo=tz or timezone.utc)


class _AtRefreshDateTime:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 11, 10, 0, tzinfo=tz or timezone.utc)


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return False


def test_scheduled_refresh_skips_outside_configured_local_hour(monkeypatch, capsys):
    calls = []
    settings = Settings(app_timezone="America/New_York", refresh_hour=10)

    monkeypatch.setattr(cli, "datetime", _BeforeRefreshDateTime)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionLocal", lambda: calls.append("db"))
    monkeypatch.setattr(cli, "FPLClient", lambda value: calls.append("client"))

    # 09:00 local time is outside the configured refresh hour, so the guard
    # must return before opening a database or external FPL client.
    assert cli.command_refresh(scheduled=True, force=False) == 0
    assert calls == []
    assert "skipped" in capsys.readouterr().out.lower()


def test_scheduled_refresh_delegates_to_refresh_data_in_configured_hour(monkeypatch):
    calls = []
    settings = Settings(app_timezone="UTC", refresh_hour=10)

    class Client:
        def __enter__(self):
            calls.append("client_enter")
            return self

        def __exit__(self, exc_type, exc, tb):
            calls.append("client_exit")
            return False

    class Run:
        status = "success"
        player_count = 12
        schema_change_count = 0

    monkeypatch.setattr(cli, "datetime", _AtRefreshDateTime)
    monkeypatch.setattr(cli, "get_settings", lambda: settings)
    monkeypatch.setattr(cli, "SessionLocal", lambda: _Context("db"))
    monkeypatch.setattr(cli, "FPLClient", lambda value: Client())
    monkeypatch.setattr(
        cli,
        "refresh_data",
        lambda db, received_settings, client: calls.append(
            ("refresh", db, received_settings, client)
        ) or Run(),
    )

    assert cli.command_refresh(scheduled=True, force=False) == 0
    assert calls[0] == "client_enter"
    assert calls[1][0] == "refresh"
    assert calls[-1] == "client_exit"


def test_build_season_aggregates_command_rebuilds_from_history(
    seeded_history_db, monkeypatch, capsys
):
    monkeypatch.setattr(cli, "SessionLocal", lambda: _Context(seeded_history_db))
    monkeypatch.setattr(
        "sys.argv", ["cli", "build-season-aggregates", "--season", "2025/26"]
    )

    assert cli.main() == 0
    assert "2 player-seasons across 1 seasons" in capsys.readouterr().out
