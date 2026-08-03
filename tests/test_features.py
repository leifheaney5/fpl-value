from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.features import (
    FEATURE_NAMES,
    InformationState,
    build_features,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _row(offset_weeks, season="2026/27", gameweek=1, **overrides):
    payload = dict(
        season=season, gameweek=gameweek, minutes=90, points=5, started=True,
        started_is_derived=False, starts=1, goals=1, assists=0, clean_sheets=0,
        saves=0, bonus=1, expected_goals=0.5, expected_assists=0.2, price=7.0,
        kickoff_time=NOW - timedelta(weeks=offset_weeks),
    )
    payload.update(overrides)
    return SimpleNamespace(**payload)


def _history(n, season="2026/27", **overrides):
    return [_row(n - i, season=season, gameweek=i + 1, **overrides) for i in range(n)]


def _target(difficulty=3, is_home=True, gameweek=10, season="2026/27"):
    return SimpleNamespace(
        difficulty=difficulty, is_home=is_home, gameweek=gameweek, season=season
    )


def test_feature_vector_has_a_stable_named_schema():
    vector = build_features(_history(6), _target(), NOW, InformationState.IN_SEASON)
    assert tuple(vector.values) == FEATURE_NAMES
    assert set(vector.mask) == set(FEATURE_NAMES)
    assert vector.version


def test_future_rows_cannot_influence_the_result():
    """The leakage guarantee: the most important test in this file."""
    past = _history(6)
    future = [
        _row(
            -(i + 1), gameweek=50 + i, minutes=90, points=99, goals=5, assists=5,
            bonus=3, expected_goals=4.0, expected_assists=4.0, price=99.0,
        )
        for i in range(6)
    ]

    clean = build_features(past, _target(), NOW, InformationState.IN_SEASON)
    poisoned = build_features(
        past + future, _target(), NOW, InformationState.IN_SEASON
    )

    assert clean.values == poisoned.values, (
        "a row after as_of changed the feature vector: this is training leakage"
    )
    assert clean.mask == poisoned.mask


def test_a_row_exactly_at_the_cutoff_is_excluded():
    at_cutoff = [_row(0)]
    assert at_cutoff[0].kickoff_time == NOW
    empty = build_features([], _target(), NOW, InformationState.IN_SEASON)
    same = build_features(at_cutoff, _target(), NOW, InformationState.IN_SEASON)
    assert same.values == empty.values


def test_naive_and_aware_timestamps_compare_without_raising():
    """SQLite returns naive datetimes; PostgreSQL returns aware ones.

    Comparing the two raises, which crashed prediction on the serving path
    where `as_of` is an aware `datetime.now(timezone.utc)` and the stored
    kickoff times came back naive from SQLite.
    """
    naive = _row(3)
    naive.kickoff_time = naive.kickoff_time.replace(tzinfo=None)

    result = build_features(
        [naive], _target(), NOW, InformationState.IN_SEASON
    )
    assert result.values["cur_matches"] == 1.0

    aware_history = build_features(
        [_row(3)], _target(), NOW, InformationState.IN_SEASON
    )
    assert result.values == aware_history.values


def test_rows_without_a_kickoff_time_are_excluded():
    """A row whose position in time is unknown cannot be proven to be past."""
    undated = [_row(3, kickoff_time=None)]
    empty = build_features([], _target(), NOW, InformationState.IN_SEASON)
    result = build_features(undated, _target(), NOW, InformationState.IN_SEASON)
    assert result.values == empty.values


def test_preseason_masks_every_current_season_feature():
    vector = build_features(_history(6), _target(), NOW, InformationState.PRESEASON)
    current = [name for name in FEATURE_NAMES if name.startswith("cur_")]
    assert current
    assert all(vector.mask[name] is False for name in current)
    assert all(vector.values[name] == 0.0 for name in current), (
        "a masked feature must be zeroed so the model cannot read a stale value"
    )


def test_in_season_reveals_current_season_features():
    vector = build_features(_history(6), _target(), NOW, InformationState.IN_SEASON)
    assert any(vector.mask[name] for name in FEATURE_NAMES if name.startswith("cur_"))
    assert vector.values["cur_matches"] == 6.0


def test_previous_season_features_survive_the_preseason_mask():
    history = _history(30, season="2025/26")
    vector = build_features(history, _target(), NOW, InformationState.PRESEASON)
    previous = [name for name in FEATURE_NAMES if name.startswith("prev_")]
    assert any(vector.mask[name] for name in previous), (
        "preseason must still see the previous season; that is all it has"
    )
    assert vector.values["prev_matches"] == 30.0


def test_current_and_previous_seasons_are_kept_apart():
    history = _history(4, season="2026/27") + _history(20, season="2025/26")
    vector = build_features(history, _target(), NOW, InformationState.IN_SEASON)
    assert vector.values["cur_matches"] == 4.0
    assert vector.values["prev_matches"] == 20.0


def test_no_history_produces_a_fully_masked_vector_not_a_crash():
    vector = build_features([], _target(), NOW, InformationState.IN_SEASON)
    assert tuple(vector.values) == FEATURE_NAMES
    assert not any(
        vector.mask[name]
        for name in FEATURE_NAMES
        if name.startswith(("cur_", "prev_"))
    )
    assert vector.mask["fix_difficulty"] is True


def test_per_ninety_rates_are_masked_when_no_minutes_were_played():
    history = _history(3, minutes=0, points=0, started=False, starts=0)
    vector = build_features(history, _target(), NOW, InformationState.IN_SEASON)
    assert vector.mask["cur_points_per_90"] is False
    assert vector.values["cur_points_per_90"] == 0.0
    # Matches played is still a real observation.
    assert vector.mask["cur_matches"] is True
    assert vector.values["cur_matches"] == 3.0


def test_era_availability_is_itself_a_feature():
    with_xg = _history(4, expected_goals=0.5)
    without_xg = _history(4, expected_goals=None, expected_assists=None)
    assert build_features(
        with_xg, _target(), NOW, InformationState.IN_SEASON
    ).values["cur_has_xg"] == 1.0
    assert build_features(
        without_xg, _target(), NOW, InformationState.IN_SEASON
    ).values["cur_has_xg"] == 0.0


def test_derived_starts_are_flagged_so_the_model_can_discount_them():
    derived = _history(4, starts=None, started_is_derived=True)
    observed = _history(4, starts=1, started_is_derived=False)
    assert build_features(
        derived, _target(), NOW, InformationState.IN_SEASON
    ).values["cur_has_starts"] == 0.0
    assert build_features(
        observed, _target(), NOW, InformationState.IN_SEASON
    ).values["cur_has_starts"] == 1.0


def test_fixture_features_describe_the_target_not_the_past():
    easy = build_features(
        _history(4), _target(difficulty=2, is_home=True), NOW,
        InformationState.IN_SEASON,
    )
    hard = build_features(
        _history(4), _target(difficulty=5, is_home=False), NOW,
        InformationState.IN_SEASON,
    )
    assert easy.values["fix_difficulty"] == 2.0
    assert hard.values["fix_difficulty"] == 5.0
    assert easy.values["fix_is_home"] == 1.0
    assert hard.values["fix_is_home"] == 0.0


def test_masked_features_are_always_zero():
    """Invariant relied on by every consumer: masked implies zero."""
    for state in (InformationState.PRESEASON, InformationState.IN_SEASON):
        vector = build_features(_history(3), _target(), NOW, state)
        for name in FEATURE_NAMES:
            if not vector.mask[name]:
                assert vector.values[name] == 0.0, f"{name} masked but non-zero"
