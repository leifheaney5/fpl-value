from __future__ import annotations

from typing import Any

from app.services.team_recommender import (
    NotReadyError,
    STRATEGIES,
    recommend_team_cached,
)


TEMPLATE_STRATEGIES = ("best_team", "balanced", "value", "safe", "differential", "upside")


def template_summaries(rows: list[dict[str, Any]], budget: float) -> dict[str, Any]:
    """Build one squad per strategy, or explain why none can be built.

    Every strategy shares the same player pool, so if the pool cannot support a
    recommendation none of them can. Reporting that once is clearer than showing
    six identical failures.
    """
    summaries: list[dict[str, Any]] = []
    for strategy in TEMPLATE_STRATEGIES:
        try:
            recommendation = recommend_team_cached(rows, budget, strategy)
        except NotReadyError as exc:
            return {
                "templates": [],
                "checks": exc.checks,
                "activates_when": exc.activates_when,
                "error": None,
            }
        except ValueError as exc:
            return {
                "templates": [],
                "checks": None,
                "activates_when": None,
                "error": str(exc),
            }
        summaries.append(
            {
                "key": strategy,
                "label": STRATEGIES[strategy]["label"],
                "recommendation": recommendation,
            }
        )
    return {
        "templates": summaries,
        "checks": None,
        "activates_when": None,
        "error": None,
    }


def price_slot_suggestions(
    selected_row: dict[str, Any],
    rows: list[dict[str, Any]],
    limit: int = 3,
) -> list[dict[str, Any]]:
    position = selected_row["player"].position_short
    slot_price = selected_row["snapshot"].price
    candidates = [
        row for row in rows
        if row["player"].position_short == position
        and row["snapshot"].price <= slot_price
        and row["player"].id != selected_row["player"].id
    ]
    def rank(row: dict[str, Any]) -> tuple[int, float, float, float]:
        """Order alternatives, keeping unprojectable players out of the lead.

        A player with no projection is not a better suggestion than one with a
        low projection; it is an unknown, so it sorts behind everything known.
        """
        projected = getattr(row["snapshot"], "projected_points_5", None)
        forward = getattr(row["snapshot"], "forward_value", None)
        known = 1 if projected is not None or forward is not None else 0
        return (
            known,
            float(projected or 0),
            float(forward or 0),
            -float(getattr(row["snapshot"], "price", 0) or 0),
        )

    candidates.sort(key=rank, reverse=True)
    return candidates[:limit]
