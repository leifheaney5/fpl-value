from app.models.baselines import (
    BASELINES,
    ExistingHeuristic,
    FixtureAdjusted,
    MinutesWeighted,
    RecentForm,
    SeasonPointsPerGame,
)
from app.models.features import FEATURE_NAMES, FeatureVector


def _vector(**overrides):
    values = {name: 0.0 for name in FEATURE_NAMES}
    mask = {name: False for name in FEATURE_NAMES}
    for name, value in overrides.items():
        values[name] = value
        mask[name] = True
    for name in ("fix_difficulty", "fix_is_home", "fix_gameweek"):
        mask[name] = True
    values.setdefault("fix_difficulty", 3.0)
    return FeatureVector(values=values, mask=mask, as_of=None, version="test")


def test_every_baseline_is_named_and_callable():
    names = [baseline.name for baseline in BASELINES]
    assert len(names) == len(set(names)), "baseline names must be unique"
    assert len(BASELINES) >= 6

    vector = _vector(cur_points_mean=4.0, cur_points_per_90=4.0, cur_minutes_mean=80.0)
    for baseline in BASELINES:
        result = baseline.predict(vector)
        assert isinstance(result, float)
        assert result >= 0.0, f"{baseline.name} produced negative points"


def test_baselines_return_zero_rather_than_crashing_on_an_empty_vector():
    empty = _vector()
    for baseline in BASELINES:
        assert baseline.predict(empty) == 0.0


def test_recent_form_prefers_recent_points_when_available():
    high = _vector(cur_points_last3=8.0, cur_points_mean=2.0)
    low = _vector(cur_points_last3=1.0, cur_points_mean=2.0)
    assert RecentForm().predict(high) > RecentForm().predict(low)


def test_fixture_adjustment_penalises_hard_fixtures():
    easy = _vector(cur_points_mean=5.0, fix_difficulty=2.0)
    hard = _vector(cur_points_mean=5.0, fix_difficulty=5.0)
    assert FixtureAdjusted().predict(easy) > FixtureAdjusted().predict(hard)


def test_minutes_weighting_discounts_a_rotation_risk():
    nailed = _vector(cur_points_mean=5.0, cur_start_rate=1.0)
    benched = _vector(cur_points_mean=5.0, cur_start_rate=0.1)
    assert MinutesWeighted().predict(nailed) > MinutesWeighted().predict(benched)


def test_a_masked_feature_is_never_read_as_a_real_value():
    """Masked means unavailable. A baseline must check the mask, not the value."""
    masked = _vector()
    # Simulate a stale number sitting behind a False mask.
    masked.values["cur_points_mean"] = 99.0
    assert SeasonPointsPerGame().predict(masked) == 0.0


def test_baselines_fall_back_to_the_previous_season_in_preseason():
    """Preseason masks every current-season feature; prediction must survive."""
    preseason = _vector(prev_points_mean=4.0, prev_points_per_90=5.0,
                        prev_minutes_mean=85.0, prev_start_rate=0.9)
    for baseline in BASELINES:
        assert baseline.predict(preseason) > 0.0, (
            f"{baseline.name} produced nothing in preseason despite previous-season data"
        )


def test_existing_heuristic_matches_the_deployed_weighting():
    """0.40 form + 0.35 ppg + 0.25 p90, scaled by minutes and fixture."""
    vector = _vector(
        cur_points_last3=5.0, cur_points_mean=4.0, cur_points_per_90=6.0,
        cur_minutes_last3=90.0, fix_difficulty=3.0,
    )
    expected = 0.40 * 5.0 + 0.35 * 4.0 + 0.25 * 6.0
    assert abs(ExistingHeuristic().predict(vector) - expected) < 1e-9
