"""Accuracy, ranking and calibration metrics.

Every function returns ``None`` rather than a number when the metric is not
defined for the input -- an empty sample, or a constant series with no variance
to correlate. Returning 0.0 there would read as "perfectly wrong" rather than
"not measurable", which is the same confusion this project removed from its
player metrics.

Rank correlation matters more than absolute error for this application: the
interface ranks players, so ordering them correctly is worth more than getting
any individual total exactly right. Calibration matters because the model is
required to publish floor, median and ceiling, and an interval nobody has
checked is decoration.
"""

from __future__ import annotations

from typing import Sequence

# Coverage is meaningless on a handful of observations.
MINIMUM_CALIBRATION_SAMPLE = 20


def _paired(actual: Sequence[float], predicted: Sequence[float]):
    return [
        (float(a), float(p))
        for a, p in zip(actual, predicted)
        if a is not None and p is not None
    ]


def mean_absolute_error(
    actual: Sequence[float], predicted: Sequence[float]
) -> float | None:
    pairs = _paired(actual, predicted)
    if not pairs:
        return None
    return sum(abs(a - p) for a, p in pairs) / len(pairs)


def root_mean_squared_error(
    actual: Sequence[float], predicted: Sequence[float]
) -> float | None:
    pairs = _paired(actual, predicted)
    if not pairs:
        return None
    return (sum((a - p) ** 2 for a, p in pairs) / len(pairs)) ** 0.5


def _ranks(values: Sequence[float]) -> list[float]:
    """Average ranks, so ties do not distort the correlation."""
    ordered = sorted(range(len(values)), key=lambda i: values[i])
    ranks = [0.0] * len(values)
    index = 0
    while index < len(ordered):
        stop = index
        while (
            stop + 1 < len(ordered)
            and values[ordered[stop + 1]] == values[ordered[index]]
        ):
            stop += 1
        average = (index + stop) / 2.0 + 1.0
        for position in range(index, stop + 1):
            ranks[ordered[position]] = average
        index = stop + 1
    return ranks


def spearman_correlation(
    actual: Sequence[float], predicted: Sequence[float]
) -> float | None:
    pairs = _paired(actual, predicted)
    if len(pairs) < 2:
        return None

    actual_ranks = _ranks([a for a, _ in pairs])
    predicted_ranks = _ranks([p for _, p in pairs])
    n = len(pairs)
    mean_a = sum(actual_ranks) / n
    mean_p = sum(predicted_ranks) / n

    covariance = sum(
        (a - mean_a) * (p - mean_p) for a, p in zip(actual_ranks, predicted_ranks)
    )
    var_a = sum((a - mean_a) ** 2 for a in actual_ranks)
    var_p = sum((p - mean_p) ** 2 for p in predicted_ranks)
    if var_a <= 0 or var_p <= 0:
        # One series is constant: correlation is undefined, not zero.
        return None
    return covariance / (var_a * var_p) ** 0.5


def calibration_error(
    actual: Sequence[float],
    predicted_quantiles: Sequence[tuple[float, float, float]],
    levels: tuple[float, float, float] = (0.1, 0.5, 0.9),
) -> float | None:
    """Mean absolute gap between nominal and empirical coverage.

    For predicted 10th/50th/90th percentiles, the fraction of actuals falling
    below each should be 0.1/0.5/0.9. Zero means perfectly calibrated; a large
    value means the published interval does not mean what it says.
    """
    pairs = [
        (float(a), q)
        for a, q in zip(actual, predicted_quantiles)
        if a is not None and q is not None
    ]
    if len(pairs) < MINIMUM_CALIBRATION_SAMPLE:
        return None

    total = 0.0
    for index, level in enumerate(levels):
        below = sum(1 for a, q in pairs if a <= q[index])
        total += abs(below / len(pairs) - level)
    return total / len(levels)


def summarise(
    actual: Sequence[float],
    predicted: Sequence[float],
    predicted_quantiles: Sequence[tuple[float, float, float]] | None = None,
) -> dict[str, float | None]:
    return {
        "n": len(_paired(actual, predicted)),
        "mae": mean_absolute_error(actual, predicted),
        "rmse": root_mean_squared_error(actual, predicted),
        "spearman": spearman_correlation(actual, predicted),
        "calibration": (
            calibration_error(actual, predicted_quantiles)
            if predicted_quantiles is not None
            else None
        ),
    }
