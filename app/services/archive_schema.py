"""Column mapping for the FPL community archive, which has four schema eras.

The archive spans ten seasons and its CSV layout changed three times. Rather than
assume a shape, each row is normalised against the columns actually present, and
anything a season never recorded stays ``None``. A ``None`` here means "this
season did not record it", which is not the same as zero -- the distinction the
rest of this application is built on.

Verified against the live repository on 2026-08-03:

    legacy        2016-17..2018-19   56 cols, detail stats, no starts/xG
    minimal       2019-20..2020-21   33 cols, no starts/xG
    transitional  2021-22            36 cols, adds position/team/xP
    modern        2022-23..2025-26   49 cols, adds starts and expected goals
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

# Present in every era. This is the guaranteed feature surface.
CORE_COLUMNS = frozenset({
    "assists", "bonus", "bps", "clean_sheets", "creativity", "element",
    "fixture", "goals_conceded", "goals_scored", "ict_index", "influence",
    "kickoff_time", "minutes", "opponent_team", "own_goals",
    "penalties_missed", "penalties_saved", "red_cards", "round", "saves",
    "selected", "threat", "total_points", "transfers_balance", "transfers_in",
    "transfers_out", "value", "was_home", "yellow_cards", "GW",
})

_LEGACY_MARKERS = frozenset({
    "attempted_passes", "ea_index", "big_chances_created", "dribbles",
})
_SEASON_PATTERN = re.compile(r"^(\d{4})-(\d{2})$")

# A start was not recorded before 2022-23. Sixty minutes is the conventional
# proxy, and it is wrong in two directions: a 45-minute start looks like a
# substitute appearance, and a 60-minute substitute appearance looks like a
# start. Every derived value is flagged so evaluation can measure the cost.
DERIVED_START_MINUTES = 60


def season_label(directory: str) -> str:
    """Convert an archive directory name (``2024-25``) to a season label.

    Raises rather than guessing: a mislabelled season silently mixes one
    season's observations into another.
    """
    match = _SEASON_PATTERN.match(directory.strip())
    if not match:
        raise ValueError(
            f"Archive directory {directory!r} is not in YYYY-YY form; "
            "a season must never be guessed."
        )
    return f"{match.group(1)}/{match.group(2)}"


def detect_era(columns: set[str]) -> str:
    """Classify which archive layout a set of column names belongs to."""
    if columns & _LEGACY_MARKERS:
        return "legacy"
    if "starts" in columns:
        return "modern"
    if "position" in columns or "xP" in columns:
        return "transitional"
    return "minimal"


def _int(value: Any, default: int | None = None) -> int | None:
    try:
        text = str(value).strip()
        return int(float(text)) if text else default
    except (TypeError, ValueError):
        return default


def _float(value: Any, default: float | None = None) -> float | None:
    try:
        text = str(value).strip()
        return float(text) if text else default
    except (TypeError, ValueError):
        return default


def _bool(value: Any) -> bool:
    return str(value).strip().casefold() in {"true", "1", "yes"}


def _time(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


@dataclass(frozen=True)
class ArchiveRow:
    """One player-gameweek observation, normalised across schema eras."""

    season: str
    player_element: int
    player_name: str
    gameweek: int
    minutes: int
    total_points: int
    started: bool
    started_is_derived: bool
    starts: int | None
    position: str | None
    team_name: str | None
    opponent_team_id: int | None
    fixture_id: int | None
    kickoff_time: datetime | None
    is_home: bool
    goals: int
    assists: int
    clean_sheets: int
    goals_conceded: int | None
    saves: int | None
    bonus: int | None
    bps: int | None
    yellow_cards: int | None
    red_cards: int | None
    own_goals: int | None
    penalties_missed: int | None
    penalties_saved: int | None
    expected_goals: float | None
    expected_assists: float | None
    expected_goal_involvements: float | None
    expected_goals_conceded: float | None
    influence: float | None
    creativity: float | None
    threat: float | None
    ict_index: float | None
    price: float | None
    selected: int | None
    transfers_in: int | None
    transfers_out: int | None
    transfers_balance: int | None


def normalise_row(raw: dict[str, str], season: str) -> ArchiveRow | None:
    """Convert one archive CSV row.

    Returns ``None`` when the row cannot be identified, which is preferable to
    storing an observation that cannot be attributed to a player and gameweek.
    """
    element = _int(raw.get("element"))
    gameweek = _int(raw.get("GW")) or _int(raw.get("round"))
    if element is None or gameweek is None or gameweek <= 0:
        return None

    minutes = _int(raw.get("minutes"), 0) or 0
    recorded_starts = _int(raw.get("starts"))
    if recorded_starts is None:
        started = minutes >= DERIVED_START_MINUTES
        started_is_derived = True
    else:
        started = recorded_starts > 0
        started_is_derived = False

    price_units = _int(raw.get("value"))
    position = str(raw.get("position") or "").strip() or None
    team_name = str(raw.get("team") or "").strip() or None

    return ArchiveRow(
        season=season,
        player_element=element,
        player_name=str(raw.get("name") or "").strip(),
        gameweek=gameweek,
        minutes=minutes,
        total_points=_int(raw.get("total_points"), 0) or 0,
        started=started,
        started_is_derived=started_is_derived,
        starts=recorded_starts,
        position=position,
        team_name=team_name,
        opponent_team_id=_int(raw.get("opponent_team")),
        fixture_id=_int(raw.get("fixture")),
        kickoff_time=_time(raw.get("kickoff_time")),
        is_home=_bool(raw.get("was_home")),
        goals=_int(raw.get("goals_scored"), 0) or 0,
        assists=_int(raw.get("assists"), 0) or 0,
        clean_sheets=_int(raw.get("clean_sheets"), 0) or 0,
        goals_conceded=_int(raw.get("goals_conceded")),
        saves=_int(raw.get("saves")),
        bonus=_int(raw.get("bonus")),
        bps=_int(raw.get("bps")),
        yellow_cards=_int(raw.get("yellow_cards")),
        red_cards=_int(raw.get("red_cards")),
        own_goals=_int(raw.get("own_goals")),
        penalties_missed=_int(raw.get("penalties_missed")),
        penalties_saved=_int(raw.get("penalties_saved")),
        expected_goals=_float(raw.get("expected_goals")),
        expected_assists=_float(raw.get("expected_assists")),
        expected_goal_involvements=_float(raw.get("expected_goal_involvements")),
        expected_goals_conceded=_float(raw.get("expected_goals_conceded")),
        influence=_float(raw.get("influence")),
        creativity=_float(raw.get("creativity")),
        threat=_float(raw.get("threat")),
        ict_index=_float(raw.get("ict_index")),
        price=None if price_units is None else round(price_units / 10.0, 1),
        selected=_int(raw.get("selected")),
        transfers_in=_int(raw.get("transfers_in")),
        transfers_out=_int(raw.get("transfers_out")),
        transfers_balance=_int(raw.get("transfers_balance")),
    )
