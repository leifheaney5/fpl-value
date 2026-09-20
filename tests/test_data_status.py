from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Player, PlayerSnapshot, RefreshRun, Team
from app.services.data_status import data_status


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'status.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def _completed(db, captured_at, *, status="success", error="", season="2026/27"):
    run = RefreshRun(
        started_at=captured_at - timedelta(minutes=5),
        completed_at=captured_at,
        status=status,
        error=error,
        details={"season": season},
    )
    db.add(run)
    db.flush()
    db.add(Team(id=1, name="Test FC", short_name="TST", updated_at=captured_at))
    db.add(
        Player(
            id=1,
            code=1,
            first_name="Test",
            second_name="Player",
            web_name="Player",
            team_id=1,
            position="Midfielder",
            position_short="MID",
            status="a",
            news="",
            updated_at=captured_at,
            raw={},
        )
    )
    db.add(
        PlayerSnapshot(
            player_id=1,
            refresh_run_id=run.id,
            captured_at=captured_at,
            season=season,
            price=5.0,
            total_points=1,
            minutes=90,
            starts=1,
            team_matches=1,
        )
    )
    db.commit()
    return run


def test_status_reports_never_refreshed_database(tmp_path):
    db = _session(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    assert data_status(db, "2026/27", now=now)["state"] == "never_refreshed"


def test_status_reports_fresh_completed_run(tmp_path):
    db = _session(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    _completed(db, now - timedelta(hours=1))
    result = data_status(db, "2026/27", now=now)
    assert result["state"] == "fresh"
    assert result["last_run_status"] == "completed"
    assert result["snapshot_age_seconds"] == 3600.0


def test_status_reports_stale_snapshot(tmp_path):
    db = _session(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    _completed(db, now - timedelta(days=3))
    assert data_status(db, "2026/27", now=now)["state"] == "stale"


def test_status_reports_failed_run_without_calling_it_current(tmp_path):
    db = _session(tmp_path)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)
    _completed(db, now - timedelta(hours=2))
    db.add(
        RefreshRun(
            started_at=now - timedelta(minutes=10),
            completed_at=now - timedelta(minutes=9),
            status="failed",
            error="upstream timeout",
            details={"season": "2026/27"},
        )
    )
    db.commit()
    result = data_status(db, "2026/27", now=now)
    assert result["state"] == "failed"
    assert result["last_run_status"] == "failed"
    assert result["last_run_error"] == "upstream timeout"
