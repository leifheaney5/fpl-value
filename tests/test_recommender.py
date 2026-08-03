from types import SimpleNamespace

import pytest

from app.services.team_recommender import NotReadyError, recommend_team


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
                        expected_minutes=85.0,
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
