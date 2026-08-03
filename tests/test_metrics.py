from app.analytics.metrics import (
    cumulative_tier,
    expected_minutes,
    project_next_fixtures,
    reliability_factor,
    rotation_risk,
)


def test_reliability_rewards_secure_minutes():
    secure = reliability_factor(850, 10, 10)
    fringe = reliability_factor(180, 2, 10)
    assert secure > fringe


def test_rotation_risk_orders_players():
    secure, secure_tier, _ = rotation_risk(880, 10, 10, None)
    fringe, fringe_tier, _ = rotation_risk(180, 2, 10, None)
    assert secure is not None and fringe is not None
    assert secure < fringe
    assert secure_tier in {"Low", "Moderate"}
    assert fringe_tier in {"High", "Very High"}


def test_cumulative_tiers():
    assert cumulative_tier(1, 100) == "Top 10%"
    assert cumulative_tier(20, 100) == "Top 25%"
    assert cumulative_tier(40, 100) == "Top 50%"
    assert cumulative_tier(80, 100) == "Bottom 50%"


def test_metrics_return_none_when_the_season_has_not_started():
    assert reliability_factor(0, 0, 0) is None
    assert expected_minutes(0, 0, 0, 1.0) is None
    assert (
        project_next_fixtures(
            form=0.0,
            points_per_game=0.0,
            points_per_90=0.0,
            expected_minutes_value=None,
            availability=1.0,
            fixtures=[{"difficulty": 3, "is_home": True}],
        )
        is None
    )


def test_projection_is_none_without_fixtures_even_when_minutes_are_known():
    assert (
        project_next_fixtures(
            form=4.0,
            points_per_game=4.0,
            points_per_90=4.0,
            expected_minutes_value=80.0,
            availability=1.0,
            fixtures=[],
        )
        is None
    )


def test_metrics_return_a_real_zero_when_the_measurement_is_genuine():
    # The team has played three matches; this player featured in none of them.
    # That is a measured zero, not an absent measurement.
    assert reliability_factor(0, 0, 3) == 0.0
    assert expected_minutes(0, 0, 3, 1.0) == 0.0
