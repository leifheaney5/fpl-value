"""The ranking harness must be right before any number it produces means anything."""

import pytest

from app.models.ranking import (
    MIN_PLAYERS_PER_GAMEWEEK,
    RankingCandidate,
    forward_points,
)
from app.models.ranking_candidates import _shrink, SHRINKAGE_MATCHES


def test_forward_points_sums_the_horizon():
    points = {1: 2.0, 2: 5.0, 3: 1.0, 4: 8.0, 5: 3.0, 6: 0.0, 7: 4.0}
    assert forward_points(points, 1, 3) == 5.0 + 1.0 + 8.0


def test_a_missing_gameweek_counts_as_zero():
    """A player left out of the squad returns nothing; that is the outcome."""
    points = {1: 2.0, 3: 6.0, 4: 1.0, 5: 0.0}
    assert forward_points(points, 1, 3) == 0.0 + 6.0 + 1.0


def test_a_truncated_horizon_is_none_not_a_short_sum():
    """Crediting a partial window would favour players whose season ended well."""
    points = {1: 2.0, 2: 5.0, 3: 9.0}
    assert forward_points(points, 2, 3) is None
    assert forward_points(points, 1, 2) == 5.0 + 9.0


def test_forward_points_never_includes_the_current_gameweek():
    """The score is known before the fixture; the outcome must start after it."""
    points = {1: 100.0, 2: 1.0, 3: 1.0}
    assert forward_points(points, 1, 2) == 2.0


def test_shrinkage_pulls_a_thin_sample_toward_the_prior():
    """One match of 10.0 points must not outrank a season of 6.0."""
    thin = _shrink(10.0, matches=1.0)
    thick = _shrink(6.0, matches=30.0)
    assert thin < thick, (thin, thick)


def test_shrinkage_leaves_a_large_sample_almost_untouched():
    assert _shrink(6.0, matches=200.0) == pytest.approx(6.0, abs=0.2)


def test_shrinkage_is_monotonic_in_sample_size():
    values = [_shrink(9.0, matches=n) for n in (1, 3, 5, 10, 30, 100)]
    assert values == sorted(values), values


def test_half_weight_is_reached_at_the_declared_sample():
    """The constant should mean what its name says."""
    prior, rate = 2.0, 8.0
    assert _shrink(rate, matches=SHRINKAGE_MATCHES) == pytest.approx(
        (rate + prior) / 2
    )


def test_a_candidate_returning_none_is_excluded_not_scored_zero():
    """Same rule as the recommender: a missing score is not a low score."""
    candidate = RankingCandidate("none", lambda vector: None)
    assert candidate.score({"cur_matches": 0.0}) is None


def test_the_minimum_gameweek_cohort_is_large_enough_to_be_meaningful():
    assert MIN_PLAYERS_PER_GAMEWEEK >= 30
