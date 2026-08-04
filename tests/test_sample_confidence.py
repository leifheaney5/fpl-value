"""A rate is only as trustworthy as the sample behind it.

Kepa showed PPG 2.00 from 90 minutes; Gabriel showed 6.50 from 2750. Rendered
identically, they read as equally solid. That equivalence is what let a keeper
with one appearance and a rate of 7.0 outrank Haaland in the recommender, and
the same typography is still what a human reads on the sheet.
"""

from types import SimpleNamespace

import pytest

from app.analytics.metrics import sample_confidence

FULL_MATCH = 90.0


def _snap(minutes, starts=0):
    return SimpleNamespace(minutes=minutes, starts=starts)


def test_a_single_appearance_is_low_confidence():
    level, label = sample_confidence(_snap(90, 1))
    assert level == "low"
    assert "1 start" in label or "90" in label


def test_a_full_season_is_high_confidence():
    level, _ = sample_confidence(_snap(3200, 36))
    assert level == "high"


def test_a_partial_season_is_medium_confidence():
    level, _ = sample_confidence(_snap(1200, 14))
    assert level == "medium"


def test_no_minutes_has_no_confidence_rather_than_low():
    """Zero minutes is not a thin sample; it is no sample."""
    level, label = sample_confidence(_snap(0, 0))
    assert level == "none"
    assert label


def test_confidence_rises_monotonically_with_minutes():
    order = {"none": 0, "low": 1, "medium": 2, "high": 3}
    levels = [sample_confidence(_snap(m))[0] for m in (0, 90, 500, 1200, 2500, 3400)]
    ranks = [order[level] for level in levels]
    assert ranks == sorted(ranks), levels
