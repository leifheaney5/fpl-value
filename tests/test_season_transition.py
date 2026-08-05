"""The preseason to in-season flip, rehearsed before it happens for real.

One finished fixture switches on six behaviours simultaneously. Each is
covered on its own; the transition between them is not, and it happens once,
in public, on 21 August.
"""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.db.session import get_db
from app.main import app
from app.services.queries import dashboard_data
from app.services.refresh import refresh_data

from fakes import CarryOverPreseasonClient, FirstGameweekPlayedClient


def _session(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False), Settings(
        database_url=url, current_season="2026/27"
    )


def _latest(db):
    return db.scalars(
        select(PlayerSnapshot).order_by(PlayerSnapshot.captured_at.desc())
    ).first()


def test_carry_over_labelling_stops_once_a_match_is_played(tmp_path):
    Session, settings = _session(tmp_path, "flip.db")

    with Session() as db:
        refresh_data(db, settings, CarryOverPreseasonClient())
        before = _latest(db)
        assert before.metric_status["value"]["status"] == "previous_season"

    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())
        after = _latest(db)
        assert after.team_matches > 0, "a fixture has finished"
        assert after.metric_status["value"]["status"] != "previous_season", (
            "counting stats are this season's now; the label must clear"
        )


def test_the_sheet_stops_naming_last_season(tmp_path):
    Session, settings = _session(tmp_path, "sheet.db")
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        body = TestClient(app).get("/spreadsheet").text
        assert "2025/26" not in body, (
            "the carry-over banner and column tags must disappear"
        )
    finally:
        app.dependency_overrides.clear()


def test_dormant_features_activate_together(tmp_path):
    """Expected minutes and start rate both require a played match."""
    Session, settings = _session(tmp_path, "activate.db")
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())
        data = dashboard_data(db, "2026/27")
        snapshot = data["rows"][0]["snapshot"]

        assert snapshot.team_matches > 0
        assert snapshot.expected_minutes is not None
        assert snapshot.start_rate is not None
        assert data["readiness"]["expected_minutes"]["state"] == "ready"


def test_the_numbers_are_small_after_one_match(tmp_path):
    """The check that catches a carry-over detection failure.

    After one match a season total should be single digits. A player showing a
    season's worth of points here means last season's data is being reported as
    this season's -- the defect that ranked a one-appearance keeper above
    Haaland, in its most visible form.
    """
    Session, settings = _session(tmp_path, "plausible.db")
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())
        snapshot = _latest(db)
        assert snapshot.total_points < 20, snapshot.total_points
        assert snapshot.minutes <= 90 * snapshot.team_matches


def test_every_page_still_renders_through_the_transition(tmp_path):
    """A refresh that flips six behaviours must not break a page."""
    Session, settings = _session(tmp_path, "pages.db")
    with Session() as db:
        refresh_data(db, settings, CarryOverPreseasonClient())
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        client = TestClient(app)
        for path in (
            "/", "/spreadsheet", "/captaincy", "/recommendation", "/templates",
            "/differentials", "/movers", "/diagnostics", "/transfer-market",
        ):
            assert client.get(path).status_code == 200, path
    finally:
        app.dependency_overrides.clear()
