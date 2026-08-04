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


class NotReadyError(ValueError):
    """The inputs cannot support a recommendation.

    Raised instead of returning a squad built from data that cannot distinguish
    one player from another.
    """

    def __init__(self, checks: list[dict[str, Any]], activates_when: str):
        self.checks = checks
        self.activates_when = activates_when
        failed = "; ".join(
            check["detail"] for check in checks if not check["passed"]
        )
        super().__init__(f"Recommendations are not available: {failed}")


# A squad is fifteen players, so fewer than fifteen projections cannot fill one.
# The share requirement stops the optimiser choosing from a sliver of the market
# while the rest of the pool is unprojectable.
MIN_PROJECTED_PLAYERS = 15
MIN_PROJECTED_SHARE = 0.25
MIN_OBJECTIVE_STDEV = 0.05


def _projected_output(row: dict[str, Any]) -> float | None:
    """Projected output over the forward window, or None when unknowable.

    Returning 0.0 for an unprojectable player used to make every player look
    identical in preseason, which handed the squad choice to an arbitrary
    tie-breaker.

    There used to be a second escape here: when ``projected_points_5`` was null
    this fell back to ``points_per_game * 5``. In preseason the FPL API still
    serves *last season's* counting stats, so that fallback silently forecast a
    finished season -- and ``points_per_game`` is a rate with no sample-size
    behind it. On 2026-08-04 the live deployment ranked a third-choice keeper
    (7 points from one 90-minute appearance, rate 7.0) above Haaland (239
    points from 2953 minutes, rate 6.8), selected him, and captained him.

    The fallback is gone. A player with no projection has no projected output,
    the readiness checks in ``validate_pool`` then fail honestly, and
    ``/recommendation`` shows its not-ready panel until a match has been
    played. See docs/RECOMMENDER_AUDIT.md.
    """
    snapshot = row["snapshot"]
    projected = getattr(snapshot, "projected_points_5", None)
    if projected is None:
        return None

    availability = getattr(snapshot, "availability_factor", None)
    if availability is None or availability <= 0:
        return 0.0

    # Unknown minutes must not read as full minutes. ``projected_points_5`` is
    # itself null without an expected-minutes estimate, so this is belt and
    # braces -- but defaulting the unknown case to 1.0 was a fail-open in an
    # application whose whole premise is that missing is not zero.
    expected_minutes = getattr(snapshot, "expected_minutes", None)
    if expected_minutes is None:
        return None
    minutes_factor = min(float(expected_minutes) / 450, 1.0)
    return float(projected) * (.70 + (.30 * minutes_factor)) * float(availability)


def _output_or_zero(row: dict[str, Any]) -> float:
    """Projected output for arithmetic that must produce a number."""
    value = _projected_output(row)
    return 0.0 if value is None else value


def validate_pool(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Check that the inputs can actually distinguish one squad from another."""
    projections = [
        value for value in (_projected_output(row) for row in rows) if value is not None
    ]
    positive = [value for value in projections if value > 0]
    if projections:
        mean = sum(projections) / len(projections)
        variance = sum((value - mean) ** 2 for value in projections) / len(projections)
        stdev = variance ** 0.5
    else:
        stdev = 0.0

    by_position = Counter(row["player"].position_short for row in rows)
    return [
        {
            "name": "player_pool",
            "passed": len(rows) >= 15,
            "detail": f"{len(rows)} players available, at least 15 required",
        },
        {
            "name": "position_pools",
            "passed": all(
                by_position.get(position, 0) >= count
                for position, count in POSITION_COUNTS.items()
            ),
            "detail": (
                "Each position needs enough players to fill its squad slots "
                f"(have {dict(by_position)}, need {POSITION_COUNTS})"
            ),
        },
        {
            "name": "projections_available",
            "passed": (
                len(positive) >= MIN_PROJECTED_PLAYERS
                and (not rows or len(positive) / len(rows) >= MIN_PROJECTED_SHARE)
            ),
            "detail": (
                f"{len(positive)} of {len(rows)} players have a positive "
                f"projection; at least {MIN_PROJECTED_PLAYERS} and "
                f"{MIN_PROJECTED_SHARE:.0%} of the pool are required"
            ),
        },
        {
            "name": "objective_variation",
            "passed": stdev > MIN_OBJECTIVE_STDEV,
            "detail": (
                f"Projection spread is {stdev:.3f}; below {MIN_OBJECTIVE_STDEV} "
                "the objective cannot tell one squad from another"
            ),
        },
    ]


def _score(row: dict[str, Any], strategy: str = "best_team") -> float:
    weights = STRATEGIES.get(strategy, STRATEGIES["best_team"])
    snapshot = row["snapshot"]
    projected = _output_or_zero(row)
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


def _captaincy_pair(
    starting: list[dict[str, Any]]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    """Pick the captain and vice-captain from the outfield players only.

    A goalkeeper is excluded on position, not on projection. Doubling a
    keeper's score is close to always wrong -- they cannot attack, and their
    ceiling is a clean sheet plus save points -- so this must not depend on the
    numbers happening to rank someone else first. On 2026-08-04 they did not:
    the live deployment captained a third-choice keeper.
    """
    outfield = [
        row for row in starting if row["player"].position_short != "GKP"
    ]
    # A legal XI always contains ten outfield players; fall back rather than
    # raise if a caller ever passes a partial lineup.
    pool = outfield or starting
    if not pool:
        return None, None
    captain = max(pool, key=_output_or_zero)
    others = [row for row in pool if row["player"].id != captain["player"].id]
    vice_captain = max(others, key=_output_or_zero) if others else captain
    return captain, vice_captain


def _best_lineup(selected: list[dict[str, Any]], strategy: str) -> tuple[str | None, list[dict[str, Any]], float]:
    by_position = {
        position: sorted(
            [row for row in selected if row["player"].position_short == position],
            key=lambda row: _output_or_zero(row),
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
        captain, _ = _captaincy_pair(starting)
        score = sum(_output_or_zero(row) for row in starting) + _output_or_zero(captain)
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
            key=lambda row: (float(row["snapshot"].price), -_output_or_zero(row)),
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


def _best_excluded(
    rows: list[dict[str, Any]],
    selected: list[dict[str, Any]],
    remaining: float,
    strategy: str,
) -> tuple[dict[str, Any] | None, float | None]:
    """The strongest affordable player left out, and what picking them would add.

    Answers "why is money still in the bank?" with a named alternative rather
    than leaving the reader to guess.
    """
    selected_ids = {row["player"].id for row in selected}
    weakest_by_position: dict[str, dict[str, Any]] = {}
    for row in selected:
        position = row["player"].position_short
        current = weakest_by_position.get(position)
        if current is None or _output_or_zero(row) < _output_or_zero(current):
            weakest_by_position[position] = row

    best: dict[str, Any] | None = None
    best_gain: float | None = None
    for row in rows:
        if row["player"].id in selected_ids:
            continue
        position = row["player"].position_short
        incumbent = weakest_by_position.get(position)
        if incumbent is None:
            continue
        # Affordable means the price difference fits in the remaining bank.
        extra_cost = float(row["snapshot"].price) - float(incumbent["snapshot"].price)
        if extra_cost > remaining:
            continue
        gain = _output_or_zero(row) - _output_or_zero(incumbent)
        if gain <= 0:
            continue
        if best_gain is None or gain > best_gain:
            best, best_gain = row, gain

    if best is None:
        return None, None
    return (
        {
            "player": best["player"],
            "team": best["team"],
            "snapshot": best["snapshot"],
            "score": _score(best, strategy),
        },
        round(best_gain, 2),
    )


def _budget_explanation(
    remaining: float,
    best_excluded: dict[str, Any] | None,
    marginal_gain: float | None,
) -> str:
    if remaining <= 0.05:
        return "The full budget is committed to the squad."
    if best_excluded is None:
        return (
            f"£{remaining:.1f}m remains because no affordable upgrade improved "
            "projected output; every candidate that fits the bank scored at or "
            "below the player already selected in that position."
        )
    name = best_excluded["player"].full_name
    return (
        f"£{remaining:.1f}m remains. The closest affordable upgrade is {name} "
        f"at £{best_excluded['snapshot'].price:.1f}m, worth about "
        f"{marginal_gain:+.2f} projected points, which did not beat the "
        "selected squad on the leading criteria."
    )


def recommend_team(rows: list[dict[str, Any]], budget: float = 100.0, strategy: str = "best_team") -> dict[str, Any]:
    """Build an explainable highest-projected-output FPL squad under real squad rules."""
    if not rows:
        raise ValueError("No player data is available for recommendations.")

    # Refuse before optimising. A squad built from indistinguishable inputs is
    # worse than no squad, because it looks like a recommendation.
    checks = validate_pool(rows)
    if not all(check["passed"] for check in checks):
        raise NotReadyError(
            checks,
            "Recommendations activate once the season has started and player "
            "projections differ from one another. Until then the optimiser has "
            "nothing to optimise.",
        )

    strategy = strategy if strategy in STRATEGIES else "best_team"
    candidates = {position: _candidates(rows, position, strategy) for position in POSITION_COUNTS}
    if any(len(items) < count for position, count in POSITION_COUNTS.items() for items in [candidates[position]]):
        raise ValueError("There are not enough eligible players to build a squad.")

    budget_units = int(round(budget * 10))
    feasible = _find_feasible_squad(rows, budget_units)
    if feasible is None:
        raise ValueError("No valid squad fits the selected budget and club limits.")

    # Expand the squad one slot at a time, and for every slot know the cheapest
    # possible cost of filling all the slots that come after it. Without this
    # reservation the search spends freely on early positions, reaches the last
    # position with nothing left, finds no legal continuation, and falls back to
    # the cheapest feasible squad -- which is how a full-budget request returned
    # a minimal team with most of the money unspent.
    slots = [
        position
        for position, count in POSITION_COUNTS.items()
        for _ in range(count)
    ]
    cheapest_units = {
        position: sorted(
            int(round(float(row["snapshot"].price) * 10)) for row in items
        )
        for position, items in candidates.items()
    }
    reserved_after: list[int] = []
    for index in range(len(slots)):
        remaining = Counter(slots[index + 1:])
        reserved_after.append(
            sum(
                sum(cheapest_units[position][:needed])
                for position, needed in remaining.items()
            )
        )

    states = [(0, 0.0, [], Counter())]
    beam_failed = False
    score_by_id = {row["player"].id: _score(row, strategy) for items in candidates.values() for row in items}
    for index, position in enumerate(slots):
        reserve = reserved_after[index]
        next_states = []
        for spent, score, selected, clubs in states:
            selected_ids = {row["player"].id for row in selected}
            for row in candidates[position]:
                player = row["player"]
                if player.id in selected_ids:
                    continue
                price_units = int(round(float(row["snapshot"].price) * 10))
                club_id = row["team"].id
                if spent + price_units + reserve > budget_units or clubs[club_id] >= 3:
                    continue
                updated_clubs = clubs.copy()
                updated_clubs[club_id] += 1
                next_states.append((spent + price_units, score + score_by_id[player.id], selected + [row], updated_clubs))
        # Keep the highest-scoring states, and among equal scores prefer the
        # ones that have committed more budget: an unspent pound buys nothing.
        next_states.sort(key=lambda state: (state[1], state[0]), reverse=True)
        retained = next_states[:600]
        diverse = sorted(next_states, key=lambda state: (len(state[3]), state[1], state[0]), reverse=True)[:250]
        by_ids = {tuple(row["player"].id for row in state[2]): state for state in retained}
        by_ids.update({tuple(row["player"].id for row in state[2]): state for state in diverse})
        states = list(by_ids.values())[:850]
        if not states:
            beam_failed = True
            break

    if beam_failed:
        # The search found no legal continuation. Fall back to a known-legal
        # squad, but the result must say so: this is the cheapest squad that
        # fits the rules, not the best squad for the budget.
        selected = feasible
        spent = sum(int(round(float(row["snapshot"].price) * 10)) for row in selected)
        score = sum(_score(row, strategy) for row in selected)
        states = [(spent, score, selected, Counter(row["team"].id for row in selected))]

    def team_objective(state: tuple[int, float, list[dict[str, Any]], Counter]) -> tuple[float, ...]:
        """Rank candidate squads, most important criterion first.

        The final key used to be ``-spent``, which broke ties by preferring the
        cheapest squad. With every projection equal -- as in preseason -- the
        first two keys tied for every state and that tie-breaker decided the
        squad outright, producing a minimal-cost team with the budget unspent.
        It is now ``spent``, so an otherwise-equal squad that uses the budget
        wins.
        """
        _, lineup, lineup_score = _best_lineup(state[2], strategy)
        starting_ids = {row["player"].id for row in lineup}
        bench_output = sum(_output_or_zero(row) for row in state[2] if row["player"].id not in starting_ids)
        expected_minutes_total = sum(
            float(getattr(row["snapshot"], "expected_minutes", None) or 0)
            for row in lineup
        )
        secure_starters = sum(
            1
            for row in lineup
            if (getattr(row["snapshot"], "rotation_risk", None) or 100) <= 25
        )
        return (
            lineup_score + (bench_output * .05),  # expected starting-XI points
            expected_minutes_total,               # expected minutes
            secure_starters,                      # role security
            state[1],                             # strategy score
            state[0],                             # budget utilisation
        )

    spent, score, selected, _ = max(states, key=team_objective)
    best_formation, best_starting, lineup_score = _best_lineup(selected, strategy)
    starting_ids = {row["player"].id for row in best_starting}
    captain, vice_captain = _captaincy_pair(best_starting)

    def decorate(row: dict[str, Any], role: str) -> dict[str, Any]:
        snapshot = row["snapshot"]
        reasons = []
        if snapshot.projected_points_5 and snapshot.projected_points_5 >= 20: reasons.append("strong projection")
        if snapshot.reliable_value and snapshot.reliable_value >= 5: reasons.append("reliable value")
        if snapshot.rotation_risk is not None and snapshot.rotation_risk <= 25: reasons.append("secure minutes")
        if strategy == "differential" and float(getattr(snapshot, "ownership", 0) or 0) <= 10: reasons.append("low ownership")
        if not reasons:
            # "best available fit" used to sit here. Every threshold above reads
            # a metric that is null before a match is played, so in preseason
            # all fifteen players carried that one string -- which reads as a
            # judgement while carrying no information. State the number the
            # selection was actually made on instead.
            projected_value = _projected_output(row)
            reasons.append(
                f"projected {projected_value:.1f} over the next "
                f"{snapshot.upcoming_fixture_count or 0} fixtures"
                if projected_value is not None
                else "no measured basis; included to satisfy squad rules"
            )
        return {"row": row, "role": role, "score": _score(row, strategy), "reason": ", ".join(reasons[:2]), "captain": row["player"].id == captain["player"].id, "vice_captain": row["player"].id == vice_captain["player"].id}

    remaining = round(budget - spent / 10, 1)
    best_excluded, marginal_gain = _best_excluded(
        rows, selected, remaining, strategy
    )

    return {
        "budget": budget,
        "spent": round(spent / 10, 1),
        "remaining": remaining,
        "checks": checks,
        "tie_breakers": [
            "Expected starting-XI points",
            "Expected minutes",
            "Role security",
            "Strategy score",
            "Budget utilisation",
        ],
        "best_excluded": best_excluded,
        "marginal_gain": marginal_gain,
        "optimised": not beam_failed,
        "budget_explanation": (
            "The optimiser found no legal squad within the budget, so this is "
            "the cheapest squad that satisfies the FPL rules. It is a fallback, "
            "not an optimised recommendation."
            if beam_failed
            else _budget_explanation(remaining, best_excluded, marginal_gain)
        ),
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
