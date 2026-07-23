from __future__ import annotations

from collections import Counter
from threading import RLock
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
    "best_team": {"label": "Best Team", "projected": .84, "raw": .03, "reliable": .06, "forward": .07, "availability": 4.0, "risk": .025, "ownership": 0.0},
    "balanced": {"label": "Balanced", "projected": .60, "raw": .15, "reliable": .15, "forward": .10, "availability": 2.0, "risk": .015, "ownership": 0.0},
    "upside": {"label": "Maximum Upside", "projected": .78, "raw": .08, "reliable": .04, "forward": .10, "availability": 1.0, "risk": .005, "ownership": 0.0},
    "safe": {"label": "Safe Starters", "projected": .42, "raw": .08, "reliable": .28, "forward": .07, "availability": 3.0, "risk": .04, "ownership": .0},
    "differential": {"label": "Differentials", "projected": .55, "raw": .12, "reliable": .10, "forward": .08, "availability": 1.5, "risk": .01, "ownership": -.025},
    "value": {"label": "Value First", "projected": .25, "raw": .30, "reliable": .28, "forward": .12, "availability": 1.5, "risk": .02, "ownership": .0},
}
_RECOMMENDATION_CACHE: dict[tuple[Any, float, str], dict[str, Any]] = {}
_RECOMMENDATION_CACHE_LOCK = RLock()
_MAX_CACHE_ENTRIES = 24


def _projected_output(row: dict[str, Any]) -> float:
    snapshot = row["snapshot"]
    projected = float(getattr(snapshot, "projected_points_5", 0) or 0)
    if projected <= 0:
        projected = float(getattr(snapshot, "points_per_game", 0) or 0) * 5
    expected_minutes = float(getattr(snapshot, "expected_minutes", 0) or 0)
    minutes_factor = min(expected_minutes / 450, 1.0) if expected_minutes else 1.0
    availability = float(getattr(snapshot, "availability_factor", 0) or 0)
    if availability <= 0:
        return 0.0
    return projected * (.70 + (.30 * minutes_factor)) * availability


def _score(row: dict[str, Any], strategy: str = "best_team") -> float:
    weights = STRATEGIES.get(strategy, STRATEGIES["best_team"])
    snapshot = row["snapshot"]
    projected = _projected_output(row)
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


def _best_lineup(selected: list[dict[str, Any]], strategy: str) -> tuple[str | None, list[dict[str, Any]], float]:
    by_position = {
        position: sorted(
            [row for row in selected if row["player"].position_short == position],
            key=lambda row: _projected_output(row),
            reverse=True,
        )
        for position in POSITION_COUNTS
    }
    best_formation = None
    best_starting: list[dict[str, Any]] = []
    best_score = -1.0
    for formation, shape in FORMATIONS.items():
        starting = by_position["GKP"][:1] + by_position["DEF"][:shape["DEF"]] + by_position["MID"][:shape["MID"]] + by_position["FWD"][:shape["FWD"]]
        if len(starting) != 11:
            continue
        captain = max(starting, key=_projected_output)
        score = sum(_projected_output(row) for row in starting) + _projected_output(captain)
        if strategy in {"safe", "best_team"}:
            score += sum(_score(row, strategy) for row in starting) * .05
        if score > best_score:
            best_formation, best_starting, best_score = formation, starting, score
    return best_formation, best_starting, best_score


def _candidates(rows: list[dict[str, Any]], position: str, strategy: str) -> list[dict[str, Any]]:
    position_rows = [row for row in rows if row["player"].position_short == position and row["snapshot"].price > 0]
    ranked = sorted(position_rows, key=lambda row: _score(row, strategy), reverse=True)
    cheapest = sorted(position_rows, key=lambda row: row["snapshot"].price)
    # Keep enough expensive and budget options to find a strong squad, without
    # expanding the combinatorial search to every player in the database.
    unique = {row["player"].id: row for row in ranked[:20]}
    unique.update({row["player"].id: row for row in cheapest[:6]})
    for club_id in {row["team"].id for row in position_rows}:
        club_players = [row for row in cheapest if row["team"].id == club_id]
        if club_players:
            unique[club_players[0]["player"].id] = club_players[0]
    return list(unique.values())


def _find_feasible_squad(rows: list[dict[str, Any]], budget_units: int) -> list[dict[str, Any]] | None:
    """Find any legal squad independent of ranking, preventing false no-solution errors."""
    by_position = {
        position: sorted(
            [row for row in rows if row["player"].position_short == position and float(row["snapshot"].price or 0) > 0],
            key=lambda row: (float(row["snapshot"].price), -_projected_output(row)),
        )
        for position in POSITION_COUNTS
    }
    if any(len(by_position[position]) < count for position, count in POSITION_COUNTS.items()):
        return None

    slots = [
        position
        for position in sorted(POSITION_COUNTS, key=lambda item: len(by_position[item]))
        for _ in range(POSITION_COUNTS[position])
    ]

    def visit(index: int, spent: int, selected: list[dict[str, Any]], clubs: Counter) -> list[dict[str, Any]] | None:
        if index == len(slots):
            return selected
        position = slots[index]
        selected_ids = {row["player"].id for row in selected}
        for row in by_position[position]:
            player = row["player"]
            if player.id in selected_ids:
                continue
            price_units = int(round(float(row["snapshot"].price) * 10))
            club_id = row["team"].id
            if spent + price_units > budget_units or clubs[club_id] >= 3:
                continue
            clubs[club_id] += 1
            result = visit(index + 1, spent + price_units, selected + [row], clubs)
            clubs[club_id] -= 1
            if result is not None:
                return result
        return None

    return visit(0, 0, [], Counter())


def recommend_team(rows: list[dict[str, Any]], budget: float = 100.0, strategy: str = "best_team") -> dict[str, Any]:
    """Build an explainable highest-projected-output FPL squad under real squad rules."""
    if not rows:
        raise ValueError("No player data is available for recommendations.")
    strategy = strategy if strategy in STRATEGIES else "best_team"
    candidates = {position: _candidates(rows, position, strategy) for position in POSITION_COUNTS}
    if any(len(items) < count for position, count in POSITION_COUNTS.items() for items in [candidates[position]]):
        raise ValueError("There are not enough eligible players to build a squad.")

    budget_units = int(round(budget * 10))
    feasible = _find_feasible_squad(rows, budget_units)
    if feasible is None:
        raise ValueError("No valid squad fits the selected budget and club limits.")
    states = [(0, 0.0, [], Counter())]
    beam_failed = False
    score_by_id = {row["player"].id: _score(row, strategy) for items in candidates.values() for row in items}
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
                    next_states.append((spent + price_units, score + score_by_id[player.id], selected + [row], updated_clubs))
            next_states.sort(key=lambda state: (state[1], -state[0]), reverse=True)
            retained = next_states[:600]
            diverse = sorted(next_states, key=lambda state: (len(state[3]), state[1], -state[0]), reverse=True)[:250]
            by_ids = {tuple(row["player"].id for row in state[2]): state for state in retained}
            by_ids.update({tuple(row["player"].id for row in state[2]): state for state in diverse})
            states = list(by_ids.values())[:850]
            if not states:
                beam_failed = True
                break
        if beam_failed:
            break

    if beam_failed:
        selected = feasible
        spent = sum(int(round(float(row["snapshot"].price) * 10)) for row in selected)
        score = sum(_score(row, strategy) for row in selected)
        states = [(spent, score, selected, Counter(row["team"].id for row in selected))]

    def team_objective(state: tuple[int, float, list[dict[str, Any]], Counter]) -> tuple[float, float, float]:
        _, lineup, lineup_score = _best_lineup(state[2], strategy)
        starting_ids = {row["player"].id for row in lineup}
        bench_output = sum(_projected_output(row) for row in state[2] if row["player"].id not in starting_ids)
        return (lineup_score + (bench_output * .05), state[1], -state[0])

    spent, score, selected, _ = max(states, key=team_objective)
    best_formation, best_starting, lineup_score = _best_lineup(selected, strategy)
    starting_ids = {row["player"].id for row in best_starting}
    captain = max(best_starting, key=_projected_output)
    vice_captain = max((row for row in best_starting if row["player"].id != captain["player"].id), key=_projected_output)

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
        "score": round(lineup_score, 2),
        "strategy": strategy,
        "strategy_label": STRATEGIES[strategy]["label"],
        "formation": best_formation,
        "starting": [decorate(row, "Starting XI") for row in best_starting],
        "bench": [decorate(row, "Bench") for row in sorted((row for row in selected if row["player"].id not in starting_ids), key=lambda row: score_by_id.get(row["player"].id, _score(row, strategy)), reverse=True)],
        "method": f"{STRATEGIES[strategy]['label']} mode: the squad is selected to maximize projected starting-XI output, with captaincy, expected minutes, availability, formation, and bench depth considered before value metrics are used as tie-breakers; constrained by FPL squad rules.",
    }


def recommend_team_cached(rows: list[dict[str, Any]], budget: float = 100.0, strategy: str = "best_team") -> dict[str, Any]:
    """Cache recommendations until the latest database snapshot changes."""
    latest = max((getattr(row["snapshot"], "captured_at", None) for row in rows), default=None)
    snapshot_key = latest.isoformat() if latest is not None else len(rows)
    key = (snapshot_key, round(budget, 1), strategy)
    with _RECOMMENDATION_CACHE_LOCK:
        cached = _RECOMMENDATION_CACHE.get(key)
    if cached is not None:
        return cached

    result = recommend_team(rows, budget, strategy)
    with _RECOMMENDATION_CACHE_LOCK:
        _RECOMMENDATION_CACHE[key] = result
        while len(_RECOMMENDATION_CACHE) > _MAX_CACHE_ENTRIES:
            _RECOMMENDATION_CACHE.pop(next(iter(_RECOMMENDATION_CACHE)))
    return result
