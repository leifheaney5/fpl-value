"""Metric contracts and the value/status pair every derived metric carries.

A derived metric column being ``NULL`` says only that there is no number. The
contract says what the number would have meant, and the recorded status says why
it is absent. Together they let the interface write "Not available — no matches
played yet" where it used to write "0.00".
"""

from __future__ import annotations

from dataclasses import dataclass


class MetricStatus:
    """Why a metric holds the value it holds.

    ``REAL_ZERO`` and ``VALUE`` are measurements. Everything else is an absence,
    and an absence must never be rendered as a number.
    """

    VALUE = "value"
    REAL_ZERO = "real_zero"
    MISSING = "missing"
    NOT_YET_AVAILABLE = "not_yet_available"
    NOT_APPLICABLE = "not_applicable"
    NOT_CALCULATED = "not_calculated"
    FAILED = "failed"
    STALE = "stale"
    INVALID = "invalid"
    SUPPRESSED_LOW_CONFIDENCE = "suppressed_low_confidence"


VALUE_STATUSES = frozenset({MetricStatus.VALUE, MetricStatus.REAL_ZERO})

STATUS_LABELS = {
    MetricStatus.MISSING: "Not available",
    MetricStatus.NOT_YET_AVAILABLE: "Not available",
    MetricStatus.NOT_APPLICABLE: "Not applicable",
    MetricStatus.NOT_CALCULATED: "Not calculated",
    MetricStatus.FAILED: "Calculation failed",
    MetricStatus.STALE: "Stale",
    MetricStatus.INVALID: "Invalid source data",
    MetricStatus.SUPPRESSED_LOW_CONFIDENCE: "Suppressed for low confidence",
}


@dataclass(frozen=True)
class MetricValue:
    """A number, or an explained absence of one."""

    value: float | None
    status: str
    reason: str = ""
    unit: str = ""
    precision: int = 2

    @property
    def is_value(self) -> bool:
        return self.status in VALUE_STATUSES and self.value is not None

    @property
    def display(self) -> str:
        if self.is_value:
            return f"{self.value:.{self.precision}f}"
        label = STATUS_LABELS.get(self.status, "Not available")
        return f"{label} — {self.reason}" if self.reason else label

    def __str__(self) -> str:  # pragma: no cover - convenience for templates
        return self.display


@dataclass(frozen=True)
class MetricContract:
    """Everything a reader needs to interpret a derived metric."""

    name: str
    formula: str
    inputs: tuple[str, ...]
    input_seasons: tuple[str, ...]
    target_season: str
    unit: str
    valid_range: tuple[float, float]
    minimum_sample: int
    null_behaviour: str
    precision: int = 2
    version: str = "1.0.0"


def _contract(**kwargs) -> MetricContract:
    return MetricContract(**kwargs)


CONTRACTS: dict[str, MetricContract] = {
    "value": _contract(
        name="value",
        formula="total_points / price",
        inputs=("total_points", "price"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 200.0),
        minimum_sample=1,
        null_behaviour=(
            "Null when price is not positive, and before the team has played a "
            "match: a points-per-million rate needs opportunity in its "
            "denominator."
        ),
    ),
    "reliability_factor": _contract(
        name="reliability_factor",
        formula=(
            "min(minutes / sample_minutes, 1) * "
            "(0.60 * minutes / (team_matches * 90) + 0.40 * starts / team_matches)"
        ),
        inputs=("minutes", "starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="ratio",
        valid_range=(0.0, 1.0),
        minimum_sample=1,
        null_behaviour="Null when the team has played no matches this season.",
        precision=3,
    ),
    "reliable_value": _contract(
        name="reliable_value",
        formula="value * reliability_factor",
        inputs=("value", "reliability_factor"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 200.0),
        minimum_sample=1,
        null_behaviour="Null when either input is null.",
    ),
    "start_rate": _contract(
        name="start_rate",
        formula="100 * starts / team_matches",
        inputs=("starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="%",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour=(
            "Null when the team has played no matches this season. Never divide "
            "a previous-season start count by a current-season match count."
        ),
        precision=1,
    ),
    "points_per_90": _contract(
        name="points_per_90",
        formula="total_points * 90 / minutes",
        inputs=("total_points", "minutes"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per 90",
        valid_range=(0.0, 30.0),
        minimum_sample=1,
        null_behaviour=(
            "Null when the team has played no matches this season. The "
            "preseason bootstrap still reports the previous season's minutes "
            "and points, so a rate built from them would describe a season "
            "that has ended."
        ),
        precision=3,
    ),
    "expected_minutes": _contract(
        name="expected_minutes",
        formula=(
            "(0.65 * minutes / team_matches + 0.35 * 90 * starts / team_matches) "
            "* availability_factor"
        ),
        inputs=("minutes", "starts", "team_matches", "availability_factor"),
        input_seasons=("current",),
        target_season="current",
        unit="minutes",
        valid_range=(0.0, 90.0),
        minimum_sample=1,
        null_behaviour=(
            "Null before the season starts. A preseason estimator built on "
            "previous-season inputs is required to fill this gap and is not yet "
            "implemented."
        ),
        precision=1,
    ),
    "projected_points_5": _contract(
        name="projected_points_5",
        formula=(
            "sum over upcoming fixtures of "
            "(0.40*form + 0.35*points_per_game + 0.25*points_per_90) "
            "* expected_minutes/90 * availability * difficulty * home_factor"
        ),
        inputs=(
            "form",
            "points_per_game",
            "points_per_90",
            "expected_minutes",
            "availability_factor",
            "upcoming_fixtures",
        ),
        input_seasons=("current",),
        target_season="current",
        unit="pts",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour=(
            "Null when no upcoming fixtures are known or expected minutes are null."
        ),
    ),
    "forward_value": _contract(
        name="forward_value",
        formula="projected_points_5 / price",
        inputs=("projected_points_5", "price"),
        input_seasons=("current",),
        target_season="current",
        unit="pts per £m",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when the projection is null or price is not positive.",
    ),
    "rotation_risk": _contract(
        name="rotation_risk",
        formula=(
            "100 * weighted mean of (1 - start share) and (1 - minute share), "
            "season and recent windows"
        ),
        inputs=("minutes", "starts", "team_matches"),
        input_seasons=("current",),
        target_season="current",
        unit="score",
        valid_range=(0.0, 100.0),
        minimum_sample=1,
        null_behaviour="Null when the team has played no matches this season.",
        precision=1,
    ),
}


def describe(
    metric: str,
    value: float | None,
    status_map: dict[str, dict[str, str]] | None,
) -> MetricValue:
    """Pair a stored metric with its recorded status.

    When no status was recorded, a present number is a value and an absent one is
    unavailable. The fallback deliberately never invents ``REAL_ZERO``: claiming
    a measurement happened is the failure mode this whole module exists to stop.
    """
    contract = CONTRACTS.get(metric)
    unit = contract.unit if contract else ""
    precision = contract.precision if contract else 2

    recorded = (status_map or {}).get(metric) or {}
    status = recorded.get("status")
    reason = recorded.get("reason", "")

    if status is None:
        status = MetricStatus.VALUE if value is not None else MetricStatus.MISSING
        if value is None and contract is not None:
            reason = contract.null_behaviour

    return MetricValue(
        value=value,
        status=status,
        reason=reason,
        unit=unit,
        precision=precision,
    )
