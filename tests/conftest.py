"""Shared fixtures for modelling tests."""

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import GameweekHistory, Player, Team

SEASONS = ("2024/25", "2025/26")
START = datetime(2024, 8, 17, 14, 0, tzinfo=timezone.utc)


@pytest.fixture
def seeded_history_db(tmp_path):
    """Two players, two seasons, ten gameweeks each, with varied outcomes.

    Outcomes vary deliberately: a dataset where every target is identical
    cannot distinguish a model from a constant, so tests built on it would
    pass for the wrong reason.
    """
    engine = create_engine(
        f"sqlite:///{tmp_path / 'history.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)

    now = datetime.now(timezone.utc)
    with Session() as db:
        db.add(Team(id=1, name="Test FC", short_name="TST", updated_at=now))
        for index, code in enumerate((1001, 1002), start=1):
            db.add(
                Player(
                    id=index, code=code, first_name="A", second_name=f"B{index}",
                    web_name=f"P{index}", team_id=1, position="Midfielder",
                    position_short="MID", status="a", news="", updated_at=now,
                    raw={},
                )
            )
        db.flush()

        offset = 0
        for season_index, season in enumerate(SEASONS):
            for gameweek in range(1, 11):
                for player_index, code in enumerate((1001, 1002), start=1):
                    # Player 1001 is a regular starter, 1002 is a fringe player.
                    regular = code == 1001
                    minutes = 90 if regular else (20 if gameweek % 3 else 0)
                    points = (
                        2 + (gameweek % 4) + (2 if regular else 0)
                        if minutes
                        else 0
                    )
                    db.add(
                        GameweekHistory(
                            player_code=code,
                            player_id=player_index,
                            season=season,
                            gameweek=gameweek,
                            fixture_id=season_index * 100 + gameweek,
                            source="archive",
                            opponent="4",
                            opponent_team_id=4,
                            is_home=gameweek % 2 == 0,
                            kickoff_time=START + timedelta(days=offset),
                            captured_at=START + timedelta(days=offset),
                            minutes=minutes,
                            started=minutes >= 60,
                            started_is_derived=False,
                            starts=1 if minutes >= 60 else 0,
                            points=points,
                            goals=1 if points > 4 else 0,
                            assists=0,
                            clean_sheets=0,
                            goals_conceded=1,
                            saves=0,
                            bonus=1 if points > 5 else 0,
                            bps=points * 4,
                            expected_goals=0.4 if regular else 0.05,
                            expected_assists=0.2 if regular else 0.02,
                            price=7.0 + season_index * 0.5,
                            ownership=10.0,
                            raw={},
                        )
                    )
                    offset += 1

        db.commit()

    with Session() as db:
        yield db


@pytest.fixture
def tiny_dataset(seeded_history_db):
    """A small in-season dataset, sufficient to fit and predict."""
    from app.models.dataset import build_dataset
    from app.models.features import InformationState

    return build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
