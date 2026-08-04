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
    availability = getattr(snapshot, "availability", None)
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
