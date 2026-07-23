from __future__ import annotations

from collections import Counter
from typing import Any


POSITION_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
FORMATIONS = {
    "3-4-3": {"DEF": 3, "MID": 4, "FWD": 3},
    "3-5-2": {"DEF": 3, "MID": 5, "FWD": 2},
    "4-3-3": {"DEF": 4, "MID": 3, "FWD": 3},
    "4-4-2": {"DEF": 4, "MID": 4, "FWD": 2},
    "4-5-1": {"DEF": 4, "MID": 5, "FWD": 1},
    "5-3-2": {"DEF": 5, "MID": 3, "FWD": 2},
    "5-4-1": {"DEF": 5, "MID": 4, "FWD": 1},
}


def _score(row: dict[str, Any]) -> float:
    snapshot = row["snapshot"]
    projected = float(snapshot.projected_points_5 or 0)
    raw_efficiency = float(snapshot.total_points or 0) / max(float(snapshot.price or 1), 1)
    reliable = float(snapshot.reliable_value or 0)
    forward = float(snapshot.forward_value or 0)
    availability = float(snapshot.availability_factor or 0)
    risk = float(snapshot.rotation_risk or 50)
    return round(
        projected * 0.60
        + raw_efficiency * 0.15
        + reliable * 0.15
        + forward * 0.10
        + availability * 2
        - risk * 0.015,
        4,
    )


def _candidates(rows: list[dict[str, Any]], position: str) -> list[dict[str, Any]]:
    position_rows = [row for row in rows if row["player"].position_short == position and row["snapshot"].price > 0]
    ranked = sorted(position_rows, key=_score, reverse=True)
    cheapest = sorted(position_rows, key=lambda row: row["snapshot"].price)
    unique = {row["player"].id: row for row in ranked[:32]}
    unique.update({row["player"].id: row for row in cheapest[:10]})
    return list(unique.values())


def recommend_team(rows: list[dict[str, Any]], budget: float = 100.0) -> dict[str, Any]:
    """Build an explainable best-value FPL squad under the real squad rules."""
    if not rows:
        raise ValueError("No player data is available for recommendations.")
    candidates = {position: _candidates(rows, position) for position in POSITION_COUNTS}
    if any(len(items) < count for position, count in POSITION_COUNTS.items() for items in [candidates[position]]):
        raise ValueError("There are not enough eligible players to build a squad.")

    budget_units = int(round(budget * 10))
    states = [(0, 0.0, [], Counter())]
    for position, count in POSITION_COUNTS.items():
        for _ in range(count):
            next_states = []
            for spent, score, selected, clubs in states:
                selected_ids = {row["player"].id for row in selected}
                for row in candidates[position]:
                    player = row["player"]
                    if player.id in selected_ids:
                        continue
                    price_units = int(round(float(row["snapshot"].price) * 10))
                    club_id = row["team"].id
                    if spent + price_units > budget_units or clubs[club_id] >= 3:
                        continue
                    updated_clubs = clubs.copy()
                    updated_clubs[club_id] += 1
                    next_states.append((spent + price_units, score + _score(row), selected + [row], updated_clubs))
            next_states.sort(key=lambda state: (state[1], -state[0]), reverse=True)
            states = next_states[:5000]
            if not states:
                raise ValueError("No valid squad fits the selected budget and club limits.")

    spent, score, selected, _ = max(states, key=lambda state: (state[1], -state[0]))
    selected_by_position = {position: sorted([row for row in selected if row["player"].position_short == position], key=_score, reverse=True) for position in POSITION_COUNTS}
    best_formation = None
    best_starting = []
    for formation, shape in FORMATIONS.items():
        starting = selected_by_position["GKP"][:1] + selected_by_position["DEF"][:shape["DEF"]] + selected_by_position["MID"][:shape["MID"]] + selected_by_position["FWD"][:shape["FWD"]]
        if len(starting) == 11 and (best_formation is None or sum(_score(row) for row in starting) > sum(_score(row) for row in best_starting)):
            best_formation, best_starting = formation, starting
    starting_ids = {row["player"].id for row in best_starting}
    captain = max(best_starting, key=_score)
    vice_captain = max((row for row in best_starting if row["player"].id != captain["player"].id), key=_score)

    def decorate(row: dict[str, Any], role: str) -> dict[str, Any]:
        return {"row": row, "role": role, "score": _score(row), "captain": row["player"].id == captain["player"].id, "vice_captain": row["player"].id == vice_captain["player"].id}

    return {
        "budget": budget,
        "spent": round(spent / 10, 1),
        "remaining": round(budget - spent / 10, 1),
        "score": round(score, 2),
        "formation": best_formation,
        "starting": [decorate(row, "Starting XI") for row in best_starting],
        "bench": [decorate(row, "Bench") for row in sorted((row for row in selected if row["player"].id not in starting_ids), key=_score, reverse=True)],
        "method": "Projected points weighted with reliable value, raw efficiency, availability, and rotation security; constrained by FPL squad rules.",
    }
