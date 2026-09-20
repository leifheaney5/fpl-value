"""Past-season records for current players.

``gameweek_history`` holds a row per player per fixture across every stored
season. Grouping it per request is too slow, and completed seasons never change,
so the grouping is done once into ``player_season_aggregates`` and the pages
read that.

Every derived measure here follows the rule used elsewhere in the application:
a sample too thin to support a number yields null, never zero and never a guess.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timezone
from math import sqrt
from statistics import mean, stdev
from typing import Any, Iterable

from sqlalchemy import and_, case, delete, func, select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory, PlayerSeasonAggregate

# An appearance returning two points or fewer is what FPL managers call a blank:
# the player turned up and nothing else happened. Ten or more is a haul.
BLANK_POINTS = 2
HAUL_POINTS = 10

# Matches the floor the application already applies to current-season P/90.
P90_MIN_MINUTES = 270
# Pooled per-appearance measures need enough appearances to mean something, and
# availability needs at least half a season of fixtures.
MIN_POOLED_APPEARANCES = 10
MIN_POOLED_FIXTURES = 19

STEADY_MAX_CV = 0.15
VARIABLE_MAX_CV = 0.30
DURABILITY_HIGH = 70.0
DURABILITY_MEDIUM = 40.0


def _season_order(season: str) -> int:
    return int(season[:4])


def rebuild_season_aggregates(
    db: Session,
    seasons: Iterable[str] | None = None,
) -> dict[str, int]:
    """Recompute aggregates from gameweek history. Safe to run repeatedly."""
    targets = list(seasons) if seasons is not None else None
    history = GameweekHistory
    played = history.minutes > 0

    grouped = select(
        history.player_code,
        history.season,
        func.count().label("fixtures"),
        func.sum(case((played, 1), else_=0)).label("appearances"),
        func.sum(history.minutes).label("minutes"),
        func.sum(case((history.started, 1), else_=0)).label("starts"),
        func.max(case((history.started_is_derived, 1), else_=0)).label(
            "starts_derived"
        ),
        func.sum(history.points).label("points"),
        func.sum(history.goals).label("goals"),
        func.sum(history.assists).label("assists"),
        func.sum(history.clean_sheets).label("clean_sheets"),
        func.sum(history.bonus).label("bonus"),
        func.sum(case((played, history.points), else_=0)).label("appearance_points"),
        func.sum(case((played, history.points * history.points), else_=0)).label(
            "appearance_points_sq"
        ),
        func.sum(
            case((and_(played, history.points <= BLANK_POINTS), 1), else_=0)
        ).label("blanks"),
        func.sum(
            case((and_(played, history.points >= HAUL_POINTS), 1), else_=0)
        ).label("hauls"),
        func.min(case((history.price > 0, history.price))).label("price_min"),
        func.max(case((history.price > 0, history.price))).label("price_max"),
    ).group_by(history.player_code, history.season)

    # Position and club as of the player's last fixture of the season, so a
    # mid-season transfer is described by where he finished.
    last_gameweek = (
        select(
            history.player_code,
            history.season,
            func.max(history.gameweek).label("gameweek"),
        )
        .group_by(history.player_code, history.season)
        .subquery()
    )
    latest = select(
        history.player_code, history.season, history.position, history.team_name
    ).join(
        last_gameweek,
        and_(
            history.player_code == last_gameweek.c.player_code,
            history.season == last_gameweek.c.season,
            history.gameweek == last_gameweek.c.gameweek,
        ),
    )

    removal = delete(PlayerSeasonAggregate)
    if targets is not None:
        grouped = grouped.where(history.season.in_(targets))
        latest = latest.where(history.season.in_(targets))
        removal = removal.where(PlayerSeasonAggregate.season.in_(targets))

    described = {
        (code, season): (position, team_name)
        for code, season, position, team_name in db.execute(latest)
    }
    now = datetime.now(timezone.utc)
    rows = []
    for record in db.execute(grouped):
        position, team_name = described.get(
            (record.player_code, record.season), (None, None)
        )
        rows.append(
            {
                **record._mapping,
                "starts_derived": bool(record.starts_derived),
                "position": position,
                "team_name": team_name,
                "computed_at": now,
            }
        )

    db.execute(removal)
    if rows:
        db.execute(PlayerSeasonAggregate.__table__.insert(), rows)
    db.commit()
    return {"seasons": len({row["season"] for row in rows}), "rows": len(rows)}


def _rate(numerator: int, denominator: int) -> float:
    return 100.0 * numerator / denominator


def _deviation(total: int, squares: int, count: int) -> float:
    average = total / count
    return sqrt(max(squares / count - average * average, 0.0))


def _season_line(aggregate: Any) -> dict[str, Any]:
    appearances = aggregate.appearances
    fixtures = aggregate.fixtures
    return {
        "season": aggregate.season,
        "team_name": aggregate.team_name,
        "position": aggregate.position,
        "fixtures": fixtures,
        "appearances": appearances,
        "minutes": aggregate.minutes,
        "starts": aggregate.starts,
        "starts_derived": aggregate.starts_derived,
        "points": aggregate.points,
        "goals": aggregate.goals,
        "assists": aggregate.assists,
        "clean_sheets": aggregate.clean_sheets,
        "bonus": aggregate.bonus,
        "ppg": aggregate.points / appearances if appearances else None,
        "p90": (
            aggregate.points * 90 / aggregate.minutes
            if aggregate.minutes >= P90_MIN_MINUTES
            else None
        ),
        "gw_sd": (
            _deviation(
                aggregate.appearance_points,
                aggregate.appearance_points_sq,
                appearances,
            )
            if appearances >= MIN_POOLED_APPEARANCES
            else None
        ),
        "blank_rate": _rate(aggregate.blanks, appearances) if appearances else None,
        "haul_rate": _rate(aggregate.hauls, appearances) if appearances else None,
        "start_rate": _rate(aggregate.starts, fixtures) if fixtures else None,
        "minutes_share": (
            _rate(aggregate.minutes, 90 * fixtures) if fixtures else None
        ),
        "price_min": aggregate.price_min,
        "price_max": aggregate.price_max,
    }


def summarise_career(
    aggregates: Iterable[Any],
    sample_minutes: int = 900,
) -> dict[str, Any]:
    """Describe a player's past seasons, newest first.

    Season-to-season measures use only seasons with at least ``sample_minutes``
    played: a per-90 rate from a handful of substitute appearances would
    dominate the spread without describing the player.

    Pooled measures count from the first such season onward. Seasons spent
    registered as an academy player are not evidence of fragility, and pooling
    them rated established regulars as unreliable.
    """
    ordered = sorted(
        aggregates, key=lambda item: _season_order(item.season), reverse=True
    )
    seasons = [_season_line(item) for item in ordered]

    qualifying = [
        line["p90"]
        for line, item in zip(seasons, ordered)
        if item.minutes >= sample_minutes and line["p90"] is not None
    ]
    mean_p90 = mean(qualifying) if qualifying else None
    spread = stdev(qualifying) if len(qualifying) >= 2 else None
    consistency = relative = None
    if spread is not None and mean_p90:
        relative = spread / mean_p90
        consistency = (
            "Steady" if relative <= STEADY_MAX_CV
            else "Variable" if relative <= VARIABLE_MAX_CV
            else "Volatile"
        )

    established = [
        index for index, item in enumerate(ordered) if item.minutes >= sample_minutes
    ]
    pooled = ordered[: established[-1] + 1] if established else ordered
    appearances = sum(item.appearances for item in pooled)
    fixtures = sum(item.fixtures for item in pooled)
    gw_sd = blank_rate = haul_rate = None
    if appearances >= MIN_POOLED_APPEARANCES:
        gw_sd = _deviation(
            sum(item.appearance_points for item in pooled),
            sum(item.appearance_points_sq for item in pooled),
            appearances,
        )
        blank_rate = _rate(sum(item.blanks for item in pooled), appearances)
        haul_rate = _rate(sum(item.hauls for item in pooled), appearances)

    minutes_share = start_rate = durability = None
    if fixtures >= MIN_POOLED_FIXTURES:
        minutes_share = _rate(sum(item.minutes for item in pooled), 90 * fixtures)
        start_rate = _rate(sum(item.starts for item in pooled), fixtures)
        durability = (
            "High" if minutes_share >= DURABILITY_HIGH
            else "Medium" if minutes_share >= DURABILITY_MEDIUM
            else "Low"
        )

    return {
        "seasons": seasons,
        "season_count": len(seasons),
        "qualifying_seasons": len(qualifying),
        "points_by_season": {line["season"]: line["points"] for line in seasons},
        "minutes_by_season": {line["season"]: line["minutes"] for line in seasons},
        "mean_p90": mean_p90,
        "p90_spread": spread,
        "relative_spread": relative,
        "consistency": consistency,
        "gw_sd": gw_sd,
        "blank_rate": blank_rate,
        "haul_rate": haul_rate,
        "minutes_share": minutes_share,
        "start_rate": start_rate,
        "starts_estimated": any(item.starts_derived for item in pooled),
        "pooled_since": pooled[-1].season if pooled else None,
        "durability": durability,
    }


def career_summaries(
    db: Session,
    player_codes: Iterable[int | None],
    *,
    exclude_season: str | None = None,
    sample_minutes: int = 900,
) -> dict[int, dict[str, Any]]:
    """Summaries keyed by player code, for the codes that have any past record."""
    codes = {code for code in player_codes if code is not None}
    if not codes:
        return {}
    query = select(PlayerSeasonAggregate).where(
        PlayerSeasonAggregate.player_code.in_(codes)
    )
    if exclude_season:
        query = query.where(PlayerSeasonAggregate.season != exclude_season)
    grouped: dict[int, list[PlayerSeasonAggregate]] = defaultdict(list)
    for aggregate in db.scalars(query):
        grouped[aggregate.player_code].append(aggregate)
    return {
        code: summarise_career(items, sample_minutes)
        for code, items in grouped.items()
    }


def stored_seasons(db: Session, exclude_season: str | None = None) -> list[str]:
    """Seasons with aggregates, newest first."""
    query = select(PlayerSeasonAggregate.season).distinct()
    if exclude_season:
        query = query.where(PlayerSeasonAggregate.season != exclude_season)
    return sorted(db.scalars(query), key=_season_order, reverse=True)
