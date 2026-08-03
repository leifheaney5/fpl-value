from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.services.player_intelligence import build_player_intelligence


def _row(*, ownership=5.0, captured_at=None, transfers_in=100, transfers_out=20, previous_in=70, previous_out=10):
    captured_at = captured_at or datetime.now(timezone.utc)
    snapshot = SimpleNamespace(
        ownership=ownership,
        forward_value=2.0,
        expected_minutes=80.0,
        rotation_risk=10.0,
        availability_factor=1.0,
        form=5.0,
        price=6.0,
        captured_at=captured_at,
        raw={"transfers_in_event": transfers_in, "transfers_out_event": transfers_out},
    )
    previous = SimpleNamespace(raw={"transfers_in_event": previous_in, "transfers_out_event": previous_out})
    return {
        "snapshot": snapshot,
        "player": SimpleNamespace(id=1, web_name="Ada", position_short="MID"),
        "team": SimpleNamespace(name="Test FC", short_name="TST"),
        "history": {"1D": {"reference": captured_at - timedelta(days=1), "snapshot": previous}},
    }


def test_lower_ownership_increases_otherwise_equal_differential_score():
    low = build_player_intelligence([_row(ownership=2.0)])[0]
    high = build_player_intelligence([_row(ownership=25.0)])[0]

    assert low["differential"]["score"] > high["differential"]["score"]


def test_missing_transfer_history_is_explicitly_unavailable():
    row = _row()
    row["history"] = {"1D": {"reference": None, "snapshot": None}}

    intelligence = build_player_intelligence([row])[0]

    assert intelligence["transfer_trend"]["classification"] == "Not available"
    assert intelligence["transfer_trend"]["velocity"] is None


def test_snapshot_older_than_two_days_is_marked_stale():
    now = datetime.now(timezone.utc)
    intelligence = build_player_intelligence([_row(captured_at=now - timedelta(days=3))], now=now)[0]

    assert intelligence["provenance"]["freshness"] == "Stale"
