from types import SimpleNamespace

import pytest

from app.services.team_recommender import (
    NotReadyError,
    _projected_output,
    recommend_team,
)


def _pool(projected=lambda position, index: 25.0 - index, price_spread=True):
    """A pool with real choice: several candidates per position across clubs."""
    rows = []
    player_id = 1
    for position, count in [("GKP", 6), ("DEF", 14), ("MID", 14), ("FWD", 8)]:
        for index in range(count):
            rows.append(
                {
                    "player": SimpleNamespace(
                        id=player_id,
                        full_name=f"Player {player_id}",
                        web_name=f"P{player_id}",
                        position_short=position,
                    ),
                    "team": SimpleNamespace(
                        id=(player_id % 10) + 1, short_name=f"T{player_id % 10}"
                    ),
                    "snapshot": SimpleNamespace(
                        price=4.0 + (index * 0.5 if price_spread else 0.0),
                        projected_points_5=projected(position, index),
                        points_per_game=None,
                        total_points=100 - index,
                        reliable_value=10.0 - index / 10,
                        forward_value=8.0 - index / 10,
                        availability_factor=1.0,
                        expected_minutes=85.0, upcoming_fixture_count=5,
                        rotation_risk=10.0,
                    ),
                }
            )
            player_id += 1
    return rows


def test_recommender_builds_valid_fpl_squad_under_budget():
    rows = []
    positions = [("GKP", 2), ("DEF", 5), ("MID", 5), ("FWD", 3)]
    player_id = 1
    for position, count in positions:
        for index in range(count):
            rows.append({
                "player": SimpleNamespace(id=player_id, full_name=f"Player {player_id}", position_short=position),
                "team": SimpleNamespace(id=(player_id % 6) + 1, short_name=f"T{player_id % 6}"),
                "snapshot": SimpleNamespace(
                    price=5.0 + (index * 0.2), projected_points_5=25.0 - index,
                    total_points=100 - index, reliable_value=10.0 - index / 10,
                    forward_value=8.0 - index / 10, availability_factor=1.0,
                    # A real snapshot cannot carry a projection without this:
                    # project_next_fixtures returns None when expected minutes
                    # are unknown. The stub omitted it, which is a state the
                    # refresh pipeline cannot produce.
                    expected_minutes=85.0, upcoming_fixture_count=5,
                    rotation_risk=10.0,
                ),
            })
            player_id += 1

    result = recommend_team(rows, 100.0, "best_team")
    selected = result["starting"] + result["bench"]

    assert len(selected) == 15
    assert len(result["starting"]) == 11
    assert len(result["bench"]) == 4
    assert result["spent"] <= 100.0
    assert {item["row"]["player"].position_short for item in selected} == {"GKP", "DEF", "MID", "FWD"}
    club_counts = {}
    for item in selected:
        club_id = item["row"]["team"].id
        club_counts[club_id] = club_counts.get(club_id, 0) + 1
    assert max(club_counts.values()) <= 3
    assert recommend_team(rows, 100.0, "safe")["strategy"] == "safe"
    assert recommend_team(rows, 100.0, "differential")["strategy"] == "differential"


def test_recommender_refuses_when_no_projection_can_distinguish_players():
    """The preseason case that used to yield a cheap squad and a full bank."""
    rows = _pool(projected=lambda position, index: None)

    with pytest.raises(NotReadyError) as excinfo:
        recommend_team(rows, 100.0, "best_team")

    failed = [check["name"] for check in excinfo.value.checks if not check["passed"]]
    assert "projections_available" in failed
    assert "objective_variation" in failed
    assert excinfo.value.activates_when


def test_recommender_refuses_when_every_projection_is_identical():
    """Equal projections cannot rank squads, even though they are present."""
    rows = _pool(projected=lambda position, index: 10.0)

    with pytest.raises(NotReadyError) as excinfo:
        recommend_team(rows, 100.0, "best_team")

    failed = [check["name"] for check in excinfo.value.checks if not check["passed"]]
    assert "objective_variation" in failed


def test_recommender_spends_the_budget_rather_than_hoarding_it():
    """The old tie-break preferred the cheapest squad; it must prefer the best.

    With a wide price range and projections that rise with price, a squad that
    leaves most of the budget unspent is strictly worse.
    """
    rows = _pool(projected=lambda position, index: 5.0 + index * 3.0)

    result = recommend_team(rows, 100.0, "best_team")

    assert result["spent"] <= 100.0
    assert result["remaining"] < 25.0, (
        f"left £{result['remaining']}m unspent, which is the hoarding defect"
    )
    assert result["budget_explanation"]
    assert result["tie_breakers"][0] == "Expected starting-XI points"
    assert result["tie_breakers"][-1] == "Budget utilisation"


def test_recommendation_explains_why_money_remains():
    rows = _pool(projected=lambda position, index: 5.0 + index * 3.0)

    result = recommend_team(rows, 100.0, "best_team")

    assert "budget" in result["budget_explanation"].casefold() or "£" in result["budget_explanation"]
    assert "checks" in result
    assert all(check["passed"] for check in result["checks"])


# --- Regression tests for the 2026-08-04 recommender audit -------------------
#
# The live deployment recommended Crystal Palace's third-choice goalkeeper as
# the best player in the game and captained him. He had 7 points from a single
# 90-minute appearance last season, so his points-per-game rate was 7.0 --
# higher than Haaland's 6.8 from 2953 minutes. The recommender derived a
# projection from that rate because `projected_points_5` was null in preseason.


def _carry_over_pool():
    """Preseason as the FPL API actually serves it.

    No match has been played this season, so `projected_points_5` is null for
    everyone -- but `points_per_game`, `total_points` and `minutes` still hold
    last season's figures. The fringe player is the hazard: a tiny sample with
    a lucky return outranks a season of elite output.
    """
    rows = []
    player_id = 1
    for position, count in [("GKP", 6), ("DEF", 14), ("MID", 14), ("FWD", 8)]:
        for index in range(count):
            fringe = position == "GKP" and index == 0
            rows.append(
                {
                    "player": SimpleNamespace(
                        id=player_id,
                        full_name=f"Player {player_id}",
                        web_name=f"P{player_id}",
                        position_short=position,
                    ),
                    "team": SimpleNamespace(
                        id=(player_id % 10) + 1, short_name=f"T{player_id % 10}"
                    ),
                    "snapshot": SimpleNamespace(
                        price=4.0 + (index * 0.5),
                        projected_points_5=None,
                        # The fringe keeper's rate is the highest in the pool.
                        points_per_game=7.0 if fringe else 6.0 - (index * 0.2),
                        total_points=7 if fringe else 200 - index,
                        minutes=90 if fringe else 2900 - index,
                        starts=1 if fringe else 34,
                        team_matches=0,
                        reliable_value=None,
                        forward_value=None,
                        availability_factor=1.0,
                        expected_minutes=None,
                        rotation_risk=None,
                        ownership=5.0,
                    ),
                }
            )
            player_id += 1
    return rows


def test_last_season_points_per_game_is_not_a_projection():
    """A rate carried over from a finished season is not this season's forecast.

    Returning a number here is what let one appearance outrank a full season.
    """
    row = _carry_over_pool()[0]
    assert _projected_output(row) is None


def test_recommender_refuses_while_only_carry_over_data_exists():
    """Preseason must reach the not-ready panel, not a confident squad."""
    with pytest.raises(NotReadyError):
        recommend_team(_carry_over_pool(), 100.0, "safe")


def test_the_captain_is_never_a_goalkeeper():
    """Doubling a goalkeeper's score is close to always the wrong call.

    Guarded on position rather than on projection alone: whatever the inputs
    say, a keeper is not a captaincy pick.
    """
    for strategy in ("best_team", "safe", "upside", "value"):
        recommendation = recommend_team(_pool(), 100.0, strategy)
        captains = [row for row in recommendation["starting"] if row["captain"]]
        vices = [row for row in recommendation["starting"] if row["vice_captain"]]
        assert len(captains) == 1 and len(vices) == 1
        assert captains[0]["row"]["player"].position_short != "GKP", strategy
        assert vices[0]["row"]["player"].position_short != "GKP", strategy


def test_a_selected_player_is_never_justified_by_an_empty_default():
    """"best available fit" was the fallback when every reason field was null.

    It reads as a judgement while carrying no information at all.
    """
    recommendation = recommend_team(_pool(), 100.0, "best_team")
    for row in recommendation["starting"] + recommendation["bench"]:
        assert row["reason"] != "best available fit"
        assert row["reason"].strip()
