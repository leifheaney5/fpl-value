from datetime import datetime, timezone
import pytest

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import Gameweek, Player, PlayerSnapshot, RefreshRun, SchemaChange
from app.services.refresh import _update_schema, refresh_data, utcnow

from fakes import CarryOverPreseasonClient, FakeClient, PreseasonClient


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


def test_refresh_stores_the_stable_player_code(tmp_path):
    """Element ids move between seasons; the code is what joins history."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'code.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'code.db'}")

    class CodedClient(FakeClient):
        def bootstrap(self):
            payload = super().bootstrap()
            payload["elements"][0]["code"] = 154561
            return payload

    with Session() as db:
        refresh_data(db, settings, CodedClient())
        player = db.scalar(select(Player))
        assert player.code == 154561


def test_carried_over_minutes_do_not_become_current_season_rates(tmp_path):
    """Previous-season minutes must not produce a current-season points-per-90.

    In preseason the FPL bootstrap still reports last season's minutes and
    points. Dividing them gives a real-looking rate that describes a season that
    has ended, presented as though it described this one.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'carryover.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'carryover.db'}",
        current_season="2026/27",
    )

    with Session() as db:
        refresh_data(db, settings, CarryOverPreseasonClient())
        snapshot = db.scalar(select(PlayerSnapshot))

        # The raw observations are stored as reported.
        assert snapshot.minutes == 1170
        assert snapshot.total_points == 43
        assert snapshot.team_matches == 0

        # Rates computable from the carry-over figures alone are available, and
        # every one of them is marked as describing the previous season. They
        # were suppressed until 2026-08-04 on the grounds that they would be
        # "presented as though they described this one" -- which is a statement
        # about presentation, and is now handled by the status and by the
        # column labelling on the sheet. The measurements themselves were always
        # sound; withholding points-per-million in particular removed the single
        # most useful preseason evaluation metric.
        for metric in (
            "value", "points_per_90", "points_per_minute", "points_per_start",
            "average_minutes_per_start",
        ):
            assert getattr(snapshot, metric) is not None, f"{metric} should be available"

        # Only the metrics the interface renders through metric_cell carry a
        # status entry; those are the ones that must name their season.
        for metric in ("value", "points_per_90"):
            assert snapshot.metric_status[metric]["status"] == "previous_season", (
                f"{metric} must say which season it describes"
            )

        # Rates needing a this-season quantity have no denominator in any form:
        # team_matches is zero and the API does not report last season's.
        assert snapshot.start_rate is None
        assert snapshot.points_per_team_match is None
        assert snapshot.expected_minutes is None

        assert "last season" in snapshot.metric_status["points_per_90"]["reason"].lower()
        assert "No matches played" in snapshot.metric_status["start_rate"]["reason"]


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


def test_first_schema_observation_is_a_baseline_not_hundreds_of_additions(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'baseline.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    with Session() as db:
        result = _update_schema(db, {"players": {"id", "web_name", "now_cost"}}, utcnow())
        db.commit()

        assert result["baseline"] is True
        assert result["added"] == 0
        assert result["removed"] == 0
        assert result["fields"] == 3
        types = {change.change_type for change in db.scalars(select(SchemaChange)).all()}
        assert types == {"Baseline"}


def test_a_field_appearing_after_the_baseline_is_an_addition(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'added.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    with Session() as db:
        _update_schema(db, {"players": {"id", "web_name"}}, utcnow())
        db.commit()
        result = _update_schema(db, {"players": {"id", "web_name", "new_field"}}, utcnow())
        db.commit()

        assert result["baseline"] is False
        assert result["added"] == 1
        added = db.scalars(
            select(SchemaChange).where(SchemaChange.change_type == "Added")
        ).all()
        assert [change.field_name for change in added] == ["new_field"]


def test_a_real_refresh_records_a_baseline_and_no_changes(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'runbaseline.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=f"sqlite:///{tmp_path / 'runbaseline.db'}")

    with Session() as db:
        run = refresh_data(db, settings, FakeClient())
        assert run.details["schema_baseline"] is True
        assert run.schema_change_count == 0
        assert run.details["schema_fields"] > 0


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
