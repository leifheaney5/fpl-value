from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Player, PlayerSnapshot, RefreshRun, Team
from app.services.queries import latest_rows
from app.services.history_import import import_history_directory


def test_historical_deltas(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'history.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Team(id=1, name="Test", short_name="TST", updated_at=now))
        db.add(
            Player(
                id=1,
                first_name="Ada",
                second_name="Example",
                web_name="Ada",
                team_id=1,
                position="Midfielder",
                position_short="MID",
                status="a",
                news="",
                updated_at=now,
                raw={},
            )
        )
        old_run = RefreshRun(started_at=now - timedelta(days=8), completed_at=now, status="success")
        new_run = RefreshRun(started_at=now, completed_at=now, status="success")
        db.add_all([old_run, new_run])
        db.flush()

        common = dict(
            player_id=1,
            price=5.0,
            total_points=50,
            minutes=900,
            starts=10,
            team_matches=10,
            value_rank=10,
        )
        db.add(PlayerSnapshot(
            refresh_run_id=old_run.id,
            captured_at=now - timedelta(days=8),
            value=9.0,
            ownership=5.0,
            **common,
        ))
        db.add(PlayerSnapshot(
            refresh_run_id=new_run.id,
            captured_at=now,
            value=10.0,
            ownership=6.0,
            **common,
        ))
        db.commit()

        rows = latest_rows(db, "2026/27")
        assert rows[0]["history"]["7D"]["delta_value"] == 1.0
        assert rows[0]["history"]["7D"]["delta_ownership"] == 1.0


def test_legacy_history_import_is_idempotent(tmp_path):
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    csv_path = tmp_path / "snapshot.csv"
    csv_path.write_text("player_id,captured_at,price,total_points,minutes,starts,team_matches,value\n1,2026-07-01T10:00:00+00:00,5.0,40,720,8,10,8.0\n", encoding="utf-8")
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Team(id=1, name="Test", short_name="TST", updated_at=now))
        db.add(Player(id=1, first_name="Ada", second_name="Example", web_name="Ada", team_id=1, position="Midfielder", position_short="MID", status="a", news="", updated_at=now, raw={}))
        db.commit()
        first = import_history_directory(db, tmp_path, season="2025/26")
        second = import_history_directory(db, tmp_path, season="2025/26")
        assert first["imported"] == 1
        assert second["imported"] == 0
        assert db.scalar(select(PlayerSnapshot).where(PlayerSnapshot.player_id == 1)) is not None
