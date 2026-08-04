"""Where the season is, and whether a feature can run.

Season state was previously inferred ad hoc wherever a page needed it, which
meant different pages could disagree about whether the season had started. This
module is the single answer, and it is a pure function of the stored gameweek
calendar so it can be tested without a database.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable


class SeasonState:
    UNINITIALIZED = "uninitialized"
    FPL_UNAVAILABLE = "fpl_unavailable"
    PRE_LAUNCH = "pre_launch"
    PRESEASON = "preseason"
    GAMEWEEK_OPEN = "gameweek_open"
    PRE_DEADLINE = "pre_deadline"
    DEADLINE_PASSED = "deadline_passed"
    LIVE = "live"
    PROVISIONAL = "provisional"
    FINALIZED = "finalized"
    INTERNATIONAL_BREAK = "international_break"
    POSTSEASON = "postseason"
    HISTORICAL_SEASON = "historical_season"


class Readiness:
    READY = "ready"
    DEGRADED = "degraded"
    FALLBACK = "fallback"
    NOT_READY = "not_ready"
    STALE = "stale"
    ERROR = "error"


STATE_LABELS = {
    SeasonState.UNINITIALIZED: "Not initialised",
    SeasonState.FPL_UNAVAILABLE: "FPL data unavailable",
    SeasonState.PRE_LAUNCH: "Before the season is published",
    SeasonState.PRESEASON: "Preseason",
    SeasonState.GAMEWEEK_OPEN: "Gameweek open",
    SeasonState.PRE_DEADLINE: "Deadline approaching",
    SeasonState.DEADLINE_PASSED: "Deadline passed",
    SeasonState.LIVE: "Gameweek live",
    SeasonState.PROVISIONAL: "Provisional results",
    SeasonState.FINALIZED: "Results final",
    SeasonState.INTERNATIONAL_BREAK: "International break",
    SeasonState.POSTSEASON: "Season complete",
    SeasonState.HISTORICAL_SEASON: "Historical season",
}

# A deadline this close is imminent enough to change what the interface should
# be showing; a next deadline this far away means there is no football on.
PRE_DEADLINE_HOURS = 24
INTERNATIONAL_BREAK_DAYS = 10
STALE_CAPTURE_HOURS = 48


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a timestamp to UTC; SQLite hands back naive datetimes."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _result(
    state: str,
    explanation: str,
    *,
    current: Any = None,
    upcoming: Any = None,
    now: datetime,
    stale: bool = False,
) -> dict[str, Any]:
    deadline = _as_utc(getattr(upcoming, "deadline_time", None))
    return {
        "state": state,
        "label": STATE_LABELS.get(state, state),
        "current_gameweek": getattr(current, "number", None),
        "next_gameweek": getattr(upcoming, "number", None),
        "next_deadline": deadline,
        "seconds_to_deadline": (
            (deadline - now).total_seconds() if deadline is not None else None
        ),
        "data_is_stale": stale,
        "explanation": explanation,
    }


def season_state(
    gameweeks: Iterable[Any],
    latest_snapshot_at: datetime | None,
    now: datetime,
) -> dict[str, Any]:
    """Classify where the season is.

    Accepts anything with ``number``, ``deadline_time``, ``finished``,
    ``data_checked``, ``is_current`` and ``is_next`` attributes, so it works
    against ORM rows and plain test doubles alike.
    """
    now = _as_utc(now)
    gameweeks = sorted(gameweeks, key=lambda item: item.number)

    if not gameweeks:
        return _result(
            SeasonState.UNINITIALIZED,
            "No gameweek calendar has been collected yet. Run a refresh to "
            "populate the season.",
            now=now,
        )

    captured = _as_utc(latest_snapshot_at)
    if captured is None:
        return _result(
            SeasonState.FPL_UNAVAILABLE,
            "The gameweek calendar exists but no player snapshot has been "
            "captured, so nothing can be calculated.",
            now=now,
        )

    stale = (now - captured) > timedelta(hours=STALE_CAPTURE_HOURS)

    finished = [item for item in gameweeks if item.finished]
    unfinished = [item for item in gameweeks if not item.finished]

    if not unfinished:
        return _result(
            SeasonState.POSTSEASON,
            "Every gameweek is complete. The season is over.",
            current=finished[-1] if finished else None,
            now=now,
            stale=stale,
        )

    current = next(
        (item for item in gameweeks if item.is_current),
        finished[-1] if finished else None,
    )
    upcoming = next(
        (item for item in unfinished if item.is_next),
        unfinished[0],
    )
    deadline = _as_utc(getattr(upcoming, "deadline_time", None))

    if not finished:
        # No match has been played. Even inside the pre-deadline window this is
        # still preseason for every metric derived from match data.
        if deadline is None:
            return _result(
                SeasonState.PRE_LAUNCH,
                "The season is published but no deadline has been set yet.",
                upcoming=upcoming,
                now=now,
                stale=stale,
            )
        if deadline <= now:
            return _result(
                SeasonState.DEADLINE_PASSED,
                f"The gameweek {upcoming.number} deadline has passed and the "
                "first results are not in yet.",
                current=upcoming,
                now=now,
                stale=stale,
            )
        if (deadline - now) <= timedelta(hours=PRE_DEADLINE_HOURS):
            return _result(
                SeasonState.PRE_DEADLINE,
                f"The gameweek {upcoming.number} deadline is within "
                f"{PRE_DEADLINE_HOURS} hours.",
                upcoming=upcoming,
                now=now,
                stale=stale,
            )
        return _result(
            SeasonState.PRESEASON,
            "No match has been played this season, so metrics derived from "
            "match data are not available yet.",
            upcoming=upcoming,
            now=now,
            stale=stale,
        )

    if current is not None and current.is_current and not current.finished:
        current_deadline = _as_utc(getattr(current, "deadline_time", None))
        if current_deadline is not None and current_deadline <= now:
            return _result(
                SeasonState.DEADLINE_PASSED,
                f"The gameweek {current.number} deadline has passed and "
                "results are still being played out.",
                current=current,
                upcoming=upcoming,
                now=now,
                stale=stale,
            )

    if current is not None and current.finished and not current.data_checked:
        return _result(
            SeasonState.PROVISIONAL,
            f"Gameweek {current.number} has finished but bonus points and "
            "final data are not confirmed.",
            current=current,
            upcoming=upcoming,
            now=now,
            stale=stale,
        )

    if deadline is not None and (deadline - now) <= timedelta(hours=PRE_DEADLINE_HOURS):
        return _result(
            SeasonState.PRE_DEADLINE,
            f"The gameweek {upcoming.number} deadline is within "
            f"{PRE_DEADLINE_HOURS} hours.",
            current=current,
            upcoming=upcoming,
            now=now,
            stale=stale,
        )

    if deadline is not None and (deadline - now) >= timedelta(
        days=INTERNATIONAL_BREAK_DAYS
    ):
        return _result(
            SeasonState.INTERNATIONAL_BREAK,
            f"The next deadline is more than {INTERNATIONAL_BREAK_DAYS} days "
            "away, so there is a break in the fixture list.",
            current=current,
            upcoming=upcoming,
            now=now,
            stale=stale,
        )

    return _result(
        SeasonState.GAMEWEEK_OPEN,
        f"Gameweek {upcoming.number} is open for changes.",
        current=current,
        upcoming=upcoming,
        now=now,
        stale=stale,
    )


@dataclass(frozen=True)
class FeatureRequirement:
    """What a feature needs before it is allowed to show a result."""

    required_inputs: tuple[str, ...]
    supported_states: tuple[str, ...]
    minimum_sample: dict[str, int]
    activates_when: str
    fallback: str
    optional_inputs: tuple[str, ...] = ()
    freshness_hours: int = 48


IN_SEASON_STATES = (
    SeasonState.GAMEWEEK_OPEN,
    SeasonState.PRE_DEADLINE,
    SeasonState.DEADLINE_PASSED,
    SeasonState.LIVE,
    SeasonState.PROVISIONAL,
    SeasonState.FINALIZED,
    SeasonState.INTERNATIONAL_BREAK,
    SeasonState.POSTSEASON,
)

FEATURES: dict[str, FeatureRequirement] = {
    "projections": FeatureRequirement(
        required_inputs=("projections_available",),
        optional_inputs=("player_count",),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"projections_available": 15},
        activates_when=(
            "Projections activate once at least one match has been played and "
            "the model can estimate expected minutes."
        ),
        fallback=(
            "No fallback is active. A preseason projection model built on "
            "previous-season data is not implemented."
        ),
    ),
    "expected_minutes": FeatureRequirement(
        required_inputs=("team_matches",),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"team_matches": 1},
        activates_when=(
            "Expected minutes activate once teams have played a match this "
            "season."
        ),
        fallback="None. Preseason minutes estimation is not implemented.",
    ),
    "movers": FeatureRequirement(
        required_inputs=("snapshot_count",),
        supported_states=(SeasonState.PRESEASON,) + IN_SEASON_STATES,
        minimum_sample={"snapshot_count": 2},
        activates_when=(
            "Movement needs at least two snapshots to compare, so it activates "
            "one refresh after the first."
        ),
        fallback="None. A single snapshot cannot show movement.",
    ),
    "recommendations": FeatureRequirement(
        required_inputs=("projections_available", "player_count"),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"projections_available": 15, "player_count": 15},
        activates_when=(
            "The team builder activates once enough players have projections "
            "that differ from one another."
        ),
        fallback=(
            "None. A squad built from indistinguishable inputs would look like "
            "a recommendation without being one."
        ),
    ),
    "predictions": FeatureRequirement(
        required_inputs=("model_available", "history_rows"),
        optional_inputs=("player_count",),
        supported_states=(SeasonState.PRESEASON,) + IN_SEASON_STATES,
        minimum_sample={"model_available": 1, "history_rows": 1},
        activates_when=(
            "Model predictions activate once a trained artefact is available "
            "that has cleared the evaluation gate in docs/MODEL_EVALUATION.md."
        ),
        fallback=(
            "The transparent projected_points_5 heuristic remains in use. It is "
            "the strongest in-season baseline measured, so this is a considered "
            "fallback rather than a stopgap."
        ),
    ),
    "differentials": FeatureRequirement(
        required_inputs=("projections_available",),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"projections_available": 15},
        activates_when=(
            "Differential scores activate once projections and expected "
            "minutes exist; low ownership alone is not evidence."
        ),
        fallback="None. Ownership on its own does not identify a differential.",
    ),
    "captaincy": FeatureRequirement(
        required_inputs=("projections_available", "next_gameweek"),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"projections_available": 15, "next_gameweek": 1},
        activates_when=(
            "Captaincy activates once expected minutes exist and a next "
            "gameweek is scheduled. Doubling a projection that has no minutes "
            "estimate behind it doubles the guess, not the information."
        ),
        fallback=(
            "None. Ranking by last season's points would be a recommendation "
            "about a season that has ended."
        ),
    ),
}


def readiness_for(
    feature: str,
    inputs: dict[str, Any],
    state: str,
    age_hours: float | None = None,
    last_success: datetime | None = None,
) -> dict[str, Any]:
    """Decide whether a feature may show a result, and say why if not."""
    requirement = FEATURES.get(feature)
    if requirement is None:
        return {
            "state": Readiness.ERROR,
            "missing": [],
            "stale": [],
            "invalid": [],
            "fallback": None,
            "last_success": last_success,
            "activates_when": "",
            "explanation": (
                f"'{feature}' is not a registered feature, so its readiness "
                "cannot be determined."
            ),
        }

    missing: list[str] = []
    invalid: list[str] = []
    for name in requirement.required_inputs:
        if name not in inputs or inputs[name] is None:
            missing.append(f"{name.replace('_', ' ')} is not available")
            continue
        needed = requirement.minimum_sample.get(name)
        value = inputs[name]
        if not isinstance(value, (int, float)):
            invalid.append(f"{name.replace('_', ' ')} is not a number")
        elif needed is not None and value < needed:
            missing.append(
                f"{name.replace('_', ' ')} is {value:g}, and {needed} is required"
            )

    if invalid:
        return {
            "state": Readiness.ERROR,
            "missing": missing,
            "stale": [],
            "invalid": invalid,
            "fallback": requirement.fallback,
            "last_success": last_success,
            "activates_when": requirement.activates_when,
            "explanation": "; ".join(invalid),
        }

    if state not in requirement.supported_states:
        return {
            "state": Readiness.NOT_READY,
            "missing": missing
            or [f"the season is {STATE_LABELS.get(state, state).lower()}"],
            "stale": [],
            "invalid": [],
            "fallback": requirement.fallback,
            "last_success": last_success,
            "activates_when": requirement.activates_when,
            "explanation": (
                f"This feature does not apply while the season is "
                f"{STATE_LABELS.get(state, state).lower()}. "
                f"{requirement.activates_when}"
            ),
        }

    if missing:
        return {
            "state": Readiness.NOT_READY,
            "missing": missing,
            "stale": [],
            "invalid": [],
            "fallback": requirement.fallback,
            "last_success": last_success,
            "activates_when": requirement.activates_when,
            "explanation": (
                "Not shown because " + "; ".join(missing) + ". "
                + requirement.activates_when
            ),
        }

    if age_hours is not None and age_hours > requirement.freshness_hours:
        return {
            "state": Readiness.STALE,
            "missing": [],
            "stale": [
                f"the last refresh was {age_hours:.0f} hours ago, beyond the "
                f"{requirement.freshness_hours}-hour freshness window"
            ],
            "invalid": [],
            "fallback": requirement.fallback,
            "last_success": last_success,
            "activates_when": requirement.activates_when,
            "explanation": (
                "Shown, but the underlying data is older than this feature's "
                "freshness window. Run a refresh."
            ),
        }

    return {
        "state": Readiness.READY,
        "missing": [],
        "stale": [],
        "invalid": [],
        "fallback": None,
        "last_success": last_success,
        "activates_when": requirement.activates_when,
        "explanation": "All required inputs are present and current.",
    }
