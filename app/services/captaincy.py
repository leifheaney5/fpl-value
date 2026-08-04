"""Who to captain, and how confident that answer is.

Captaincy doubles one player's score, so it is judged on the next gameweek
alone -- never on the five-fixture forward window the rest of the application
uses. The distinction matters most in a double gameweek, where a player has two
fixtures and the five-fixture average silently halves the thing that makes them
worth captaining.

Everything here is derived from data already stored on the snapshot, so this
module needs no migration and no change to the refresh pipeline.
"""

from __future__ import annotations

from typing import Any

from app.analytics.metrics import project_next_fixtures

# The snapshot attributes this module reads. Named explicitly because every one
# of them is reached through ``getattr`` with a default, so a renamed or
# misremembered column degrades to "no projection for anybody" rather than
# raising. That is exactly how ``availability`` -- which does not exist; the
# column is ``availability_factor`` -- silently emptied the shortlist.
SNAPSHOT_FIELDS = (
    "expected_minutes",
    "availability_factor",
    "form",
    "points_per_game",
    "points_per_90",
    "upcoming_fixtures",
)


def fixtures_in_gameweek(snapshot: Any, gameweek: int | None) -> list[dict[str, Any]]:
    """Every fixture the player's team plays in that gameweek.

    A list, not a single fixture: a double gameweek has two, and collapsing
    them to one is the specific error this function exists to avoid.
    """
    if gameweek is None:
        return []
    fixtures = getattr(snapshot, "upcoming_fixtures", None) or []
    return [fixture for fixture in fixtures if fixture.get("event") == gameweek]


def next_gameweek_projection(snapshot: Any, gameweek: int | None) -> float | None:
    """Projected points for that gameweek, or None when there is no basis.

    Returns 0.0 -- a real zero -- for a blank gameweek, because a player with
    no fixture genuinely cannot score. Returns None when expected minutes are
    unknown, because that is an absence of information rather than a forecast
    of a blank. The two must not collapse into each other.
    """
    if gameweek is None:
        return None

    expected = getattr(snapshot, "expected_minutes", None)
    availability = getattr(snapshot, "availability_factor", None)
    if expected is None or availability is None:
        return None

    fixtures = fixtures_in_gameweek(snapshot, gameweek)
    if not fixtures:
        return 0.0

    return project_next_fixtures(
        form=float(getattr(snapshot, "form", 0.0) or 0.0),
        points_per_game=float(getattr(snapshot, "points_per_game", 0.0) or 0.0),
        points_per_90=getattr(snapshot, "points_per_90", None),
        expected_minutes_value=expected,
        availability=availability,
        fixtures=fixtures,
    )


# Below this many expected minutes a captaincy recommendation is not worth
# making: the doubled downside of a benching outweighs the upside.
MINUTES_CONFIDENT = 75.0
MINUTES_TENTATIVE = 60.0


def _confidence(expected: float | None, fixture_count: int) -> str:
    if expected is None:
        return "Low"
    if expected >= MINUTES_CONFIDENT and fixture_count >= 1:
        return "High"
    if expected >= MINUTES_TENTATIVE:
        return "Medium"
    return "Low"


def _reasoning(fixtures: list[dict[str, Any]], expected: float | None) -> str:
    if not fixtures:
        return "No fixture in this gameweek, so no captaincy case."

    opponents = ", ".join(
        f"{fixture.get('opponent', '?')} "
        f"({'H' if fixture.get('is_home') else 'A'})"
        for fixture in fixtures
    )
    parts = []
    if len(fixtures) == 2:
        parts.append(f"Plays two fixtures this gameweek: {opponents}")
    elif len(fixtures) > 2:
        parts.append(f"Plays {len(fixtures)} fixtures this gameweek: {opponents}")
    else:
        parts.append(f"Faces {opponents}")

    if expected is not None:
        parts.append(f"expected around {expected:.0f} minutes")

    easiest = min((fixture.get("difficulty", 3) for fixture in fixtures), default=3)
    if easiest <= 2:
        parts.append("against a low-difficulty opponent")
    elif easiest >= 4:
        parts.append("against a high-difficulty opponent")

    return ". ".join(parts) + "."


def captain_candidates(
    snapshots: list[Any], gameweek: int | None, limit: int = 8
) -> list[dict[str, Any]]:
    """Rank captaincy options for one gameweek.

    Players whose projection has no basis are excluded rather than ranked last.
    Ordering them at the bottom would assert they are the worst options, which
    is a claim the data does not support; their absence is the honest answer.
    """
    if gameweek is None:
        return []

    candidates = []
    for snapshot in snapshots:
        projection = next_gameweek_projection(snapshot, gameweek)
        if projection is None:
            continue
        fixtures = fixtures_in_gameweek(snapshot, gameweek)
        expected = getattr(snapshot, "expected_minutes", None)
        candidates.append(
            {
                "player": getattr(snapshot, "player", None),
                "projection": projection,
                "captain_points": round(projection * 2, 2),
                "fixture_count": len(fixtures),
                "opponents": fixtures,
                "expected_minutes": expected,
                "confidence": _confidence(expected, len(fixtures)),
                "reasoning": _reasoning(fixtures, expected),
            }
        )

    candidates.sort(key=lambda row: row["projection"], reverse=True)
    return candidates[:limit]
