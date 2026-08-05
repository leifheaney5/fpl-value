#!/usr/bin/env python
"""Walk-forward: which spreadsheet sort actually predicts the next 5 gameweeks?

See docs/RANKING_EVALUATION.md for the recorded results and the gate any new
ordering has to clear.

    python scripts/evaluate_ranking.py
"""
import sys, json
sys.path.insert(0, ".")
from sqlalchemy import create_engine, select, distinct
from sqlalchemy.orm import sessionmaker
from app.db.models import GameweekHistory
from app.models.ranking import walk_forward_ranking
from app.models.ranking_candidates import CANDIDATES
from app.models.evaluation import season_order

engine = create_engine("sqlite:///data/local.db")
S = sessionmaker(engine)

with S() as db:
    seasons = season_order(
        set(db.scalars(select(distinct(GameweekHistory.season))).all())
    )
    print("seasons:", ", ".join(seasons))

    report = walk_forward_ranking(db, seasons, CANDIDATES, horizon=5)

    print(f"\nhorizon: next {report['horizon']} gameweeks")
    print(f"seasons evaluated: {len(report['seasons_evaluated'])}")
    print(f"\n{'candidate':32} {'Spearman':>9} {'wins':>5} {'seasons':>8}")
    print("-" * 60)
    rows = sorted(
        report["candidates"].items(),
        key=lambda kv: (kv[1]["spearman"] is None, -(kv[1]["spearman"] or 0)),
    )
    for name, data in rows:
        sp = data["spearman"]
        print(f"{name:32} {sp if sp is None else round(sp, 4):>9} "
              f"{data['wins']:>5} {data['seasons']:>8}")

    # Per-position breakdown, pooled across seasons by cohort size. A sort is
    # applied inside a position at least as often as across the whole pool.
    positions = ("GK", "DEF", "MID", "FWD")
    print("\nper-position Spearman (pooled across seasons)")
    print(f"{'candidate':32} " + " ".join(f"{p:>8}" for p in positions))
    print("-" * 70)
    for name, data in rows:
        cells = []
        for position in positions:
            values = [
                season_data.get(position)
                for season_data in data.get("per_position", {}).values()
            ]
            values = [v for v in values if v is not None]
            cells.append(
                f"{sum(values)/len(values):8.3f}" if values else f"{'-':>8}"
            )
        print(f"{name:32} " + " ".join(cells))

    with open("docs/ranking-evaluation.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print("\nwritten to docs/ranking-evaluation.json")
