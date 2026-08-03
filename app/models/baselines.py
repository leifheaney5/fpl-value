"""Baselines a trained model has to beat before it is allowed to ship.

These exist so "the network works" is a measurable claim rather than an
assertion. Every baseline reads ``vector.mask[name]`` before
``vector.values[name]``: a masked feature is zero, and reading that zero as a
measurement is exactly the mistake this project is built to avoid.

``ExistingHeuristic`` reproduces the weighting currently deployed in
``project_next_fixtures``, so the comparison is against what real users see
today, not against a strawman.
"""

from __future__ import annotations

from typing import Protocol

from app.models.features import FeatureVector


class Baseline(Protocol):
    name: str

    def predict(self, vector: FeatureVector) -> float: ...


def _get(vector: FeatureVector, name: str) -> float | None:
    """Read a feature, or None when it is unavailable."""
    if not vector.mask.get(name, False):
        return None
    return vector.values.get(name)


def _first(vector: FeatureVector, *names: str) -> float | None:
    """First available feature among the names given, preferring the earliest."""
    for name in names:
        value = _get(vector, name)
        if value is not None:
            return value
    return None


def _fixture_multiplier(vector: FeatureVector) -> float:
    """Scale by fixture difficulty, mirroring the deployed heuristic.

    Difficulty runs 1 (easiest) to 5 (hardest); 3 is neutral.
    """
    difficulty = _get(vector, "fix_difficulty")
    if difficulty is None:
        return 1.0
    factor = 1.0 + (3.0 - difficulty) * 0.08
    home = _get(vector, "fix_is_home")
    if home:
        factor *= 1.03
    return max(0.75, min(1.25, factor))


class SeasonPointsPerGame:
    name = "season_points_per_game"

    def predict(self, vector: FeatureVector) -> float:
        value = _first(vector, "cur_points_mean", "prev_points_mean")
        return max(0.0, value or 0.0)


class RecentForm:
    name = "recent_form"

    def predict(self, vector: FeatureVector) -> float:
        value = _first(
            vector, "cur_points_last3", "cur_points_mean", "prev_points_mean"
        )
        return max(0.0, value or 0.0)


class PointsPer90Scaled:
    name = "points_per_90_scaled"

    def predict(self, vector: FeatureVector) -> float:
        per_90 = _first(vector, "cur_points_per_90", "prev_points_per_90")
        minutes = _first(vector, "cur_minutes_last3", "cur_minutes_mean", "prev_minutes_mean")
        if per_90 is None or minutes is None:
            return 0.0
        return max(0.0, per_90 * min(minutes, 90.0) / 90.0)


class FixtureAdjusted:
    name = "fixture_adjusted"

    def predict(self, vector: FeatureVector) -> float:
        base = _first(vector, "cur_points_mean", "prev_points_mean")
        if base is None:
            return 0.0
        return max(0.0, base * _fixture_multiplier(vector))


class MinutesWeighted:
    name = "minutes_weighted"

    def predict(self, vector: FeatureVector) -> float:
        """Points per game scaled by how likely the player is to start."""
        base = _first(vector, "cur_points_mean", "prev_points_mean")
        start_rate = _first(vector, "cur_start_rate", "prev_start_rate")
        if base is None:
            return 0.0
        if start_rate is None:
            return max(0.0, base)
        return max(0.0, base * (0.3 + 0.7 * start_rate))


class ExistingHeuristic:
    """The weighting currently deployed in project_next_fixtures.

    0.40 form + 0.35 points per game + 0.25 points per 90, scaled by expected
    minutes and fixture difficulty. This is the bar that matters: beating it is
    what justifies replacing what users see today.
    """

    name = "existing_heuristic"

    def predict(self, vector: FeatureVector) -> float:
        form = _first(vector, "cur_points_last3", "prev_points_mean") or 0.0
        per_game = _first(vector, "cur_points_mean", "prev_points_mean") or 0.0
        per_90 = _first(vector, "cur_points_per_90", "prev_points_per_90") or 0.0
        if form == 0.0 and per_game == 0.0 and per_90 == 0.0:
            return 0.0

        baseline = 0.40 * form + 0.35 * per_game + 0.25 * per_90
        minutes = _first(
            vector, "cur_minutes_last3", "cur_minutes_mean", "prev_minutes_mean"
        )
        minute_factor = 1.0 if minutes is None else min(minutes, 90.0) / 90.0
        return max(0.0, baseline * minute_factor * _fixture_multiplier(vector))


BASELINES: tuple[Baseline, ...] = (
    SeasonPointsPerGame(),
    RecentForm(),
    PointsPer90Scaled(),
    FixtureAdjusted(),
    MinutesWeighted(),
    ExistingHeuristic(),
)
