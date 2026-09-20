from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import and_, func, or_, select, union_all
from sqlalchemy.orm import Session

from app.db.models import Fixture, Team


BADGE_URL = "https://resources.premierleague.com/premierleague/badges/t{team_id}.png"
MAX_FPL_SCORE = 2_147_483_647

# The FPL team id is not the same identifier used by the Premier League badge
# CDN. Keep the mapping at this presentation boundary and retain an id-based
# fallback for imported or otherwise unknown clubs.
CLUB_BADGE_CODES = {
    "arsenal": 3,
    "aston villa": 7,
    "bournemouth": 91,
    "brentford": 94,
    "brighton": 36,
    "brighton and hove albion": 36,
    "burnley": 90,
    "chelsea": 8,
    "crystal palace": 31,
    "everton": 11,
    "fulham": 54,
    "leeds": 2,
    "liverpool": 14,
    "man city": 43,
    "manchester city": 43,
    "man utd": 1,
    "manchester united": 1,
    "newcastle": 4,
    "newcastle united": 4,
    "nott'm forest": 17,
    "nottingham forest": 17,
    "sunderland": 56,
    "spurs": 6,
    "tottenham hotspur": 6,
    "west ham": 21,
    "west ham united": 21,
    "wolves": 39,
    "wolverhampton wanderers": 39,
}


def _fixture_key(fixture: Fixture) -> tuple[bool, int, bool, datetime, int]:
    return (
        fixture.event is None,
        fixture.event or 0,
        fixture.kickoff_time is None,
        fixture.kickoff_time or datetime.min.replace(tzinfo=timezone.utc),
        fixture.id,
    )


def _badge_url(
    team_id: int,
    team_name: str | None = None,
    team_code: int | None = None,
) -> str:
    badge_code = team_code or CLUB_BADGE_CODES.get(
        (team_name or "").strip().casefold(), team_id
    )
    return BADGE_URL.format(team_id=badge_code)


def _valid_difficulty(raw: dict[str, Any], key: str) -> int | None:
    value = raw.get(key)
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 1 <= value <= 5 else None


def _valid_score(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if 0 <= value <= MAX_FPL_SCORE else None


def _fixture_order(descending: bool = False):
    columns = (
        Fixture.event.is_(None),
        Fixture.event,
        Fixture.kickoff_time.is_(None),
        Fixture.kickoff_time,
        Fixture.id,
    )
    return tuple(column.desc() if descending else column.asc() for column in columns)


def _valid_score_predicate(db: Session):
    """Keep malformed scores out of the SQL window before they consume a slot."""
    dialect = db.get_bind().dialect.name
    if dialect == "sqlite":
        def sqlite_score(key: str):
            return and_(
                func.json_type(Fixture.raw, f"$.{key}") == "integer",
                Fixture.raw[key].as_integer() >= 0,
                Fixture.raw[key].as_integer() <= MAX_FPL_SCORE,
            )

        return and_(sqlite_score("team_h_score"), sqlite_score("team_a_score"))

    if dialect == "postgresql":
        def postgres_score(key: str):
            value = Fixture.raw[key].as_string()
            return and_(
                func.json_typeof(Fixture.raw[key]) == "number",
                value.op("~")(r"^[0-9]+$"),
                or_(
                    func.length(value) < 10,
                    and_(
                        func.length(value) == 10,
                        value <= str(MAX_FPL_SCORE),
                    ),
                ),
            )

        return and_(postgres_score("team_h_score"), postgres_score("team_a_score"))

    raise RuntimeError(f"Unsupported fixture database dialect: {dialect}")


def _bounded_fixtures(
    db: Session,
    *,
    finished: bool,
    limit: int,
    valid_scores_only: bool = False,
) -> list[tuple[Fixture, int]]:
    """Return at most ``limit`` relevant fixtures per participating team."""
    if limit <= 0:
        return []

    fixture_teams = union_all(
        select(Fixture.id.label("fixture_id"), Fixture.team_h.label("team_id")),
        select(Fixture.id.label("fixture_id"), Fixture.team_a.label("team_id")),
    ).subquery()
    conditions = [Fixture.finished.is_(finished)]
    if valid_scores_only:
        conditions.append(_valid_score_predicate(db))
    ranked = (
        select(
            fixture_teams.c.fixture_id,
            fixture_teams.c.team_id,
            func.row_number()
            .over(
                partition_by=fixture_teams.c.team_id,
                order_by=_fixture_order(descending=finished),
            )
            .label("row_number"),
        )
        .join(Fixture, Fixture.id == fixture_teams.c.fixture_id)
        .where(*conditions)
        .subquery()
    )
    return list(
        db.execute(
            select(Fixture, ranked.c.team_id)
            .join(ranked, Fixture.id == ranked.c.fixture_id)
            .where(ranked.c.row_number <= limit)
        ).all()
    )


def fixture_analysis(db: Session, *, limit: int = 10) -> list[dict[str, Any]]:
    """Project the next stored fixtures for every team without remote requests."""
    teams = db.scalars(select(Team)).all()
    fixtures_by_team: dict[int, list[Fixture]] = {team.id: [] for team in teams}
    teams_by_id = {team.id: team for team in teams}
    for fixture, team_id in _bounded_fixtures(db, finished=False, limit=limit):
        if team_id in fixtures_by_team:
            fixtures_by_team[team_id].append(fixture)

    rows = []
    for team in teams:
        records = []
        for fixture in sorted(fixtures_by_team[team.id], key=_fixture_key):
            is_home = fixture.team_h == team.id
            opponent_id = fixture.team_a if is_home else fixture.team_h
            opponent = teams_by_id.get(opponent_id)
            raw = fixture.raw if isinstance(fixture.raw, dict) else {}
            difficulty = _valid_difficulty(
                raw,
                "team_h_difficulty" if is_home else "team_a_difficulty",
            )
            records.append(
                {
                    "event": fixture.event,
                    "kickoff_time": fixture.kickoff_time,
                    "opponent": opponent.name if opponent else str(opponent_id),
                    "opponent_id": opponent_id,
                    "is_home": is_home,
                    "difficulty": difficulty,
                    "badge_url": _badge_url(
                        opponent_id,
                        opponent.name if opponent else None,
                        opponent.code if opponent else None,
                    ),
                }
            )

        numeric = [
            record["difficulty"]
            for record in records
            if record["difficulty"] is not None
        ]
        rows.append(
            {
                "team": team,
                "badge_url": _badge_url(team.id, team.name, team.code),
                "fixtures": records,
                "available": len(records),
                "complete": bool(records) and len(numeric) == len(records),
                "average_difficulty": sum(numeric) / len(numeric) if numeric else None,
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
    fixtures_by_team: dict[int, list[Fixture]] = {team.id: [] for team in teams}
    teams_by_id = {team.id: team for team in teams}
    for fixture, team_id in _bounded_fixtures(
        db,
        finished=True,
        limit=limit,
        valid_scores_only=True,
    ):
        if team_id in fixtures_by_team:
            fixtures_by_team[team_id].append(fixture)

    rows = []
    for team in teams:
        results = []
        for fixture in sorted(fixtures_by_team[team.id], key=_fixture_key):
            raw = fixture.raw if isinstance(fixture.raw, dict) else {}
            home_score = _valid_score(raw.get("team_h_score"))
            away_score = _valid_score(raw.get("team_a_score"))
            if home_score is None or away_score is None:
                continue
            is_home = fixture.team_h == team.id
            goals_for, goals_against = (
                (home_score, away_score) if is_home else (away_score, home_score)
            )
            result = (
                "W"
                if goals_for > goals_against
                else "D"
                if goals_for == goals_against
                else "L"
            )
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
                    "badge_url": _badge_url(
                        opponent_id,
                        opponent.name if opponent else None,
                        opponent.code if opponent else None,
                    ),
                }
            )

        wins = sum(result["result"] == "W" for result in results)
        draws = sum(result["result"] == "D" for result in results)
        losses = sum(result["result"] == "L" for result in results)
        points = wins * 3 + draws
        rows.append(
            {
                "team": team,
                "badge_url": _badge_url(team.id, team.name, team.code),
                "results": results,
                "wins": wins,
                "draws": draws,
                "losses": losses,
                "points": points,
                "points_per_game": points / len(results) if results else None,
                "goals_for": sum(result["goals_for"] for result in results),
                "goals_against": sum(result["goals_against"] for result in results),
                "available": len(results),
                "complete": len(results) == limit,
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
