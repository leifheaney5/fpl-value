"""Every page must render a populated preseason database.

Before this increment the derived metrics were all zero, so pages rendered but
lied. Now they are null, so a page that silently assumed a number will raise.
Both failure modes are caught here: the pages must render, and they must not
show a fabricated 0.00 for a measurement that was never taken.
"""

import re

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.refresh import refresh_data

from fakes import FakeClient, PreseasonClient


PAGES = [
    "/",
    "/spreadsheet",
    "/spreadsheet?view=forward",
    "/spreadsheet?view=rotation",
    "/spreadsheet?view=movers",
    "/spreadsheet?view=transfers",
    "/forward",
    "/rotation",
    "/transfers",
    "/movers",
    "/compare",
    "/diagnostics",
    "/schema",
    "/settings",
    "/differentials",
    "/transfer-market",
    "/templates",
    "/recommendation",
]


def _seeded_client(tmp_path, client_class, name):
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


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_preseason_data(tmp_path, path):
    client, _ = _seeded_client(tmp_path, PreseasonClient, "pre.db")
    try:
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_in_season_data(tmp_path, path):
    client, _ = _seeded_client(tmp_path, FakeClient, "live.db")
    try:
        assert client.get(path).status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_a_player_page_renders_in_preseason(tmp_path):
    client, _ = _seeded_client(tmp_path, PreseasonClient, "player.db")
    try:
        assert client.get("/players/10").status_code == 200
    finally:
        app.dependency_overrides.clear()


def test_preseason_pages_do_not_present_unavailable_metrics_as_zero(tmp_path):
    """Derived metrics with no value must not render as a number.

    Observations the FPL API genuinely reports (points, minutes, price,
    ownership, points-per-game) still render as themselves, including when they
    are legitimately zero. The distinction is the whole point.
    """
    client, _ = _seeded_client(tmp_path, PreseasonClient, "honest.db")
    try:
        body = client.get("/spreadsheet").text

        # Every value/reliable/forward chip must be a dash, not a number.
        chips = re.findall(r'class="sheet-chip[^"]*">([^<]*)</span>', body)
        assert chips, "expected value chips to be present"
        for chip in chips:
            assert not re.fullmatch(r"\s*[\d.]+\s*", chip), (
                f"a derived metric rendered as {chip.strip()!r} with no value behind it"
            )

        # And the sort keys must be empty rather than zero, so a click cannot
        # order unavailable players as though they scored lowest.
        assert 'data-sort-value=""' in body
    finally:
        app.dependency_overrides.clear()


def test_exports_leave_unavailable_metrics_empty_rather_than_zero(tmp_path):
    client, _ = _seeded_client(tmp_path, PreseasonClient, "export.db")
    try:
        text = client.get("/exports/current.csv").content.decode("utf-8")
        header = text.split("\r\n")[0].split(",")
        row = text.split("\r\n")[1].split(",")
        cells = dict(zip(header, row))
        for column in ("Value", "Reliable Value", "Forward Value", "Expected Minutes"):
            assert cells.get(column, "") == "", (
                f"the export wrote {cells.get(column)!r} for {column}, "
                "which has no value"
            )
    finally:
        app.dependency_overrides.clear()
