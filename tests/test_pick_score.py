"""Perfect Pick (stored as pick_score): the window it reads, and the number the refresh stores."""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.analytics.contracts import CONTRACTS, MetricStatus
from app.analytics.metrics import pick_score, recent_window
from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.services.refresh import refresh_data

from fakes import LiveClient, PreseasonClient, SquadClient


def test_recent_window_is_the_whole_season_until_three_matches_are_played():
    assert recent_window([], team_matches=2, points=9, minutes=150) == (4.5, 75.0)
    assert recent_window([], team_matches=0, points=0, minutes=0) == (None, None)


def test_recent_window_differences_against_the_checkpoint_three_matches_back():
    checkpoints = [(4, 20, 360), (5, 22, 450), (6, 31, 540), (7, 33, 540)]

    # Eight played: the window opens at the five-match checkpoint.
    points, minutes = recent_window(checkpoints, team_matches=8, points=40, minutes=630)

    assert points == pytest.approx((40 - 22) / 3)
    assert minutes == pytest.approx((630 - 450) / 3)


def test_recent_window_widens_over_a_gap_and_shortens_when_history_is_young():
    # No snapshot at exactly five matches: fall back to the nearest older one.
    assert recent_window([(3, 10, 270)], team_matches=8, points=40, minutes=720) == (
        pytest.approx(30 / 5), pytest.approx(450 / 5),
    )
    # Snapshots only began last week: a two-match window beats none.
    assert recent_window([(6, 30, 500)], team_matches=8, points=40, minutes=680) == (
        pytest.approx(10 / 2), pytest.approx(180 / 2),
    )
    # Nothing older than now, and a corrected total that went backwards.
    assert recent_window([(8, 40, 680)], team_matches=8, points=40, minutes=680) == (None, None)
    assert recent_window([(5, 22, 700)], team_matches=8, points=40, minutes=680) == (None, None)


def test_recent_window_ignores_carried_over_totals_in_a_preseason_reading():
    # Production, September 2026: the zero-match reading held last season's
    # 170 points and 3330 minutes, which disabled the window for everyone.
    checkpoints = [(0, 170, 3330), (2, 19, 225), (3, 29, 360)]

    points, minutes = recent_window(checkpoints, team_matches=4, points=43, minutes=450)

    assert points == pytest.approx(43 / 4)
    assert minutes == pytest.approx(450 / 4)


def _refreshed(tmp_path, client):
    url = f"sqlite:///{tmp_path / 'pick.db'}"
    engine = create_engine(url)
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as db:
        refresh_data(db, Settings(database_url=url, current_season="2026/27"), client)
        return db.scalars(select(PlayerSnapshot)).all()


def test_refresh_stores_pick_score_from_the_deployed_formula(tmp_path):
    (snapshot,) = _refreshed(tmp_path, LiveClient())

    per_match = snapshot.points_per_team_match
    expected = pick_score(
        per_match, per_match, snapshot.minutes / snapshot.team_matches
    ) * snapshot.availability_factor
    assert snapshot.pick_score == pytest.approx(expected, abs=1e-3)
    assert snapshot.metric_status["pick_score"]["status"] == MetricStatus.VALUE
    assert snapshot.pick_rank == 1 and snapshot.pick_tier != "Not Ranked"


def test_refresh_ranks_pick_score_across_the_pool(tmp_path):
    snapshots = _refreshed(tmp_path, SquadClient())

    ranks = sorted(s.pick_rank for s in snapshots if s.pick_rank is not None)
    assert len(ranks) == len(snapshots)
    assert ranks[0] == 1


def test_pick_score_is_withheld_before_a_match_is_played(tmp_path):
    snapshots = _refreshed(tmp_path, PreseasonClient())

    assert all(s.pick_score is None for s in snapshots)
    assert all(
        s.metric_status["pick_score"]["status"] == MetricStatus.NOT_YET_AVAILABLE
        for s in snapshots
    )
    assert "pick_score" in CONTRACTS


def test_spreadsheet_places_pick_score_between_position_percentile_and_reliable(tmp_path):
    from fastapi.testclient import TestClient

    from app.db.session import get_db
    from app.main import app

    url = f"sqlite:///{tmp_path / 'sheet.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as db:
        refresh_data(db, Settings(database_url=url, current_season="2026/27"), SquadClient())
        stored = db.scalars(select(PlayerSnapshot)).first().pick_score

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        html = TestClient(app).get("/spreadsheet").text
    finally:
        app.dependency_overrides.clear()

    labels = [
        html.index(f'<span class="column-label">{label}</span>')
        for label in ("Pos %ile", "Perfect Pick", "Reliable")
    ]
    assert labels == sorted(labels)
    assert f'data-sort-value="{stored}"' in html
