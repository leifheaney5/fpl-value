"""Stub FPL clients shared across tests.

``FakeClient`` has one finished gameweek and no unfinished successor, which
``season_state()`` classifies as **postseason** -- not a season in progress. For
a live season use ``LiveClient`` and its subclasses below.
``PreseasonClient`` represents the state the live deployment is in today:
fixtures scheduled, none played, every counting stat still zero.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone


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


class CarryOverPreseasonClient(PreseasonClient):
    """The shape the real FPL API actually returns in preseason.

    Observed on the live deployment on 2026-08-03: no fixture has finished, yet
    ``minutes`` and ``total_points`` still hold the previous season's totals.
    Any rate derived from them describes last season while being labelled as
    this one.
    """

    def bootstrap(self):
        payload = super().bootstrap()
        payload["elements"][0].update(
            {
                "total_points": 43,
                "minutes": 1170,
                "starts": 13,
                "points_per_game": "1.8",
            }
        )
        return payload


def _relative(hours: float) -> str:
    """An ISO deadline offset from now.

    The routes call ``datetime.now(timezone.utc)`` themselves, so a fixed
    timestamp would drift into a different season state as the calendar moves.
    Generating relative to now keeps each client in its intended window
    permanently, without freezing time.
    """
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveClient(FakeClient):
    """Gameweek 1 played and confirmed, gameweek 2 open for changes.

    This is ``gameweek_open``: the state the application spends most of the
    season in, and the one no page had ever been rendered in.
    """

    NEXT_DEADLINE_HOURS = 72.0
    NEXT_FINISHED = False
    NEXT_IS_CURRENT = False
    NEXT_IS_NEXT = True
    FIRST_DATA_CHECKED = True
    FIRST_IS_CURRENT = True

    def bootstrap(self):
        payload = super().bootstrap()
        payload["events"] = [
            {
                "id": 1,
                "name": "Gameweek 1",
                "deadline_time": _relative(-168),
                "finished": True,
                "data_checked": self.FIRST_DATA_CHECKED,
                "is_current": self.FIRST_IS_CURRENT,
                "is_next": False,
            },
            {
                "id": 2,
                "name": "Gameweek 2",
                "deadline_time": _relative(self.NEXT_DEADLINE_HOURS),
                "finished": self.NEXT_FINISHED,
                "data_checked": False,
                "is_current": self.NEXT_IS_CURRENT,
                "is_next": self.NEXT_IS_NEXT,
            },
        ]
        return payload

    def fixtures(self):
        first, second = super().fixtures()
        return [
            dict(first, finished=True, kickoff_time=_relative(-166)),
            dict(
                second,
                finished=False,
                kickoff_time=_relative(self.NEXT_DEADLINE_HOURS + 2),
            ),
        ]


class PreDeadlineClient(LiveClient):
    """The next deadline is within the 24-hour window."""

    NEXT_DEADLINE_HOURS = 6.0


class DeadlinePassedClient(LiveClient):
    """Gameweek 2's deadline has gone and its results are not in."""

    NEXT_DEADLINE_HOURS = -2.0
    NEXT_IS_CURRENT = True
    NEXT_IS_NEXT = False
    FIRST_IS_CURRENT = False


class ProvisionalClient(LiveClient):
    """Gameweek 1 has finished but bonus points are not confirmed."""

    FIRST_DATA_CHECKED = False
