"""Risers and fallers must be mutually exclusive and must exclude no-change.

The original implementation kept every row whose delta was not null -- including
exact zeros -- then sorted the same list ascending and descending, so a player
who had not moved appeared in both lists.
"""

from datetime import timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.services.queries import classify_movement, movers_data
from app.services.refresh import refresh_data

from fakes import FakeClient


def test_a_zero_delta_is_neither_a_riser_nor_a_faller():
    assert classify_movement(0.0, 0.01) == "unchanged"
    assert classify_movement(0.005, 0.01) == "unchanged"
    assert classify_movement(-0.005, 0.01) == "unchanged"
    assert classify_movement(0.01, 0.01) == "unchanged"


def test_movement_beyond_the_threshold_is_classified_by_sign():
    assert classify_movement(0.5, 0.01) == "riser"
    assert classify_movement(-0.5, 0.01) == "faller"


def test_a_missing_delta_is_not_movement():
    assert classify_movement(None, 0.01) == "no_history"


def _seeded(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=url, current_season="2026/27")
    with Session() as db:
        refresh_data(db, settings, FakeClient())
    return Session, settings


def _clone_snapshot(db, source, *, days_earlier, value):
    """An identical earlier snapshot, so the delta is exactly zero."""
    columns = {
        column.name: getattr(source, column.name)
        for column in PlayerSnapshot.__table__.columns
        if column.name != "id"
    }
    columns["captured_at"] = source.captured_at - timedelta(days=days_earlier)
    columns["value"] = value
    db.add(PlayerSnapshot(**columns))


def test_an_unchanged_player_appears_in_neither_list(tmp_path):
    Session, settings = _seeded(tmp_path, "unchanged.db")

    with Session() as db:
        current = db.scalars(select(PlayerSnapshot)).all()
        for snapshot in current:
            _clone_snapshot(db, snapshot, days_earlier=8, value=snapshot.value)
        db.commit()

        movers = movers_data(db, settings.current_season, "7D")

        assert movers["value_risers"] == []
        assert movers["value_fallers"] == []
        window = movers["value_window"]
        assert window["observations"] >= 1
        assert window["unchanged"] == window["observations"]


def test_a_real_move_is_classified_once_and_only_once(tmp_path):
    Session, settings = _seeded(tmp_path, "moved.db")

    with Session() as db:
        current = db.scalars(select(PlayerSnapshot)).all()
        for snapshot in current:
            # An earlier, lower value means the player has risen since.
            _clone_snapshot(db, snapshot, days_earlier=8, value=snapshot.value - 2.0)
        db.commit()

        movers = movers_data(db, settings.current_season, "7D")

        risers = {row["player"].id for row in movers["value_risers"]}
        fallers = {row["player"].id for row in movers["value_fallers"]}
        assert risers
        assert not risers & fallers, "a player was classified as both"
        assert movers["value_window"]["unchanged"] == 0


def test_every_metric_keeps_risers_and_fallers_disjoint(tmp_path):
    Session, settings = _seeded(tmp_path, "disjoint.db")

    with Session() as db:
        current = db.scalars(select(PlayerSnapshot)).all()
        for snapshot in current:
            _clone_snapshot(db, snapshot, days_earlier=8, value=snapshot.value)
        db.commit()

        movers = movers_data(db, settings.current_season, "7D")

    for metric in ("value", "price", "ownership", "rank"):
        risers = {row["player"].id for row in movers[f"{metric}_risers"]}
        fallers = {row["player"].id for row in movers[f"{metric}_fallers"]}
        assert not risers & fallers, f"{metric} classified a player both ways"
