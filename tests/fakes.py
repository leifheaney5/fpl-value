"""Stub FPL clients shared across tests.

``FakeClient`` represents a season in progress: one fixture played, real minutes
and points recorded. ``PreseasonClient`` represents the state the live
deployment is actually in today: fixtures scheduled, none played, every counting
stat still zero.
"""

from __future__ import annotations


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


class PreseasonClient(FakeClient):
    """No match has been played. Nothing derived from match data is knowable."""

    def bootstrap(self):
        payload = super().bootstrap()
        payload["events"] = [
            {
                "id": 1,
                "name": "Gameweek 1",
                "deadline_time": "2026-08-14T17:30:00Z",
                "finished": False,
                "data_checked": False,
                "is_current": False,
                "is_next": True,
            }
        ]
        payload["elements"][0].update(
            {
                "total_points": 0,
                "minutes": 0,
                "starts": 0,
                "goals_scored": 0,
                "assists": 0,
                "clean_sheets": 0,
                "bonus": 0,
                "bps": 0,
                "form": "0.0",
                "points_per_game": "0.0",
            }
        )
        return payload

    def fixtures(self):
        return [dict(fixture, finished=False) for fixture in super().fixtures()]
