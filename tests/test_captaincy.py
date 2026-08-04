"""Captaincy is judged on the next gameweek alone, and on every fixture in it."""

from types import SimpleNamespace

import pytest

from app.services.captaincy import (
    captain_candidates,
    fixtures_in_gameweek,
    next_gameweek_projection,
)


def _snapshot(**overrides):
    base = dict(
        form=5.0,
        points_per_game=5.0,
        points_per_90=6.0,
        expected_minutes=85.0,
        availability=1.0,
        upcoming_fixtures=[
            {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
            {"event": 3, "opponent": "TST", "difficulty": 4, "is_home": False},
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_only_fixtures_in_the_target_gameweek_count():
    assert len(fixtures_in_gameweek(_snapshot(), 2)) == 1
    assert fixtures_in_gameweek(_snapshot(), 2)[0]["opponent"] == "OTH"


def test_a_double_gameweek_counts_both_fixtures():
    """Two fixtures in one gameweek is the case captaincy exists to catch."""
    double = _snapshot(upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
        {"event": 2, "opponent": "TST", "difficulty": 3, "is_home": False},
    ])
    single = _snapshot(upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
    ])
    assert len(fixtures_in_gameweek(double, 2)) == 2
    assert next_gameweek_projection(double, 2) > next_gameweek_projection(single, 2)


def test_a_blank_gameweek_projects_zero_not_none():
    """No fixture is a real, measured zero: the player cannot score."""
    blank = _snapshot(upcoming_fixtures=[
        {"event": 3, "opponent": "OTH", "difficulty": 2, "is_home": True},
    ])
    assert next_gameweek_projection(blank, 2) == 0.0


def test_no_expected_minutes_projects_none_not_zero():
    """Preseason. Unknown is not zero; that distinction is load-bearing here."""
    assert next_gameweek_projection(_snapshot(expected_minutes=None), 2) is None


def test_no_target_gameweek_projects_none():
    assert next_gameweek_projection(_snapshot(), None) is None


def _player(name, **overrides):
    snapshot = _snapshot(**overrides)
    snapshot.player = SimpleNamespace(web_name=name, position_short="MID")
    return snapshot


def test_candidates_are_ordered_by_projection():
    rows = [
        _player("Low", form=1.0, points_per_game=1.0, points_per_90=1.0),
        _player("High", form=9.0, points_per_game=9.0, points_per_90=9.0),
    ]
    result = captain_candidates(rows, 2)
    assert [row["player"].web_name for row in result] == ["High", "Low"]


def test_captain_points_are_double_the_projection():
    result = captain_candidates([_player("Ada")], 2)
    assert result[0]["captain_points"] == pytest.approx(result[0]["projection"] * 2)


def test_players_with_no_basis_are_excluded_not_ranked_last():
    """A None projection is unknown. Ranking it last would assert it is worst."""
    rows = [_player("Known"), _player("Unknown", expected_minutes=None)]
    result = captain_candidates(rows, 2)
    assert [row["player"].web_name for row in result] == ["Known"]


def test_a_double_gameweek_is_stated_in_the_reasoning():
    double = _player("Ada", upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
        {"event": 2, "opponent": "TST", "difficulty": 3, "is_home": False},
    ])
    result = captain_candidates([double], 2)
    assert result[0]["fixture_count"] == 2
    assert "two fixtures" in result[0]["reasoning"]


def test_confidence_is_low_when_expected_minutes_are_low():
    rotated = _player("Fringe", expected_minutes=30.0)
    assert captain_candidates([rotated], 2)[0]["confidence"] == "Low"


def test_no_gameweek_yields_no_candidates():
    assert captain_candidates([_player("Ada")], None) == []
