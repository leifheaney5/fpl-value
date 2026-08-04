from __future__ import annotations

import math
from collections import defaultdict
from typing import Any


def safe_float(value: Any, default: float = 0.0) -> float:
    try:
        if value in (None, ""):
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def safe_int(value: Any, default: int = 0) -> int:
    try:
        if value in (None, ""):
            return default
        return int(float(value))
    except (TypeError, ValueError):
        return default


def clamp(value: float, lower: float, upper: float) -> float:
    return max(lower, min(upper, value))


def cumulative_tier(rank: int | None, count: int) -> str:
    if rank is None or count <= 0:
        return "Not Ranked"
    if rank <= max(1, math.ceil(count * 0.10)):
        return "Top 10%"
    if rank <= max(1, math.ceil(count * 0.25)):
        return "Top 25%"
    if rank <= max(1, math.ceil(count * 0.50)):
        return "Top 50%"
    return "Bottom 50%"


def percentile(rank: int | None, count: int) -> float | None:
    if rank is None or count <= 0:
        return None
    return round(100.0 * (count - rank + 1) / count, 1)


def availability_factor(player: dict[str, Any]) -> float:
    status = str(player.get("status") or "a").lower()
    chance = player.get("chance_of_playing_next_round")
    if chance is not None:
        return clamp(safe_float(chance) / 100.0, 0.0, 1.0)
    return {
        "a": 1.0,
        "d": 0.75,
        "i": 0.15,
        "s": 0.0,
        "u": 0.0,
        "n": 0.0,
    }.get(status, 0.8)


def reliability_factor(
    minutes: int,
    starts: int,
    team_matches: int,
    sample_minutes: int = 900,
) -> float | None:
    """Return None when the team has played no matches.

    Returning 0.0 here would claim the player is maximally unreliable, which is
    a measurement the data does not support before a ball is kicked.
    """
    if team_matches <= 0:
        return None
    minute_share = clamp(minutes / (team_matches * 90.0), 0.0, 1.0)
    start_share = clamp(starts / team_matches, 0.0, 1.0)
    sample_factor = clamp(minutes / max(1, sample_minutes), 0.0, 1.0)
    return round(
        sample_factor * (0.60 * minute_share + 0.40 * start_share),
        4,
    )


def rotation_risk(
    minutes: int,
    starts: int,
    team_matches: int,
    previous: dict[str, Any] | None,
    season_start_weight: float = 0.35,
    season_minutes_weight: float = 0.25,
    recent_start_weight: float = 0.25,
    recent_minutes_weight: float = 0.15,
) -> tuple[float | None, str, str]:
    if team_matches <= 0:
        return None, "Insufficient Data", "Low"

    season_start_share = clamp(starts / team_matches, 0.0, 1.0)
    season_minute_share = clamp(
        minutes / (team_matches * 90.0), 0.0, 1.0
    )

    weighted = [
        (1.0 - season_start_share, season_start_weight),
        (1.0 - season_minute_share, season_minutes_weight),
    ]

    recent_matches = 0
    if previous:
        old_matches = safe_int(previous.get("team_matches"), -1)
        old_starts = safe_int(previous.get("starts"), -1)
        old_minutes = safe_int(previous.get("minutes"), -1)
        if (
            old_matches >= 0
            and old_starts >= 0
            and old_minutes >= 0
            and team_matches > old_matches
            and starts >= old_starts
            and minutes >= old_minutes
        ):
            recent_matches = team_matches - old_matches
            recent_starts = starts - old_starts
            recent_minutes = minutes - old_minutes
            recent_start_share = clamp(
                recent_starts / recent_matches, 0.0, 1.0
            )
            recent_minute_share = clamp(
                recent_minutes / (recent_matches * 90.0), 0.0, 1.0
            )
            weighted.extend(
                [
                    (1.0 - recent_start_share, recent_start_weight),
                    (1.0 - recent_minute_share, recent_minutes_weight),
                ]
            )

    denominator = sum(weight for _, weight in weighted)
    if denominator <= 0:
        return None, "Insufficient Data", "Low"
    score = round(
        100.0
        * sum(component * weight for component, weight in weighted)
        / denominator,
        1,
    )

    if score <= 20:
        tier = "Low"
    elif score <= 40:
        tier = "Moderate"
    elif score <= 65:
        tier = "High"
    else:
        tier = "Very High"

    if team_matches >= 5 and recent_matches >= 2:
        confidence = "High"
    elif team_matches >= 3:
        confidence = "Medium"
    else:
        confidence = "Low"

    return score, tier, confidence


def expected_minutes(
    minutes: int,
    starts: int,
    team_matches: int,
    availability: float | None,
) -> float | None:
    """Observed expected minutes for the current season.

    Returns None before the season starts. Estimating preseason minutes needs a
    different model built on previous-season inputs; pretending the answer is
    zero would rank every player as a non-starter.
    """
    if team_matches <= 0 or availability is None:
        return None
    average_minutes = clamp(minutes / team_matches, 0.0, 90.0)
    start_share = clamp(starts / team_matches, 0.0, 1.0)
    blended = 0.65 * average_minutes + 0.35 * (90.0 * start_share)
    return round(clamp(blended * availability, 0.0, 90.0), 1)


def project_next_fixtures(
    *,
    form: float,
    points_per_game: float,
    points_per_90: float | None,
    expected_minutes_value: float | None,
    availability: float | None,
    fixtures: list[dict[str, Any]],
    form_weight: float = 0.40,
    ppg_weight: float = 0.35,
    p90_weight: float = 0.25,
    difficulty_weight: float = 0.08,
    home_advantage_factor: float = 0.03,
) -> float | None:
    """Return None when the projection has no basis.

    A projection needs both a fixture list and an expected-minutes estimate. With
    either missing there is nothing to project, and 0.0 would read as a confident
    forecast of a blank.
    """
    if not fixtures or expected_minutes_value is None or availability is None:
        return None

    baseline = (
        form_weight * max(0.0, form)
        + ppg_weight * max(0.0, points_per_game)
        + p90_weight * max(0.0, points_per_90 or 0.0)
    )
    minute_factor = expected_minutes_value / 90.0
    total = 0.0

    for fixture in fixtures:
        difficulty = safe_float(fixture.get("difficulty"), 3.0)
        difficulty_factor = clamp(1.0 + (3.0 - difficulty) * difficulty_weight, 0.75, 1.25)
        home_factor = 1.0 + home_advantage_factor if fixture.get("is_home") else 1.0
        total += (
            baseline
            * minute_factor
            * availability
            * difficulty_factor
            * home_factor
        )

    return round(total, 2)


def assign_global_ranks(
    rows: list[dict[str, Any]],
    metric: str,
    rank_key: str,
    percentile_key: str,
    tier_key: str,
) -> dict[str, int]:
    """Rank rows on a metric and report why the rest were left out.

    Returns a count of exclusions by reason so the interface can explain the gap
    between "players tracked" and "players ranked" instead of showing two
    numbers that do not add up.
    """
    exclusions: dict[str, int] = {}
    ranked: list[dict[str, Any]] = []
    for row in rows:
        raw = row.get(metric)
        if raw is None:
            reason = "Metric not available yet"
        elif safe_float(raw) <= 0:
            reason = "No positive score to rank"
        else:
            ranked.append(row)
            continue
        exclusions[reason] = exclusions.get(reason, 0) + 1

    ranked.sort(
        key=lambda row: (
            -safe_float(row.get(metric)),
            -safe_int(row.get("total_points")),
            str(row.get("player_name", "")).casefold(),
        )
    )
    count = len(ranked)
    for rank, row in enumerate(ranked, start=1):
        row[rank_key] = rank
        row[percentile_key] = percentile(rank, count)
        row[tier_key] = cumulative_tier(rank, count)
    return exclusions


def assign_position_ranks(
    rows: list[dict[str, Any]],
    metric: str,
    rank_key: str,
    percentile_key: str,
    tier_key: str,
) -> None:
    groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        raw = row.get(metric)
        if raw is not None and safe_float(raw) > 0:
            groups[str(row.get("position_short"))].append(row)

    for group in groups.values():
        group.sort(
            key=lambda row: (
                -safe_float(row.get(metric)),
                str(row.get("player_name", "")).casefold(),
            )
        )
        count = len(group)
        for rank, row in enumerate(group, start=1):
            row[rank_key] = rank
            row[percentile_key] = percentile(rank, count)
            row[tier_key] = cumulative_tier(rank, count)


# A rate needs a sample behind it. These thresholds are in minutes because
# minutes are what the FPL API reports for every player in every era, and
# because a substitute who plays 20 minutes twenty times is a better-evidenced
# rate than a starter who played twice.
#
# 900 minutes is ten full matches -- roughly a quarter of a season, and the
# point at which a per-90 rate stops swinging wildly on one return. 2000 is
# over half a season of regular starts.
SAMPLE_HIGH_MINUTES = 2000
SAMPLE_MEDIUM_MINUTES = 900


def sample_confidence(snapshot: Any) -> tuple[str, str]:
    """How much weight a rate derived from this player's minutes can carry.

    Returns a level and a human-readable reason. ``none`` is distinct from
    ``low``: no minutes is an absence of evidence, not thin evidence, and the
    interface should not invite comparison between them.
    """
    minutes = int(getattr(snapshot, "minutes", 0) or 0)
    starts = int(getattr(snapshot, "starts", 0) or 0)

    if minutes <= 0:
        return "none", "No minutes played, so no rate can be formed"

    detail = f"{minutes} minutes"
    if starts:
        detail += f" across {starts} start{'s' if starts != 1 else ''}"

    if minutes >= SAMPLE_HIGH_MINUTES:
        return "high", f"{detail} — a well-evidenced rate"
    if minutes >= SAMPLE_MEDIUM_MINUTES:
        return "medium", f"{detail} — a moderate sample"
    return "low", f"{detail} — too small a sample to rely on"
