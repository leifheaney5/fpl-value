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
STRATEGIES = {
    "balanced": {"label": "Balanced", "projected": .60, "raw": .15, "reliable": .15, "forward": .10, "availability": 2.0, "risk": .015, "ownership": 0.0},
    "upside": {"label": "Maximum Upside", "projected": .78, "raw": .08, "reliable": .04, "forward": .10, "availability": 1.0, "risk": .005, "ownership": 0.0},
    "safe": {"label": "Safe Starters", "projected": .42, "raw": .08, "reliable": .28, "forward": .07, "availability": 3.0, "risk": .04, "ownership": .0},
    "differential": {"label": "Differentials", "projected": .55, "raw": .12, "reliable": .10, "forward": .08, "availability": 1.5, "risk": .01, "ownership": -.025},
    "value": {"label": "Value First", "projected": .25, "raw": .30, "reliable": .28, "forward": .12, "availability": 1.5, "risk": .02, "ownership": .0},
}


def _score(row: dict[str, Any], strategy: str = "balanced") -> float:
    weights = STRATEGIES.get(strategy, STRATEGIES["balanced"])
    snapshot = row["snapshot"]
    projected = float(snapshot.projected_points_5 or 0)
    raw_efficiency = float(snapshot.total_points or 0) / max(float(snapshot.price or 1), 1)
    reliable = float(snapshot.reliable_value or 0)
    forward = float(snapshot.forward_value or 0)
    availability = float(snapshot.availability_factor or 0)
    risk = float(snapshot.rotation_risk or 50)
    ownership = float(getattr(snapshot, "ownership", 0) or 0)
    return round(
        projected * weights["projected"]
        + raw_efficiency * weights["raw"]
        + reliable * weights["reliable"]
        + forward * weights["forward"]
        + availability * weights["availability"]
        - risk * weights["risk"]
        + ownership * weights["ownership"],
        4,
    )


def _candidates(rows: list[dict[str, Any]], position: str, strategy: str) -> list[dict[str, Any]]:
    position_rows = [row for row in rows if row["player"].position_short == position and row["snapshot"].price > 0]
    ranked = sorted(position_rows, key=lambda row: _score(row, strategy), reverse=True)
    cheapest = sorted(position_rows, key=lambda row: row["snapshot"].price)
    unique = {row["player"].id: row for row in ranked[:32]}
    unique.update({row["player"].id: row for row in cheapest[:10]})
    return list(unique.values())


def recommend_team(rows: list[dict[str, Any]], budget: float = 100.0, strategy: str = "balanced") -> dict[str, Any]:
    """Build an explainable best-value FPL squad under the real squad rules."""
    if not rows:
        raise ValueError("No player data is available for recommendations.")
    strategy = strategy if strategy in STRATEGIES else "balanced"
    candidates = {position: _candidates(rows, position, strategy) for position in POSITION_COUNTS}
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
                    next_states.append((spent + price_units, score + _score(row, strategy), selected + [row], updated_clubs))
            next_states.sort(key=lambda state: (state[1], -state[0]), reverse=True)
            states = next_states[:5000]
            if not states:
                raise ValueError("No valid squad fits the selected budget and club limits.")

    spent, score, selected, _ = max(states, key=lambda state: (state[1], -state[0]))
    selected_by_position = {position: sorted([row for row in selected if row["player"].position_short == position], key=lambda row: _score(row, strategy), reverse=True) for position in POSITION_COUNTS}
    best_formation = None
    best_starting = []
    for formation, shape in FORMATIONS.items():
        starting = selected_by_position["GKP"][:1] + selected_by_position["DEF"][:shape["DEF"]] + selected_by_position["MID"][:shape["MID"]] + selected_by_position["FWD"][:shape["FWD"]]
        if len(starting) == 11 and (best_formation is None or sum(_score(row, strategy) for row in starting) > sum(_score(row, strategy) for row in best_starting)):
            best_formation, best_starting = formation, starting
    starting_ids = {row["player"].id for row in best_starting}
    captain = max(best_starting, key=lambda row: _score(row, strategy))
    vice_captain = max((row for row in best_starting if row["player"].id != captain["player"].id), key=lambda row: _score(row, strategy))

    def decorate(row: dict[str, Any], role: str) -> dict[str, Any]:
        snapshot = row["snapshot"]
        reasons = []
        if snapshot.projected_points_5 and snapshot.projected_points_5 >= 20: reasons.append("strong projection")
        if snapshot.reliable_value and snapshot.reliable_value >= 5: reasons.append("reliable value")
        if snapshot.rotation_risk is not None and snapshot.rotation_risk <= 25: reasons.append("secure minutes")
        if strategy == "differential" and float(getattr(snapshot, "ownership", 0) or 0) <= 10: reasons.append("low ownership")
        return {"row": row, "role": role, "score": _score(row, strategy), "reason": ", ".join(reasons[:2]) or "best available fit", "captain": row["player"].id == captain["player"].id, "vice_captain": row["player"].id == vice_captain["player"].id}

    return {
        "budget": budget,
        "spent": round(spent / 10, 1),
        "remaining": round(budget - spent / 10, 1),
        "score": round(score, 2),
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy]["label"],
        "formation": best_formation,
        "starting": [decorate(row, "Starting XI") for row in best_starting],
        "bench": [decorate(row, "Bench") for row in sorted((row for row in selected if row["player"].id not in starting_ids), key=_score, reverse=True)],
        "method": f"{STRATEGIES[strategy]['label']} mode: projected points, reliable value, raw efficiency, availability, rotation security, and ownership were weighted according to the selected strategy; constrained by FPL squad rules.",
    }
