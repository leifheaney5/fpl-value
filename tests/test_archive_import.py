from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import GameweekHistory, Player, Team
from app.services.archive_import import import_archive_season


class StubReader:
    """Serves gameweek rows and the season's element -> code mapping."""

    def __init__(self, rows, players=None):
        self._rows = rows
        self._players = players or []

    def read_gameweeks(self, season_directory):
        return iter(self._rows)

    def read_players(self, season_directory):
        return iter(self._players)


def _row(element, gw, minutes=90, **extra):
    payload = {
        "name": f"Player {element}", "element": str(element), "GW": str(gw),
        "round": str(gw), "minutes": str(minutes), "total_points": "5",
        "goals_scored": "0", "assists": "0", "clean_sheets": "0",
        "opponent_team": "4", "fixture": "10", "was_home": "True",
        "value": "55", "selected": "1000",
        "kickoff_time": "2024-08-17T14:00:00Z",
    }
    payload.update(extra)
    return payload


def _players(mapping):
    """mapping: {element_id: code}"""
    return [{"id": str(element), "code": str(code)} for element, code in mapping.items()]


def _session(tmp_path, name, codes=(1001, 1002)):
    engine = create_engine(
        f"sqlite:///{tmp_path / name}", connect_args={"check_same_thread": False}
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Team(id=1, name="Test", short_name="TST", updated_at=now))
        for index, code in enumerate(codes, start=1):
            db.add(Player(
                id=index, code=code, first_name="A", second_name=f"B{index}",
                web_name=f"P{index}", team_id=1, position="Midfielder",
                position_short="MID", status="a", news="", updated_at=now, raw={},
            ))
        db.commit()
    return Session


def test_import_stores_rows_under_the_named_season(tmp_path):
    Session = _session(tmp_path, "imp.db")
    reader = StubReader(
        [_row(7, 1, starts="1"), _row(8, 1, starts="0", minutes="20")],
        _players({7: 1001, 8: 1002}),
    )

    with Session() as db:
        result = import_archive_season(db, "2024-25", reader)
        assert result["season"] == "2024/25"
        assert result["era"] == "modern"
        assert result["rows_written"] == 2
        assert result["derived_starts"] == 0

        rows = db.scalars(select(GameweekHistory)).all()
        assert {r.season for r in rows} == {"2024/25"}
        assert {r.source for r in rows} == {"archive"}


def test_archive_elements_resolve_through_the_stable_code(tmp_path):
    """The defect this guards: element ids are recycled between seasons.

    Archive element 7 maps to code 1002, which is our player id 2 -- not our
    player id 7, and not our player id 1.
    """
    Session = _session(tmp_path, "identity.db")
    reader = StubReader([_row(7, 1)], _players({7: 1002}))

    with Session() as db:
        import_archive_season(db, "2019-20", reader)
        row = db.scalar(select(GameweekHistory))
        assert row.player_id == 2
        assert row.player_code == 1002


def test_a_departed_player_is_kept_without_a_current_player_row(tmp_path):
    """Survivorship bias guard.

    Code 999999 belongs to nobody in the current game. The row must still be
    stored: dropping it would train the model only on careers that survived.
    """
    Session = _session(tmp_path, "departed.db")
    reader = StubReader([_row(7, 1)], _players({7: 999999}))

    with Session() as db:
        result = import_archive_season(db, "2024-25", reader)
        assert result["rows_written"] == 1
        assert result["departed_players"] == 1
        row = db.scalar(select(GameweekHistory))
        assert row.player_code == 999999
        assert row.player_id is None


def test_an_element_missing_from_the_player_map_is_rejected(tmp_path):
    Session = _session(tmp_path, "nomap.db")
    reader = StubReader([_row(7, 1)], _players({}))

    with Session() as db:
        result = import_archive_season(db, "2024-25", reader)
        assert result["rows_written"] == 0
        assert result["rows_rejected"] == 1


def test_import_flags_derived_starts_for_older_seasons(tmp_path):
    Session = _session(tmp_path, "derived.db")
    reader = StubReader(
        [_row(7, 1, minutes="90"), _row(8, 1, minutes="30")],
        _players({7: 1001, 8: 1002}),
    )

    with Session() as db:
        result = import_archive_season(db, "2019-20", reader)
        assert result["era"] == "minimal"
        assert result["derived_starts"] == 2

        rows = {r.player_id: r for r in db.scalars(select(GameweekHistory)).all()}
        assert rows[1].started is True and rows[1].started_is_derived is True
        assert rows[2].started is False and rows[2].started_is_derived is True
        assert rows[1].starts is None


def test_import_is_idempotent(tmp_path):
    Session = _session(tmp_path, "idem.db")
    rows = [_row(7, 1), _row(7, 2)]
    players = _players({7: 1001})

    with Session() as db:
        first = import_archive_season(db, "2024-25", StubReader(list(rows), players))
        second = import_archive_season(db, "2024-25", StubReader(list(rows), players))
        assert first["rows_written"] == 2
        assert second["rows_written"] == 0
        assert len(db.scalars(select(GameweekHistory)).all()) == 2


def test_two_seasons_do_not_collide_on_the_same_gameweek(tmp_path):
    Session = _session(tmp_path, "seasons.db")
    with Session() as db:
        import_archive_season(
            db, "2023-24", StubReader([_row(7, 1)], _players({7: 1001}))
        )
        import_archive_season(
            db, "2024-25", StubReader([_row(9, 1)], _players({9: 1001}))
        )
        rows = db.scalars(select(GameweekHistory)).all()
        assert len(rows) == 2
        assert {r.season for r in rows} == {"2023/24", "2024/25"}
        # Different element ids, same player, because the code matched.
        assert {r.player_id for r in rows} == {1}


def test_rows_without_an_identity_are_counted_as_rejected(tmp_path):
    Session = _session(tmp_path, "bad.db")
    broken = _row(7, 1)
    broken["element"] = ""
    reader = StubReader([broken], _players({7: 1001}))

    with Session() as db:
        result = import_archive_season(db, "2024-25", reader)
        assert result["rows_read"] == 1
        assert result["rows_written"] == 0
        assert result["rows_rejected"] == 1
