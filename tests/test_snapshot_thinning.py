"""Hourly refreshes must not grow storage: old days collapse to one snapshot."""

from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker

from app import cli
from app.config import Settings
from app.db.base import Base
from app.db.models import Player, PlayerSnapshot, RefreshRun, Team
from app.services.snapshot_retention import thin_snapshots

NOW = datetime(2026, 9, 19, 18, 0, tzinfo=timezone.utc)


def _session(tmp_path, captures, season="2026/27"):
    engine = create_engine(f"sqlite:///{tmp_path / 'thin.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    db = Session()
    db.add(Team(id=1, name="Test FC", short_name="TST", updated_at=NOW))
    run = RefreshRun(started_at=NOW, completed_at=NOW, status="success")
    db.add(run)
    db.flush()
    for player_id in (1, 2):
        db.add(Player(id=player_id, web_name=f"P{player_id}", team_id=1,
                      position="Midfielder", position_short="MID", updated_at=NOW))
        for captured in captures:
            db.add(PlayerSnapshot(
                player_id=player_id, refresh_run_id=run.id, captured_at=captured,
                season=season, price=5.0, total_points=10, minutes=90, starts=1,
                team_matches=1,
            ))
    db.commit()
    return db


def _captures(db):
    return sorted(
        value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value
        for value in db.scalars(select(PlayerSnapshot.captured_at).distinct())
    )


def test_old_days_keep_only_their_last_snapshot(tmp_path):
    # New York is UTC-4 in September, so 03:00 UTC still belongs to the 16th.
    sept_16 = [datetime(2026, 9, 16, hour, tzinfo=timezone.utc) for hour in (14, 18, 22)]
    late_16 = datetime(2026, 9, 17, 3, tzinfo=timezone.utc)
    sept_17 = datetime(2026, 9, 17, 14, tzinfo=timezone.utc)
    recent = [NOW - timedelta(hours=hours) for hours in (30, 5, 1)]
    db = _session(tmp_path, sept_16 + [late_16, sept_17] + recent)

    removed = thin_snapshots(
        db, "2026/27", now=NOW, keep_hours=48, timezone_name="America/New_York"
    )

    assert removed == 6  # three captures, two players each
    assert _captures(db) == sorted([late_16, sept_17] + recent)


def test_recent_snapshots_and_other_seasons_are_left_alone(tmp_path):
    hourly = [NOW - timedelta(hours=hours) for hours in range(1, 40)]
    db = _session(tmp_path, hourly)
    assert thin_snapshots(db, "2026/27", now=NOW, keep_hours=48,
                          timezone_name="America/New_York") == 0

    old = [datetime(2025, 9, 1, hour, tzinfo=timezone.utc) for hour in (14, 15)]
    db2 = _session_with_prefix(tmp_path, old)
    assert thin_snapshots(db2, "2026/27", now=NOW, keep_hours=48,
                          timezone_name="America/New_York") == 0
    assert db2.scalar(select(func.count()).select_from(PlayerSnapshot)) == 4


def _session_with_prefix(tmp_path, captures):
    path = tmp_path / "other-season"
    path.mkdir()
    return _session(path, captures, season="2025/26")


class _Context:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self.value

    def __exit__(self, exc_type, exc, tb):
        return False


class _OffHour:
    @classmethod
    def now(cls, tz=None):
        return datetime(2026, 9, 19, 15, 0, tzinfo=tz or timezone.utc)


def test_hourly_mode_runs_a_scheduled_refresh_outside_the_daily_hour(monkeypatch, capsys):
    calls = []
    run = RefreshRun(status="success", player_count=1, schema_change_count=0)
    monkeypatch.setattr(cli, "datetime", _OffHour)
    monkeypatch.setattr(cli, "SessionLocal", lambda: _Context("db"))
    monkeypatch.setattr(cli, "FPLClient", lambda settings: _Context("client"))
    monkeypatch.setattr(cli, "refresh_data", lambda *args: calls.append(args) or run)

    monkeypatch.setattr(cli, "get_settings", lambda: Settings(refresh_hour=10))
    assert cli.command_refresh(scheduled=True, force=False) == 0
    assert calls == [] and "skipped" in capsys.readouterr().out

    monkeypatch.setattr(
        cli, "get_settings", lambda: Settings(refresh_hour=10, refresh_hourly=True)
    )
    assert cli.command_refresh(scheduled=True, force=False) == 0
    assert len(calls) == 1
