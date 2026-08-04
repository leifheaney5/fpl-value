"""Every page must render in the states the season will actually occupy.

Coverage previously jumped from preseason straight to postseason: the test
named ``test_every_page_renders_with_in_season_data`` used a client whose only
gameweek was finished, which ``season_state()`` classifies as ``postseason``.
The four states below are the ones the application is in every week between
21 August and May, and no page had ever been rendered in any of them.
"""

from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import Gameweek
from app.db.session import get_db
from app.main import app
from app.services.captaincy import captain_candidates
from app.services.queries import dashboard_data, latest_snapshot_time
from app.services.refresh import refresh_data
from app.services.season_state import SeasonState, season_state

from fakes import (
    DeadlinePassedClient,
    LiveClient,
    PreDeadlineClient,
    ProvisionalClient,
    SquadClient,
)

PAGES = [
    "/", "/spreadsheet", "/spreadsheet?view=forward", "/spreadsheet?view=movers",
    "/forward", "/rotation", "/transfers", "/movers", "/compare",
    "/diagnostics", "/schema", "/settings", "/differentials",
    "/transfer-market", "/templates", "/recommendation", "/captaincy",
]

STATES = [
    (LiveClient, SeasonState.GAMEWEEK_OPEN),
    (PreDeadlineClient, SeasonState.PRE_DEADLINE),
    (DeadlinePassedClient, SeasonState.DEADLINE_PASSED),
    (ProvisionalClient, SeasonState.PROVISIONAL),
]


def _seeded(tmp_path, client_class, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=url, current_season="2026/27")
    with Session() as db:
        refresh_data(db, settings, client_class())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app), Session


def _state_of(Session):
    with Session() as db:
        gameweeks = db.scalars(
            select(Gameweek).where(Gameweek.season == "2026/27")
        ).all()
        return season_state(
            gameweeks,
            latest_snapshot_time(db, "2026/27"),
            datetime.now(timezone.utc),
        )["state"]


@pytest.mark.parametrize("client_class,expected_state", STATES)
def test_client_produces_the_state_it_claims(tmp_path, client_class, expected_state):
    """The fixture must actually reach the state, or the coverage is fictional.

    Asserted against ``season_state`` rather than scraped from a page: the
    templates render the human label, so an HTML search would pass or fail for
    reasons unrelated to the state the client actually produces.
    """
    _, Session = _seeded(tmp_path, client_class, f"{expected_state}.db")
    try:
        assert _state_of(Session) == expected_state, (
            f"{client_class.__name__} did not produce {expected_state}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("client_class,expected_state", STATES)
@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders(tmp_path, path, client_class, expected_state):
    client, _ = _seeded(tmp_path, client_class, f"{expected_state}-pages.db")
    try:
        response = client.get(path)
        assert response.status_code == 200, (
            f"{path} returned {response.status_code} in {expected_state}"
        )
    finally:
        app.dependency_overrides.clear()


def test_captaincy_names_the_gameweek_it_is_advising_on(tmp_path):
    """A captaincy pick without a gameweek attached is not actionable."""
    client, _ = _seeded(tmp_path, LiveClient, "cap.db")
    try:
        body = client.get("/captaincy").text
        assert "Gameweek 2" in body
    finally:
        app.dependency_overrides.clear()


def test_captaincy_ranks_a_full_squad(tmp_path):
    """The ranking table must actually render, not the not-available panel.

    ``LiveClient`` fields one player, so captaincy stays below its
    fifteen-projection minimum and the page shows "Not available". Without a
    full roster the real output would never be exercised -- the same gap this
    module exists to close.
    """
    client, _ = _seeded(tmp_path, SquadClient, "cap-squad.db")
    try:
        body = client.get("/captaincy").text
        assert "Not available" not in body
        assert "Captain points" in body
        assert "P00" in body
    finally:
        app.dependency_overrides.clear()


def test_a_double_gameweek_outranks_an_identical_single(tmp_path):
    """Team 1 plays twice in GW2; team 2 once. Otherwise the squads match.

    This is the whole reason captaincy uses a next-gameweek projection instead
    of dividing the five-fixture window: that average would rank these equally.
    """
    client, Session = _seeded(tmp_path, SquadClient, "cap-dgw.db")
    try:
        with Session() as db:
            data = dashboard_data(db, "2026/27")
            # The whole squad, not the default top eight: with ten
            # double-gameweek players the shortlist is all doubles, which is
            # the right answer but leaves nothing to compare against.
            candidates = captain_candidates(
                [row["snapshot"] for row in data["rows"]],
                data["season_state"]["next_gameweek"],
                limit=50,
            )
        assert candidates, "expected a ranked shortlist"
        doubles = [row for row in candidates if row["fixture_count"] == 2]
        singles = [row for row in candidates if row["fixture_count"] == 1]
        assert doubles and singles, (
            f"expected both a double and a single gameweek, got "
            f"{[row['fixture_count'] for row in candidates]}"
        )
        assert min(row["projection"] for row in doubles) > max(
            row["projection"] for row in singles
        )
        assert "two fixtures" in doubles[0]["reasoning"]
    finally:
        app.dependency_overrides.clear()
