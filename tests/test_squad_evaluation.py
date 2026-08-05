"""A ranking is only as good as the squad you can afford from it."""

import pytest

from app.models.squad_evaluation import build_squad, squad_points

POSITION_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def _scored(n_per_position=8, price_tenths=50, score=lambda i: 10.0 - i):
    """Players across four positions and ten clubs, priced identically."""
    rows = []
    player_id = 1
    for position in ("GKP", "DEF", "MID", "FWD"):
        for index in range(n_per_position):
            rows.append(
                {
                    "player_code": player_id,
                    "position": position,
                    "club": player_id % 10,
                    "price_tenths": price_tenths,
                    "score": score(index),
                }
            )
            player_id += 1
    return rows


def test_a_squad_has_the_right_shape():
    squad = build_squad(_scored(), budget_tenths=1000)
    assert squad is not None
    assert len(squad) == 15
    counts = {}
    for row in squad:
        counts[row["position"]] = counts.get(row["position"], 0) + 1
    assert counts == POSITION_COUNTS


def test_a_squad_respects_the_budget():
    squad = build_squad(_scored(price_tenths=60), budget_tenths=1000)
    assert squad is not None
    assert sum(row["price_tenths"] for row in squad) <= 1000


def test_no_more_than_three_players_from_one_club():
    squad = build_squad(_scored(n_per_position=20), budget_tenths=1000)
    assert squad is not None
    clubs = {}
    for row in squad:
        clubs[row["club"]] = clubs.get(row["club"], 0) + 1
    assert max(clubs.values()) <= 3


def test_no_player_is_selected_twice():
    squad = build_squad(_scored(n_per_position=20), budget_tenths=1000)
    assert squad is not None
    codes = [row["player_code"] for row in squad]
    assert len(codes) == len(set(codes))


def test_an_unaffordable_pool_yields_no_squad():
    """Refusing is correct; a partial squad would be scored as if complete."""
    assert build_squad(_scored(price_tenths=200), budget_tenths=1000) is None


def test_a_thin_position_yields_no_squad():
    """Five defenders are required; four cannot make a legal squad."""
    rows = [row for row in _scored() if row["position"] != "DEF"]
    rows += [row for row in _scored() if row["position"] == "DEF"][:4]
    assert build_squad(rows, budget_tenths=1000) is None


def test_the_highest_ranked_affordable_players_are_taken():
    """With equal prices there is no reason to skip anyone better."""
    squad = build_squad(_scored(), budget_tenths=1000)
    assert squad is not None
    for position, needed in POSITION_COUNTS.items():
        picked = sorted(
            (row["score"] for row in squad if row["position"] == position),
            reverse=True,
        )
        assert picked == [10.0 - index for index in range(needed)], position


def test_budget_is_reserved_for_slots_still_unfilled():
    """The defect this guards: spending everything, then failing to fill out.

    One very expensive elite player plus a cheap pool. A builder that takes the
    best available without reserving for the remaining fourteen slots ends up
    unable to complete the squad and returns nothing, when a legal squad
    containing that player was affordable.
    """
    rows = _scored(n_per_position=8, price_tenths=40)
    rows.append(
        {
            "player_code": 999,
            "position": "FWD",
            "club": 99,
            "price_tenths": 400,
            "score": 99.0,
        }
    )
    squad = build_squad(rows, budget_tenths=1000)
    assert squad is not None, "a legal squad containing the elite player exists"
    assert sum(row["price_tenths"] for row in squad) <= 1000
    assert 999 in {row["player_code"] for row in squad}


def test_squad_points_sums_the_horizon_for_its_members():
    squad = [{"player_code": 1}, {"player_code": 2}]
    points = {(1, 2): 5.0, (1, 3): 1.0, (2, 2): 2.0, (2, 3): 4.0, (3, 2): 99.0}
    assert squad_points(squad, points, gameweek=1, horizon=2) == 12.0


def test_squad_points_counts_a_missing_gameweek_as_zero():
    squad = [{"player_code": 1}]
    points = {(1, 2): 5.0}
    assert squad_points(squad, points, gameweek=1, horizon=2) == 5.0


def test_squad_points_never_counts_the_current_gameweek():
    """The squad is chosen before the gameweek; the outcome starts after it."""
    squad = [{"player_code": 1}]
    points = {(1, 1): 100.0, (1, 2): 3.0}
    assert squad_points(squad, points, gameweek=1, horizon=1) == 3.0
