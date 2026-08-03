from __future__ import annotations

from typing import Any

from app.services.team_recommender import STRATEGIES, recommend_team_cached


TEMPLATE_STRATEGIES = ("best_team", "balanced", "value", "safe", "differential", "upside")


def template_summaries(rows: list[dict[str, Any]], budget: float) -> list[dict[str, Any]]:
    return [
        {
            "key": strategy,
            "label": STRATEGIES[strategy]["label"],
            "recommendation": recommend_team_cached(rows, budget, strategy),
        }
        for strategy in TEMPLATE_STRATEGIES
    ]


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
    candidates.sort(
        key=lambda row: (
            getattr(row["snapshot"], "projected_points_5", 0) or 0,
            getattr(row["snapshot"], "forward_value", 0) or 0,
            -(getattr(row["snapshot"], "price", 0) or 0),
        ),
        reverse=True,
    )
    return candidates[:limit]
