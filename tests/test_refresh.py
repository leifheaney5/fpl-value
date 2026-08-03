from datetime import datetime, timezone
import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import Gameweek, PlayerSnapshot, RefreshRun, SchemaChange
from app.services.refresh import refresh_data

from fakes import FakeClient, PreseasonClient


def test_refresh_pipeline(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}"
    )

    with Session() as db:
        run = refresh_data(db, settings, FakeClient())
        assert run.status == "success"
        snapshot = db.scalar(select(PlayerSnapshot))
        assert snapshot is not None
        assert snapshot.value == 10.0
        assert snapshot.reliable_value > 0
        assert snapshot.forward_value > 0


def test_preseason_refresh_stores_null_metrics_rather_than_zero(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'preseason.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'preseason.db'}",
        current_season="2026/27",
    )

    with Session() as db:
        run = refresh_data(db, settings, PreseasonClient())
        assert run.status == "success"

        snapshot = db.scalar(select(PlayerSnapshot))
        assert snapshot.season == "2026/27"
        # No team has played, so none of these can be measured.
        assert snapshot.expected_minutes is None
        assert snapshot.start_rate is None
        assert snapshot.reliability_factor is None
        assert snapshot.reliable_value is None
        assert snapshot.projected_points_5 is None
        assert snapshot.forward_value is None
        # Observations remain observations.
        assert snapshot.minutes == 0
        assert snapshot.total_points == 0

        status = snapshot.metric_status["expected_minutes"]
        assert status["status"] == "not_yet_available"
        assert "No matches played" in status["reason"]

        # And the run explains the ranking gap rather than leaving it implicit.
        assert run.details["ranked_count"] == 0
        assert sum(run.details["ranking_exclusions"].values()) == 1


def test_preseason_refresh_records_the_season_calendar(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'calendar.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'calendar.db'}",
        current_season="2026/27",
    )

    with Session() as db:
        refresh_data(db, settings, PreseasonClient())
        gameweek = db.scalar(select(Gameweek))
        assert gameweek.season == "2026/27"
        assert gameweek.number == 1
        assert gameweek.is_next is True
        assert gameweek.deadline_time is not None

        # Re-running must update in place rather than duplicate.
        refresh_data(db, settings, PreseasonClient())
        assert len(db.scalars(select(Gameweek)).all()) == 1


def test_refresh_records_schema_removals_and_rejects_overlap(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'schema.db'}", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'schema.db'}")

    class ChangedClient(FakeClient):
        def bootstrap(self):
            payload = super().bootstrap()
            payload["elements"][0].pop("ict_index")
            return payload

    with Session() as db:
        refresh_data(db, settings, FakeClient())
        refresh_data(db, settings, ChangedClient())
        assert db.query(SchemaChange).filter_by(change_type="Removed", field_name="ict_index").count() == 1
        db.add(RefreshRun(started_at=datetime.now(timezone.utc), status="running"))
        db.commit()
        with pytest.raises(RuntimeError, match="already in progress"):
            refresh_data(db, settings, FakeClient())
