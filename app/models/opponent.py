"""Opponent strength derived from matches already played.

The FPL archive carries no fixture difficulty rating, so every model evaluated
against it has been blind to who the opponent is. This derives attack and
defence rates from results instead.

Two properties matter and both are tested:

1. **Point-in-time.** Only matches whose kickoff precedes the cutoff count.
   Using full-season totals would leak later results into earlier rows, and it
   would do so invisibly -- the model would simply appear excellent.
2. **One row per fixture.** Gameweek history is per player, so eleven rows share
   a single team match. Counting them individually would divide every rate by
   eleven.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable


def _as_utc(value: datetime | None) -> datetime | None:
    """SQLite returns naive datetimes; PostgreSQL returns aware ones."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


@dataclass(frozen=True)
class TeamStrength:
    """Goals scored and conceded per match, over matches already played."""

    matches: int
    attack: float
    defence: float


def build_team_strength(
    rows: Iterable[Any],
    as_of: datetime,
    season: str,
) -> dict[str, TeamStrength]:
    """Attack and defence rates per team, from matches strictly before as_of.

    A team with no completed matches is absent from the result rather than
    present with zeroes: absent means unknown, whereas zero would claim the team
    scores nothing.
    """
    cutoff = _as_utc(as_of)

    # Keyed by (team, kickoff, opponent) so eleven player rows for one fixture
    # collapse to a single match.
    fixtures: dict[tuple[str, datetime, Any], tuple[int, int]] = {}

    for row in rows:
        if getattr(row, "season", None) != season:
            continue
        team = getattr(row, "team_name", None)
        kickoff = _as_utc(getattr(row, "kickoff_time", None))
        if not team or kickoff is None or kickoff >= cutoff:
            continue
        key = (team, kickoff, getattr(row, "opponent_team_id", None))
        if key in fixtures:
            continue
        fixtures[key] = (
            int(getattr(row, "goals", 0) or 0),
            int(getattr(row, "goals_conceded", 0) or 0),
        )

    totals: dict[str, list[int]] = {}
    for (team, _, _), (scored, conceded) in fixtures.items():
        bucket = totals.setdefault(team, [0, 0, 0])
        bucket[0] += 1
        bucket[1] += scored
        bucket[2] += conceded

    return {
        team: TeamStrength(
            matches=matches,
            attack=scored / matches,
            defence=conceded / matches,
        )
        for team, (matches, scored, conceded) in totals.items()
        if matches > 0
    }


def build_fixture_opponents(rows: Iterable[Any]) -> dict[tuple[str, Any, str], str]:
    """Map (season, fixture, team) to the opposing team's name.

    History stores ``opponent_team_id`` but no team id, so an id cannot be
    joined to a name directly. Both sides of a fixture share a ``fixture_id``,
    which resolves it: whichever other team appears on the fixture is the
    opponent.

    Team names are absent from the archive before 2021-22, so this returns
    nothing for those seasons and the dependent features stay masked, which is
    the honest outcome rather than a guess.
    """
    teams_by_fixture: dict[tuple[str, Any], set[str]] = {}
    for row in rows:
        season = getattr(row, "season", None)
        fixture = getattr(row, "fixture_id", None)
        team = getattr(row, "team_name", None)
        if not season or fixture is None or not team:
            continue
        teams_by_fixture.setdefault((season, fixture), set()).add(team)

    opponents: dict[tuple[str, Any, str], str] = {}
    for (season, fixture), teams in teams_by_fixture.items():
        if len(teams) != 2:
            # A fixture should have exactly two sides. Anything else is a data
            # problem and is skipped rather than guessed at.
            continue
        first, second = sorted(teams)
        opponents[(season, fixture, first)] = second
        opponents[(season, fixture, second)] = first
    return opponents


def build_strength_by_gameweek(
    rows: Iterable[Any], season: str
) -> dict[int, dict[str, TeamStrength]]:
    """Team strength as it stood at the start of each gameweek.

    Gameweek granularity rather than per-fixture: it is far cheaper and, because
    a gameweek's matches are played together, it does not admit information from
    a match a player's own fixture has not yet seen.

    Strength for gameweek G uses only gameweeks strictly before G.
    """
    materialised = [row for row in rows if getattr(row, "season", None) == season]
    gameweeks = sorted(
        {
            int(row.gameweek)
            for row in materialised
            if getattr(row, "gameweek", None) is not None
        }
    )

    # Collapse player rows into team fixtures. Goals and goals conceded need
    # opposite treatment, which is easy to get wrong:
    #
    #   goals            per player, so the team total is the SUM across the
    #                    squad. Taking one player's row gives roughly zero,
    #                    because most players do not score.
    #   goals_conceded   per player, counting only while they were on the pitch,
    #                    so the team total is the MAX across the squad -- whoever
    #                    played the full match saw them all.
    per_fixture: dict[tuple[int, str, Any], list[int]] = {}
    for row in materialised:
        team = getattr(row, "team_name", None)
        gameweek = getattr(row, "gameweek", None)
        if not team or gameweek is None:
            continue
        key = (int(gameweek), team, getattr(row, "fixture_id", None))
        bucket = per_fixture.setdefault(key, [0, 0])
        bucket[0] += int(getattr(row, "goals", 0) or 0)
        bucket[1] = max(bucket[1], int(getattr(row, "goals_conceded", 0) or 0))

    per_gameweek: dict[int, dict[str, tuple[int, int]]] = {}
    for (gameweek, team, _), (scored, conceded) in per_fixture.items():
        bucket = per_gameweek.setdefault(gameweek, {})
        previous_scored, previous_conceded = bucket.get(team, (0, 0))
        bucket[team] = (previous_scored + scored, previous_conceded + conceded)

    result: dict[int, dict[str, TeamStrength]] = {}
    running: dict[str, list[int]] = {}
    for gameweek in gameweeks:
        # Snapshot before folding this gameweek in, so gameweek G never sees
        # its own results.
        result[gameweek] = {
            team: TeamStrength(
                matches=matches, attack=scored / matches, defence=conceded / matches
            )
            for team, (matches, scored, conceded) in running.items()
            if matches > 0
        }
        for team, (scored, conceded) in per_gameweek.get(gameweek, {}).items():
            bucket = running.setdefault(team, [0, 0, 0])
            bucket[0] += 1
            bucket[1] += scored
            bucket[2] += conceded

    return result
