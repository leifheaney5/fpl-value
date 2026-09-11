from __future__ import annotations

from datetime import datetime, timezone
from math import isfinite
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Fixture, Team


BADGE_URL = "https://resources.premierleague.com/premierleague/badges/t{team_id}.png"


def _number(value: Any) -> int | float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if isfinite(value) else None


def _fixture_key(fixture: Fixture) -> tuple[bool, int, bool, datetime, int]:
    return (
        fixture.event is None,
        fixture.event or 0,
        fixture.kickoff_time is None,
        fixture.kickoff_time or datetime.min.replace(tzinfo=timezone.utc),
        fixture.id,
    )


def _badge_url(team_id: int) -> str:
    return BADGE_URL.format(team_id=team_id)


def fixture_analysis(db: Session, *, limit: int = 10) -> list[dict[str, Any]]:
    """Project the next stored fixtures for every team without remote requests."""
    teams = db.scalars(select(Team)).all()
    fixtures = db.scalars(select(Fixture).where(Fixture.finished.is_(False))).all()
    fixtures_by_team: dict[int, list[Fixture]] = {team.id: [] for team in teams}
    teams_by_id = {team.id: team for team in teams}

    for fixture in fixtures:
        if fixture.team_h in fixtures_by_team:
            fixtures_by_team[fixture.team_h].append(fixture)
        if fixture.team_a in fixtures_by_team:
            fixtures_by_team[fixture.team_a].append(fixture)

    rows = []
    for team in teams:
        selected = sorted(fixtures_by_team[team.id], key=_fixture_key)[:limit]
        records = []
        for fixture in selected:
            is_home = fixture.team_h == team.id
            opponent_id = fixture.team_a if is_home else fixture.team_h
            opponent = teams_by_id.get(opponent_id)
            difficulty = _number(
                fixture.team_h_difficulty if is_home else fixture.team_a_difficulty
            )
            records.append(
                {
                    "event": fixture.event,
                    "kickoff_time": fixture.kickoff_time,
                    "opponent": opponent.name if opponent else str(opponent_id),
                    "opponent_id": opponent_id,
                    "is_home": is_home,
                    "difficulty": difficulty,
                    "badge_url": _badge_url(opponent_id),
                }
            )

        difficulties = [record["difficulty"] for record in records]
        numeric = [value for value in difficulties if value is not None]
        complete = bool(records) and len(numeric) == len(records)
        rows.append(
            {
                "team": team,
                "fixtures": records,
                "available": len(records),
                "complete": complete,
                "average_difficulty": (
                    sum(numeric) / len(numeric) if numeric else None
                ),
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            row["average_difficulty"] is None,
            row["average_difficulty"] or 0,
            row["team"].name,
        ),
    )


def team_performance(db: Session, *, limit: int = 10) -> list[dict[str, Any]]:
    """Calculate current form from stored, completed fixtures with valid scores."""
    teams = db.scalars(select(Team)).all()
    fixtures = db.scalars(select(Fixture).where(Fixture.finished.is_(True))).all()
    fixtures_by_team: dict[int, list[Fixture]] = {team.id: [] for team in teams}
    teams_by_id = {team.id: team for team in teams}

    for fixture in fixtures:
        raw = fixture.raw or {}
        if _number(raw.get("team_h_score")) is None or _number(raw.get("team_a_score")) is None:
            continue
        if fixture.team_h in fixtures_by_team:
            fixtures_by_team[fixture.team_h].append(fixture)
        if fixture.team_a in fixtures_by_team:
            fixtures_by_team[fixture.team_a].append(fixture)

    rows = []
    for team in teams:
        latest = sorted(fixtures_by_team[team.id], key=_fixture_key, reverse=True)[:limit]
        results = []
        for fixture in reversed(latest):
            raw = fixture.raw or {}
            home_score = _number(raw.get("team_h_score"))
            away_score = _number(raw.get("team_a_score"))
            assert home_score is not None and away_score is not None
            is_home = fixture.team_h == team.id
            goals_for, goals_against = (
                (home_score, away_score) if is_home else (away_score, home_score)
            )
            result = "W" if goals_for > goals_against else "D" if goals_for == goals_against else "L"
            opponent_id = fixture.team_a if is_home else fixture.team_h
            opponent = teams_by_id.get(opponent_id)
            results.append(
                {
                    "event": fixture.event,
                    "kickoff_time": fixture.kickoff_time,
                    "opponent": opponent.name if opponent else str(opponent_id),
                    "opponent_id": opponent_id,
                    "is_home": is_home,
                    "goals_for": goals_for,
                    "goals_against": goals_against,
                    "score": f"{goals_for}-{goals_against}",
                    "result": result,
                    "badge_url": _badge_url(opponent_id),
                }
            )

        wins = sum(result["result"] == "W" for result in results)
        draws = sum(result["result"] == "D" for result in results)
        losses = sum(result["result"] == "L" for result in results)
        points = wins * 3 + draws
        rows.append(
            {
                "team": team,
                "results": results,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "points": points,
                "points_per_game": points / len(results) if results else None,
                "goals_for": sum(result["goals_for"] for result in results),
                "goals_against": sum(result["goals_against"] for result in results),
            }
        )

    return sorted(
        rows,
        key=lambda row: (
            row["points_per_game"] is None,
            -(row["points_per_game"] or 0),
            -row["points"],
            row["team"].name,
        ),
    )
