from app.analytics.contracts import (
    CONTRACTS,
    MetricStatus,
    MetricValue,
    describe,
)


def test_every_contract_declares_its_provenance():
    assert CONTRACTS
    for name, contract in CONTRACTS.items():
        assert contract.name == name
        assert contract.formula
        assert contract.inputs
        assert contract.input_seasons
        assert contract.target_season
        assert contract.unit
        assert contract.version
        assert contract.null_behaviour
        low, high = contract.valid_range
        assert low < high


def test_a_real_zero_and_an_absent_measurement_render_differently():
    real = MetricValue(0.0, MetricStatus.REAL_ZERO, "Measured as zero", "pts", 2)
    missing = MetricValue(
        None, MetricStatus.NOT_YET_AVAILABLE, "No matches played yet", "pts", 2
    )
    assert real.display == "0.00"
    assert missing.display == "Not available — No matches played yet"
    assert real.is_value is True
    assert missing.is_value is False


def test_a_status_without_a_reason_still_reads_sensibly():
    assert MetricValue(None, MetricStatus.NOT_APPLICABLE).display == "Not applicable"
    assert MetricValue(None, MetricStatus.FAILED).display == "Calculation failed"


def test_describe_builds_a_metric_value_from_a_snapshot_status_map():
    status_map = {
        "expected_minutes": {
            "status": MetricStatus.NOT_YET_AVAILABLE,
            "reason": "No matches played yet this season",
        }
    }
    result = describe("expected_minutes", None, status_map)
    assert result.is_value is False
    assert "No matches played" in result.display
    assert result.unit == "minutes"


def test_describe_falls_back_to_a_value_status_when_none_is_recorded():
    result = describe("expected_minutes", 62.5, {})
    assert result.is_value is True
    assert result.display == "62.5"


def test_describe_treats_an_unrecorded_null_as_unavailable_not_zero():
    result = describe("forward_value", None, {})
    assert result.is_value is False
    assert result.value is None
