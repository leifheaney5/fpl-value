from datetime import datetime, timezone

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.services.refresh import refresh_data


class FakeClient:
    def bootstrap(self):
        return {
            "teams": [
                {"id": 1, "name": "Test FC", "short_name": "TST"},
                {"id": 2, "name": "Other FC", "short_name": "OTH"},
            ],
            "element_types": [
                {
                    "id": 3,
                    "singular_name": "Midfielder",
                    "singular_name_short": "MID",
                }
            ],
            "events": [{"id": 1, "finished": True}],
            "elements": [
                {
                    "id": 10,
                    "first_name": "Ada",
                    "second_name": "Example",
                    "web_name": "Ada",
                    "team": 1,
                    "element_type": 3,
                    "now_cost": 50,
                    "total_points": 50,
                    "minutes": 900,
                    "starts": 10,
                    "goals_scored": 5,
                    "assists": 4,
                    "clean_sheets": 2,
                    "bonus": 8,
                    "bps": 200,
                    "form": "5.0",
                    "points_per_game": "5.0",
                    "expected_goals": "4.0",
                    "expected_assists": "3.0",
                    "expected_goal_involvements": "7.0",
                    "ict_index": "80.0",
                    "selected_by_percent": "10.0",
                    "status": "a",
                    "news": "",
                }
            ],
        }

    def fixtures(self):
        return [
            {
                "id": 1,
                "event": 1,
                "finished": True,
                "kickoff_time": "2026-08-01T12:00:00Z",
                "team_h": 1,
                "team_a": 2,
                "team_h_difficulty": 3,
                "team_a_difficulty": 3,
            },
            {
                "id": 2,
                "event": 2,
                "finished": False,
                "kickoff_time": "2026-08-08T12:00:00Z",
                "team_h": 2,
                "team_a": 1,
                "team_h_difficulty": 3,
                "team_a_difficulty": 2,
            },
        ]


def test_refresh_pipeline(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'test.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(
        database_url=f"sqlite:///{tmp_path / 'test.db'}"
    )

    with Session() as db:
        run = refresh_data(db, settings, FakeClient())
        assert run.status == "success"
        snapshot = db.scalar(select(PlayerSnapshot))
        assert snapshot is not None
        assert snapshot.value == 10.0
        assert snapshot.reliable_value > 0
        assert snapshot.forward_value > 0
