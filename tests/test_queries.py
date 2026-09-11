from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Player, PlayerSnapshot, RefreshRun, Team
from app.services.queries import (
    diagnostics_data,
    history_player_count,
    latest_player_options,
    latest_rows,
)


def _seeded_session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'queries.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    now = datetime(2026, 9, 11, tzinfo=timezone.utc)

    with Session() as db:
        db.add(Team(id=1, name="Test FC", short_name="TST", updated_at=now))
        old_run = RefreshRun(
            started_at=now - timedelta(days=1),
            completed_at=now - timedelta(days=1),
            status="success",
        )
        latest_run = RefreshRun(
            started_at=now,
            completed_at=now,
            status="success",
        )
        db.add_all([old_run, latest_run])
        db.flush()
        for player_id, name, snapshots in (
            (1, "Ada", 2),
            (2, "Bea", 2),
            (3, "Cy", 1),
        ):
            db.add(
                Player(
                    id=player_id,
                    first_name=name,
                    second_name="Example",
                    web_name=name,
                    team_id=1,
                    position="Midfielder",
                    position_short="MID",
                    status="a",
                    news="",
                    updated_at=now,
                    raw={},
                )
            )
            for days_ago in range(snapshots - 1, -1, -1):
                db.add(
                    PlayerSnapshot(
                        player_id=player_id,
                        refresh_run_id=(old_run.id if days_ago else latest_run.id),
                        captured_at=now - timedelta(days=days_ago),
                        season="2026/27",
                        price=5.0 + player_id,
                        total_points=50,
                        minutes=900,
                        starts=10,
                        team_matches=10,
                        ownership=5.0,
                        value=float(player_id),
                        value_rank=player_id,
                    )
                )
        db.commit()
    return engine, Session


def test_history_player_count_uses_one_grouped_query(tmp_path):
    engine, Session = _seeded_session(tmp_path)
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with Session() as db:
            assert history_player_count(db, "2026/27") == 2
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert len(statements) == 1
    assert "GROUP BY" in statements[0].upper()
    assert "HAVING" in statements[0].upper()


def test_latest_player_options_match_the_full_row_visible_fields(tmp_path):
    engine, Session = _seeded_session(tmp_path)

    with Session() as db:
        full_rows = latest_rows(db, "2026/27")

    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with Session() as db:
            options = latest_player_options(db, "2026/27", exclude_player_id=2)
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    expected = {
        (row["player"].id, row["player"].full_name, row["team"].short_name, row["snapshot"].price)
        for row in full_rows
        if row["player"].id != 2
    }
    actual = {
        (row["player"]["id"], row["player"]["full_name"], row["team"]["short_name"], row["snapshot"]["price"])
        for row in options
    }
    assert actual == expected
    assert len(statements) == 2
    timestamp_statement = statements[0].upper()
    options_statement = statements[1].upper()
    assert "SELECT MAX(PLAYER_SNAPSHOTS.CAPTURED_AT)" in timestamp_statement
    assert "WHERE PLAYER_SNAPSHOTS.SEASON" in timestamp_statement
    assert "SELECT PLAYERS.ID, PLAYERS.FIRST_NAME, PLAYERS.SECOND_NAME, PLAYERS.WEB_NAME, TEAMS.SHORT_NAME, PLAYER_SNAPSHOTS.PRICE" in options_statement
    assert "JOIN PLAYERS" in options_statement
    assert "JOIN TEAMS" in options_statement
    assert "SELECT PLAYER_SNAPSHOTS.ID" not in options_statement


def test_diagnostics_uses_one_grouped_history_count_instead_of_per_player_queries(tmp_path):
    engine, Session = _seeded_session(tmp_path)
    statements = []

    def record_statement(conn, cursor, statement, parameters, context, executemany):
        statements.append(statement)

    event.listen(engine, "before_cursor_execute", record_statement)
    try:
        with Session() as db:
            diagnostics = diagnostics_data(db, "2026/27")
    finally:
        event.remove(engine, "before_cursor_execute", record_statement)

    assert diagnostics["history_points"] == 2
    assert len(statements) == 6
