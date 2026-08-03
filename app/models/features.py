"""Point-in-time features for player prediction.

The single contract of this module: **no feature may depend on a row whose
kickoff time is at or after ``as_of``**. Training leakage is the most likely
failure mode of a football model and the hardest to see from the outputs, so the
filter is applied first, before any other computation, and there is a test that
feeds this builder deliberately poisoned future rows and asserts the vector does
not move.

Every feature carries an availability flag. ``mask[name] is False`` means the
feature is unavailable, and ``values[name]`` is then exactly ``0.0``. Zeroing a
masked feature is what stops a model reading a stale number as if it were a
measurement -- the same distinction the rest of this application draws between a
real zero and an absent value.

The mask does double duty. It hides current-season features when predicting
preseason, and it hides features an archive era never recorded. That is what
lets one model serve both preseason and in-season prediction: preseason is
simply the fully-masked case.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable

VERSION = "1.0.0"


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a timestamp to UTC before comparing.

    SQLite returns naive datetimes and PostgreSQL returns aware ones, so
    comparing a stored kickoff time against an aware ``as_of`` raises. Naive
    values are treated as UTC, which is what the ingestion pipeline writes.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class InformationState:
    """What the model is allowed to know at prediction time."""

    PRESEASON = "preseason"
    IN_SEASON = "in_season"


FEATURE_NAMES: tuple[str, ...] = (
    # Fixture context. Describes the match being predicted, so always available.
    "fix_difficulty",
    "fix_is_home",
    "fix_gameweek",
    # Current season. Masked entirely in preseason.
    "cur_matches",
    "cur_minutes_mean",
    "cur_minutes_last3",
    "cur_start_rate",
    "cur_points_mean",
    "cur_points_last3",
    "cur_points_per_90",
    "cur_goals_per_90",
    "cur_assists_per_90",
    "cur_clean_sheet_rate",
    "cur_saves_per_90",
    "cur_bonus_mean",
    "cur_xg_per_90",
    "cur_xa_per_90",
    "cur_price",
    "cur_price_delta",
    "cur_has_xg",
    "cur_has_starts",
    # Previous seasons. Available in both information states.
    "prev_matches",
    "prev_minutes_mean",
    "prev_start_rate",
    "prev_points_mean",
    "prev_points_per_90",
    "prev_goals_per_90",
    "prev_assists_per_90",
    "prev_clean_sheet_rate",
    "prev_saves_per_90",
    "prev_bonus_mean",
    "prev_xg_per_90",
    "prev_xa_per_90",
    "prev_price_end",
    "prev_has_xg",
    "prev_has_starts",
)


@dataclass(frozen=True)
class FeatureVector:
    values: dict[str, float]
    mask: dict[str, bool]
    as_of: datetime | None
    version: str

    def as_list(self) -> list[float]:
        return [self.values[name] for name in FEATURE_NAMES]

    def mask_list(self) -> list[float]:
        return [1.0 if self.mask[name] else 0.0 for name in FEATURE_NAMES]


class _Accumulator:
    """Running totals for one season's worth of rows."""

    def __init__(self, prefix: str) -> None:
        self.prefix = prefix
        self.matches = 0
        self.minutes = 0
        self.points = 0
        self.starts = 0
        self.goals = 0
        self.assists = 0
        self.clean_sheets = 0
        self.saves = 0
        self.bonus = 0
        self.xg = 0.0
        self.xa = 0.0
        self.has_xg = False
        self.has_starts = False
        self.recent_points: list[float] = []
        self.recent_minutes: list[float] = []
        self.first_price: float | None = None
        self.last_price: float | None = None

    def add(self, row: Any) -> None:
        self.matches += 1
        self.minutes += int(getattr(row, "minutes", 0) or 0)
        self.points += int(getattr(row, "points", 0) or 0)
        self.goals += int(getattr(row, "goals", 0) or 0)
        self.assists += int(getattr(row, "assists", 0) or 0)
        self.clean_sheets += int(getattr(row, "clean_sheets", 0) or 0)
        self.saves += int(getattr(row, "saves", 0) or 0)
        self.bonus += int(getattr(row, "bonus", 0) or 0)

        if getattr(row, "started", False):
            self.starts += 1
        if getattr(row, "starts", None) is not None and not getattr(
            row, "started_is_derived", False
        ):
            self.has_starts = True

        expected_goals = getattr(row, "expected_goals", None)
        expected_assists = getattr(row, "expected_assists", None)
        if expected_goals is not None:
            self.xg += float(expected_goals)
            self.has_xg = True
        if expected_assists is not None:
            self.xa += float(expected_assists)

        self.recent_points.append(float(getattr(row, "points", 0) or 0))
        self.recent_minutes.append(float(getattr(row, "minutes", 0) or 0))

        price = getattr(row, "price", None)
        if price is not None:
            if self.first_price is None:
                self.first_price = float(price)
            self.last_price = float(price)


def _set(
    values: dict[str, float],
    mask: dict[str, bool],
    name: str,
    value: float | None,
) -> None:
    """Record a feature. ``None`` means unavailable, and unavailable means zero."""
    if value is None:
        values[name] = 0.0
        mask[name] = False
    else:
        values[name] = float(value)
        mask[name] = True


def _per_90(total: float, minutes: int) -> float | None:
    return None if minutes <= 0 else total * 90.0 / minutes


def _mean(values: list[float]) -> float | None:
    return None if not values else sum(values) / len(values)


def _write_season_block(
    values: dict[str, float],
    mask: dict[str, bool],
    acc: _Accumulator,
    *,
    include_recent: bool,
) -> None:
    prefix = acc.prefix
    played = acc.matches > 0
    minutes = acc.minutes

    _set(values, mask, f"{prefix}_matches", float(acc.matches) if played else None)
    _set(
        values, mask, f"{prefix}_minutes_mean",
        acc.minutes / acc.matches if played else None,
    )
    _set(
        values, mask, f"{prefix}_start_rate",
        acc.starts / acc.matches if played else None,
    )
    _set(
        values, mask, f"{prefix}_points_mean",
        acc.points / acc.matches if played else None,
    )
    _set(values, mask, f"{prefix}_points_per_90", _per_90(acc.points, minutes))
    _set(values, mask, f"{prefix}_goals_per_90", _per_90(acc.goals, minutes))
    _set(values, mask, f"{prefix}_assists_per_90", _per_90(acc.assists, minutes))
    _set(
        values, mask, f"{prefix}_clean_sheet_rate",
        acc.clean_sheets / acc.matches if played else None,
    )
    _set(values, mask, f"{prefix}_saves_per_90", _per_90(acc.saves, minutes))
    _set(
        values, mask, f"{prefix}_bonus_mean",
        acc.bonus / acc.matches if played else None,
    )
    # Expected goals were not recorded before 2022-23. Absent, not zero.
    _set(values, mask, f"{prefix}_xg_per_90", _per_90(acc.xg, minutes) if acc.has_xg else None)
    _set(values, mask, f"{prefix}_xa_per_90", _per_90(acc.xa, minutes) if acc.has_xg else None)

    # Era availability is itself informative: it tells the model how much to
    # trust the neighbouring features. With no matches at all there is no era to
    # describe, so the flag is unavailable rather than a confident "no".
    _set(
        values, mask, f"{prefix}_has_xg",
        (1.0 if acc.has_xg else 0.0) if played else None,
    )
    _set(
        values, mask, f"{prefix}_has_starts",
        (1.0 if acc.has_starts else 0.0) if played else None,
    )

    if include_recent:
        _set(values, mask, f"{prefix}_points_last3", _mean(acc.recent_points[-3:]))
        _set(values, mask, f"{prefix}_minutes_last3", _mean(acc.recent_minutes[-3:]))
        _set(values, mask, f"{prefix}_price", acc.last_price)
        _set(
            values, mask, f"{prefix}_price_delta",
            (acc.last_price - acc.first_price)
            if acc.last_price is not None
            and acc.first_price is not None
            and acc.matches >= 2
            else None,
        )
    else:
        _set(values, mask, f"{prefix}_price_end", acc.last_price)


def build_features(
    history: Iterable[Any],
    target: Any,
    as_of: datetime,
    information_state: str,
) -> FeatureVector:
    """Build the feature vector for one player and one upcoming fixture.

    ``history`` may contain anything; rows at or after ``as_of``, and rows whose
    kickoff time is unknown, are discarded before any computation.
    """
    values: dict[str, float] = {}
    mask: dict[str, bool] = {}

    # Leakage filter. First, unconditionally, before anything else. Both sides
    # are normalised to UTC because SQLite returns naive timestamps and
    # PostgreSQL returns aware ones, and comparing the two raises.
    target_season = getattr(target, "season", None)
    cutoff = _as_utc(as_of)
    past = [
        row
        for row in history
        if _as_utc(getattr(row, "kickoff_time", None)) is not None
        and _as_utc(row.kickoff_time) < cutoff
    ]

    current = _Accumulator("cur")
    previous = _Accumulator("prev")
    for row in past:
        season = getattr(row, "season", None)
        if target_season is not None and season == target_season:
            current.add(row)
        elif target_season is None:
            current.add(row)
        else:
            previous.add(row)

    # Fixture context describes the match being predicted, not the past, so it
    # is available even with no history at all.
    _set(values, mask, "fix_difficulty", float(getattr(target, "difficulty", 3) or 3))
    _set(values, mask, "fix_is_home", 1.0 if getattr(target, "is_home", False) else 0.0)
    _set(values, mask, "fix_gameweek", float(getattr(target, "gameweek", 0) or 0))

    _write_season_block(values, mask, current, include_recent=True)
    _write_season_block(values, mask, previous, include_recent=False)

    if information_state == InformationState.PRESEASON:
        # Preseason is the fully-masked case: nothing about this season is known
        # yet, so the model must predict from the previous season alone.
        for name in FEATURE_NAMES:
            if name.startswith("cur_"):
                values[name] = 0.0
                mask[name] = False

    # Guarantee the ordering and completeness of the schema.
    ordered_values = {name: values.get(name, 0.0) for name in FEATURE_NAMES}
    ordered_mask = {name: mask.get(name, False) for name in FEATURE_NAMES}
    for name in FEATURE_NAMES:
        if not ordered_mask[name]:
            ordered_values[name] = 0.0

    return FeatureVector(
        values=ordered_values,
        mask=ordered_mask,
        as_of=as_of,
        version=VERSION,
    )
