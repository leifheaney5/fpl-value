"""Captaincy is judged on the next gameweek alone, and on every fixture in it."""

from types import SimpleNamespace

import pytest

from app.services.captaincy import fixtures_in_gameweek, next_gameweek_projection


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
