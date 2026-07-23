from app.analytics.metrics import (
    cumulative_tier,
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
