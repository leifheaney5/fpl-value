#!/usr/bin/env python
"""Which ordering builds the squad that actually scores most?

Complements scripts/evaluate_ranking.py, which measures how well an ordering
ranks individual players. This measures the decision that ordering leads to:
under a budget, the metric that predicts individual points best is not
necessarily the one that assembles the best fifteen.

    python scripts/evaluate_squads.py

See docs/RANKING_EVALUATION.md for recorded results.
"""

import sys
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine, distinct, select  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.db.models import GameweekHistory  # noqa: E402
from app.models.evaluation import season_order  # noqa: E402
from app.models.features import InformationState, build_features  # noqa: E402
from app.models.ranking import _target_from  # noqa: E402
from app.models.ranking_candidates import CANDIDATES  # noqa: E402
from app.models.squad_evaluation import build_squad, squad_points  # noqa: E402

HORIZON = 5
# Gameweeks at which a squad is built. Sampled rather than every week: the
# builder runs once per candidate per gameweek and the cost is linear.
SAMPLE_GAMEWEEKS = (5, 8, 11, 14, 17, 20, 23, 26, 29, 32)

# The per-90 family is excluded from the squad comparison. It is already known
# to be the worst ordering by a wide margin (Spearman 0.027 unfloored, 0.29 at
# best), and the floored variants decline to score most of the pool, so they
# frequently cannot fill a legal squad. Leaving them in forces the like-for-like
# comparison down to the handful of gameweeks where they happened to manage it,
# which is a far worse loss of evidence than dropping candidates already
# settled by scripts/evaluate_ranking.py.
EXCLUDED_PREFIXES = ("points_per_90",)

# The archive labels goalkeepers "GK"; the squad rules use "GKP". Verified
# against 2024/25 GW10, where the position values are GK/DEF/MID/FWD. Getting
# this wrong produced zero squads rather than a wrong answer, because every
# build failed the two-goalkeeper requirement silently.
POSITION_ALIASES = {"GK": "GKP", "GKP": "GKP", "DEF": "DEF", "MID": "MID", "FWD": "FWD"}

# Archive prices are in millions (4.8), not tenths (48) as the FPL API reports
# them. The squad builder works in tenths so the budget is integer arithmetic.
PRICE_TO_TENTHS = 10


def main() -> int:
    engine = create_engine("sqlite:///data/local.db")
    Session = sessionmaker(engine)
    # (season, gameweek) -> candidate -> squad points. Keyed per build so the
    # comparison can be restricted to builds every candidate completed:
    # averaging each candidate over whichever gameweeks it happened to manage
    # rewards the ones that quietly failed on the hard ones.
    per_build: dict[tuple[str, int], dict[str, float]] = defaultdict(dict)
    failures: dict[str, int] = defaultdict(int)

    with Session() as db:
        seasons = season_order(
            set(db.scalars(select(distinct(GameweekHistory.season))).all())
        )
        print("seasons:", ", ".join(seasons))

        for index, season in enumerate(seasons):
            if index == 0:
                continue
            print(f"  {season} ...", flush=True)
            rows = db.scalars(
                select(GameweekHistory)
                .where(GameweekHistory.season.in_(seasons[: index + 1]))
                .order_by(
                    GameweekHistory.player_code, GameweekHistory.kickoff_time
                )
            ).all()
            by_player: dict[int, list] = defaultdict(list)
            for row in rows:
                by_player[row.player_code].append(row)

            points = {
                (row.player_code, row.gameweek): float(row.points or 0.0)
                for row in rows
                if row.season == season
            }

            for gameweek in SAMPLE_GAMEWEEKS:
                pool: dict[str, list[dict]] = defaultdict(list)
                for code, history in by_player.items():
                    target = next(
                        (
                            r
                            for r in history
                            if r.season == season
                            and r.gameweek == gameweek
                            and r.kickoff_time is not None
                        ),
                        None,
                    )
                    if target is None or not target.position:
                        continue
                    if not target.price:
                        continue
                    position = POSITION_ALIASES.get(target.position)
                    if position is None:
                        continue
                    vector = build_features(
                        history,
                        _target_from(target),
                        target.kickoff_time,
                        InformationState.IN_SEASON,
                    )
                    for candidate in CANDIDATES:
                        if candidate.name.startswith(EXCLUDED_PREFIXES):
                            continue
                        score = candidate.score(vector)
                        if score is None:
                            continue
                        pool[candidate.name].append(
                            {
                                "player_code": code,
                                "position": position,
                                # The archive has no team_id -- team_name is the
                                # only club identifier it carries. Verified
                                # against GameweekHistory on 2026-08-04.
                                "club": target.team_name or "",
                                "price_tenths": round(target.price * PRICE_TO_TENTHS),
                                "score": float(score),
                            }
                        )

                for name, scored in pool.items():
                    squad = build_squad(scored)
                    if squad is None:
                        failures[name] += 1
                        continue
                    per_build[(season, gameweek)][name] = squad_points(
                        squad, points, gameweek, HORIZON
                    )

    all_names = {name for build in per_build.values() for name in build}
    common = [
        build for build in per_build.values() if set(build) == all_names
    ]
    print(
        f"\n{len(per_build)} build points; {len(common)} completed by all "
        f"{len(all_names)} candidates"
    )

    if not common:
        print(
            "\nNo build point was completed by every candidate, so no fair\n"
            "comparison is possible. The floored per-90 variants decline to\n"
            "score most players and cannot fill a squad; drop them and re-run."
        )
        return 1

    means = {
        name: sum(build[name] for build in common) / len(common)
        for name in all_names
    }
    wins: dict[str, int] = defaultdict(int)
    for build in common:
        wins[max(build, key=build.get)] += 1

    print(f"\nsquad points over the next {HORIZON} gameweeks")
    print(f"{'candidate':32} {'mean pts':>9} {'wins':>6} {'no squad':>9}")
    print("-" * 60)
    for name in sorted(all_names, key=lambda n: -means[n]):
        print(
            f"{name:32} {means[name]:9.1f} {wins.get(name, 0):>6} "
            f"{failures.get(name, 0):>9}"
        )

    # Per-season aggregation. The gate is a majority of seasons, not a mean:
    # a candidate that wins narrowly most weeks and loses hugely once has the
    # same mean as one that is reliably second, and they are not the same bet.
    by_season: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for (season, _gw), build in per_build.items():
        if set(build) != all_names:
            continue
        for name, value in build.items():
            by_season[season][name].append(value)

    season_wins: dict[str, int] = defaultdict(int)
    for season, scores in by_season.items():
        season_means = {
            name: sum(values) / len(values) for name, values in scores.items()
        }
        season_wins[max(season_means, key=season_means.get)] += 1

    print(f"\nseasons won (of {len(by_season)})")
    for name in sorted(all_names, key=lambda n: -season_wins.get(n, 0)):
        if season_wins.get(name):
            print(f"  {name:32} {season_wins[name]}")

    best = max(means, key=means.get)
    spread = means[best] - min(means.values())
    print(
        f"\nbest: {best} at {means[best]:.1f}; spread across candidates "
        f"{spread:.1f} points"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
