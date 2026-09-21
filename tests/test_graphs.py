"""The graphs page plots every player in the filtered position, per metric."""

import json
import re

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.refresh import refresh_data

from fakes import CarryOverPreseasonClient, LiveClient


class TwoPositionClient(LiveClient):
    def bootstrap(self):
        payload = super().bootstrap()
        payload["element_types"].append(
            {"id": 4, "singular_name": "Forward", "singular_name_short": "FWD"}
        )
        striker = dict(payload["elements"][0])
        striker.update(
            {
                "id": 11,
                "first_name": "Bo",
                "second_name": "Striker",
                "web_name": "Striker",
                "team": 2,
                "element_type": 4,
                "now_cost": 95,
                "goals_scored": 12,
                "expected_goals": "10.5",
            }
        )
        payload["elements"].append(striker)
        return payload


def _seeded(tmp_path, client_class):
    url = f"sqlite:///{tmp_path / 'graphs.db'}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    with Session() as db:
        refresh_data(db, Settings(database_url=url, current_season="2026/27"), client_class())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


def _points(html):
    match = re.search(
        r'<script id="graph-data" type="application/json">(.*?)</script>', html, re.S
    )
    assert match, "graph data block missing"
    return json.loads(match.group(1))


def test_graph_has_a_point_for_every_player_with_each_metric(tmp_path):
    try:
        response = _seeded(tmp_path, TwoPositionClient).get("/graphs")
        assert response.status_code == 200
        points = {point["name"]: point for point in _points(response.text)}
        assert set(points) == {"Ada Example", "Bo Striker"}
        striker = points["Bo Striker"]
        assert striker["position"] == "FWD"
        assert striker["price"] == 9.5
        assert striker["goals"] == 12
        assert striker["expected_goals"] == 10.5
        assert striker["assists"] == 4
        assert '<a href="/graphs"' in response.text
    finally:
        app.dependency_overrides.clear()


def test_graph_points_follow_the_position_filter(tmp_path):
    try:
        client = _seeded(tmp_path, TwoPositionClient)
        forwards = _points(client.get("/graphs?position=FWD").text)
        assert [point["name"] for point in forwards] == ["Bo Striker"]
        assert _points(client.get("/graphs?position=GKP").text) == []
        # An unknown position is treated as no filter rather than an empty graph.
        assert len(_points(client.get("/graphs?position=nonsense").text)) == 2
    finally:
        app.dependency_overrides.clear()


def test_graph_metric_toggle_falls_back_to_the_default(tmp_path):
    try:
        client = _seeded(tmp_path, TwoPositionClient)
        chosen = client.get("/graphs?metric=assists").text
        assert 'data-metric="assists" aria-current="true"' in chosen
        fallback = client.get("/graphs?metric=drop-table").text
        assert 'data-metric="goals" aria-current="true"' in fallback
        assert "drop-table" not in fallback
    finally:
        app.dependency_overrides.clear()


def test_graph_says_when_totals_are_carried_over(tmp_path):
    try:
        text = _seeded(tmp_path, CarryOverPreseasonClient).get("/graphs").text
        assert "2025/26" in text
    finally:
        app.dependency_overrides.clear()
