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

    with open("docs/ranking-evaluation.json", "w", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, default=str)
    print("\nwritten to docs/ranking-evaluation.json")
