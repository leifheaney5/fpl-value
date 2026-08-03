import math

from app.models.metrics import (
    calibration_error,
    mean_absolute_error,
    root_mean_squared_error,
    spearman_correlation,
    summarise,
)


def test_perfect_prediction_scores_zero_error():
    actual = [1.0, 5.0, 3.0]
    assert mean_absolute_error(actual, actual) == 0.0
    assert root_mean_squared_error(actual, actual) == 0.0


def test_errors_are_positive_and_rmse_punishes_outliers_harder():
    actual = [0.0, 0.0, 0.0]
    predicted = [0.0, 0.0, 9.0]
    assert mean_absolute_error(actual, predicted) == 3.0
    assert root_mean_squared_error(actual, predicted) > 3.0


def test_rank_correlation_detects_ordering_not_magnitude():
    actual = [1.0, 2.0, 3.0, 4.0]
    assert math.isclose(spearman_correlation(actual, [10.0, 20.0, 30.0, 40.0]), 1.0)
    assert math.isclose(spearman_correlation(actual, [4.0, 3.0, 2.0, 1.0]), -1.0)


def test_rank_correlation_handles_ties_without_distortion():
    actual = [1.0, 1.0, 3.0, 4.0]
    predicted = [1.0, 1.0, 3.0, 4.0]
    assert math.isclose(spearman_correlation(actual, predicted), 1.0)


def test_metrics_return_none_rather_than_zero_when_undefined():
    assert mean_absolute_error([], []) is None
    assert root_mean_squared_error([], []) is None
    assert spearman_correlation([1.0], [1.0]) is None
    # A constant series has no variance, so correlation is undefined.
    assert spearman_correlation([1.0, 1.0], [2.0, 2.0]) is None


def test_calibration_is_good_when_quantiles_match_the_distribution():
    actual = [i / 100 for i in range(100)]
    quantiles = [(0.1, 0.5, 0.9)] * 100
    assert calibration_error(actual, quantiles) < 0.05


def test_calibration_detects_an_overconfident_interval():
    actual = [i / 10 for i in range(100)]
    quantiles = [(0.49, 0.50, 0.51)] * 100
    assert calibration_error(actual, quantiles) > 0.3


def test_calibration_is_none_on_a_sample_too_small_to_mean_anything():
    assert calibration_error([1.0, 2.0], [(0.0, 1.0, 2.0)] * 2) is None


def test_summarise_reports_every_metric_by_name():
    result = summarise([1.0, 2.0, 3.0], [1.0, 2.5, 2.0])
    assert set(result) >= {"mae", "rmse", "spearman", "n", "calibration"}
    assert result["n"] == 3
    assert result["calibration"] is None
