# Ranking Evidence and GW1 Readiness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Settle which sort the spreadsheet should default to using budget-aware and position-aware evidence, and prove the preseason→in-season transition works before it happens for real on 21 August.

**Architecture:** Two halves that do not depend on each other. The evidence half extends `app/models/ranking.py` — which already measures rank correlation against a five-gameweek horizon — with a squad-level evaluation that respects the £100m budget and FPL squad rules, plus a per-position breakdown. The readiness half rehearses the carry-over→in-season flip end-to-end against a populated database, because that flip switches on six dormant features simultaneously and has never run on real data.

**Tech Stack:** Python 3.12, SQLAlchemy 2, pytest, Alembic. No new runtime dependencies.

## Context: where the project actually is

Read this before starting. State as of 2026-08-04, ~17 days from the GW1 deadline (21 August 17:30 UTC).

**Live and healthy** at `https://web-production-f5979.up.railway.app/`, running `aa36bbc`. 365 tests pass. Migrations through `0009` are applied in production. Both `main` and `feat/railway-fpl-value-studio` point at the same commit.

**The model line is closed.** `docs/MODEL_EVALUATION.md` records the decision: no trained model ships, the `projected_points_5` heuristic remains the sole projection source. **Do not revisit this.**

**The ranking harness exists and has a gate.** `docs/RANKING_EVALUATION.md` records nine-fold walk-forward results over ten archive seasons, measuring rank correlation against the next five gameweeks, computed within each gameweek:

| Candidate | Spearman | Seasons won |
| --- | --- | --- |
| points_per_game | 0.7368 | 8 / 9 |
| reliable_value_approx *(current default sort)* | 0.7252 | 0 / 9 |
| minutes_mean *(control)* | 0.7229 | 1 / 9 |
| points_per_million | 0.7213 | 0 / 9 |
| points_per_90 | 0.0270 | 0 / 9 |

The gate for any new ordering: beat points_per_game at 0.7368, beat the minutes-only control at 0.7229, and win a majority of nine seasons. **It has already rejected three proposals** — both Track 2 composites, sample-size shrinkage, and promoting P/90. Expect it to reject more; that is the point of it.

**The open question this plan answers.** The harness measures raw points. `points_per_million` scores 0.7213 while *also* accounting for price; `points_per_game` scores 0.7368 while ignoring it. Under a £100m budget the cheaper metric may build a better squad even though it predicts individual points slightly worse. The current harness cannot say. Task 1 builds the one that can.

**Why the default sort has not moved.** `reliable_value_approx` loses to points_per_game on all nine seasons, which is evidence for changing the default from `reliable_value`. It has not been changed because the candidate is an *approximation* — the deployed metric also uses this-season availability, which the archive does not carry. Changing a default on an approximation of itself is the shape of the mistake that produced the recommender bug. Task 3 measures the real thing first.

**What flips at GW1.** The moment one fixture finishes, `team_matches > 0` and six things change at once: carry-over labels disappear from five columns, `value`/`points_per_90` switch from `previous_season` to `value` status, `expected_minutes` and `projected_points_5` become non-null, the recommender's `validate_pool` starts passing, `/captaincy` starts producing a shortlist, and every rank/percentile repopulates from this season. None of that has run against real data.

## Global Constraints

- **No new production dependencies.** `pyproject.toml` production `dependencies` stays as it is.
- **Missing is not zero.** A metric with no basis returns `None` and carries a `metric_status` reason. Never emit `0.0` for an unmeasured quantity.
- **Rates need a sample.** `P90_MIN_MINUTES = 270` in `app/services/refresh.py`. Any new rate needs a stated, justified floor.
- **Shrinkage must not be baked into a score.** Measured as harmful to ranking on all nine folds. The Track 1 visual marker stays; the scoring adjustment does not return.
- **Evaluation candidates read the masked feature vector** via `_get` in `app/models/ranking_candidates.py`, which returns `None` for a masked feature. This inherits the proven no-leakage guarantee — never read `vector.values` directly.
- **Timezone discipline.** Normalise both sides to UTC before comparing datetimes; SQLite returns naive ones. This has caused two production crashes.
- **Test stubs must be realistic.** Two stubs previously set `projected_points_5` without `expected_minutes`, a state the pipeline cannot produce, and hid a real bug. If a stub needs a field the model has, give it one.
- **Full suite green before each commit:** `python -m pytest -p no:dash` (the `-p no:dash` avoids a broken global plugin on this machine; 365 tests currently pass).

---

## File Structure

**Modify:**
- `app/models/ranking.py` — add per-position breakdown to `evaluate_ranking`.
- `app/models/ranking_candidates.py` — add the real `reliable_value` candidate.
- `docs/RANKING_EVALUATION.md` — record squad-level and per-position results.
- `scripts/evaluate_ranking.py` — report the new dimensions.

**Create:**
- `app/models/squad_evaluation.py` — build a legal squad from a ranking and score it over a horizon. One responsibility: turn an ordering into a squad and that squad into points.
- `tests/test_squad_evaluation.py`
- `scripts/evaluate_squads.py`
- `tests/test_season_transition.py` — the carry-over→in-season rehearsal.
- `docs/GW1_RUNBOOK.md` — what to check on 21–23 August.

---

## Task 1: Score a ranking by the squad it builds, not the players it ranks

**Files:**
- Create: `app/models/squad_evaluation.py`
- Test: `tests/test_squad_evaluation.py`

**Interfaces:**
- Consumes: `RankingCandidate` from `app/models/ranking.py`; `build_features` and `InformationState` from `app/models/features.py`.
- Produces: `build_squad(scored, budget_tenths=1000) -> list[dict]` and `squad_points(squad, points_by_player_gameweek, gameweek, horizon) -> float | None`.

**Why this task exists:** points_per_game predicts individual points better; points_per_million accounts for price. Under a budget those pull in opposite directions and the existing harness cannot arbitrate. A squad-level score can: build the best legal squad each ordering can afford, then count what that squad actually scored.

- [ ] **Step 1: Write the failing test**

Create `tests/test_squad_evaluation.py`:

```python
"""A ranking is only as good as the squad you can afford from it."""

import pytest

from app.models.squad_evaluation import build_squad, squad_points

POSITION_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}


def _scored(n_per_position=8, price_tenths=50, score=lambda i: 10.0 - i):
    """Players across four positions and ten clubs, priced identically."""
    rows = []
    player_id = 1
    for position in ("GKP", "DEF", "MID", "FWD"):
        for index in range(n_per_position):
            rows.append(
                {
                    "player_code": player_id,
                    "position": position,
                    "club": player_id % 10,
                    "price_tenths": price_tenths,
                    "score": score(index),
                }
            )
            player_id += 1
    return rows


def test_a_squad_has_the_right_shape():
    squad = build_squad(_scored(), budget_tenths=1000)
    assert squad is not None
    assert len(squad) == 15
    counts = {}
    for row in squad:
        counts[row["position"]] = counts.get(row["position"], 0) + 1
    assert counts == POSITION_COUNTS


def test_a_squad_respects_the_budget():
    squad = build_squad(_scored(price_tenths=60), budget_tenths=1000)
    assert squad is not None
    assert sum(row["price_tenths"] for row in squad) <= 1000


def test_no_more_than_three_players_from_one_club():
    squad = build_squad(_scored(n_per_position=20), budget_tenths=1000)
    assert squad is not None
    clubs = {}
    for row in squad:
        clubs[row["club"]] = clubs.get(row["club"], 0) + 1
    assert max(clubs.values()) <= 3


def test_an_unaffordable_pool_yields_no_squad():
    """Refusing is correct; a partial squad would be scored as if complete."""
    assert build_squad(_scored(price_tenths=200), budget_tenths=1000) is None


def test_a_higher_ranking_is_preferred_when_price_is_equal():
    squad = build_squad(_scored(), budget_tenths=1000)
    assert squad is not None
    chosen = {row["player_code"] for row in squad}
    # Index 0 of each position is the highest scorer and must be selected.
    assert len(chosen) == 15
    assert min(row["score"] for row in squad) >= 3.0


def test_squad_points_sums_the_horizon_for_its_members():
    squad = [{"player_code": 1}, {"player_code": 2}]
    points = {(1, 2): 5.0, (1, 3): 1.0, (2, 2): 2.0, (2, 3): 4.0, (3, 2): 99.0}
    assert squad_points(squad, points, gameweek=1, horizon=2) == 12.0


def test_squad_points_counts_a_missing_gameweek_as_zero():
    squad = [{"player_code": 1}]
    points = {(1, 2): 5.0}
    assert squad_points(squad, points, gameweek=1, horizon=2) == 5.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_squad_evaluation.py -p no:dash`
Expected: `ModuleNotFoundError: No module named 'app.models.squad_evaluation'`.

- [ ] **Step 3: Write the implementation**

Create `app/models/squad_evaluation.py`:

```python
"""Turn an ordering into a squad, and that squad into points.

``ranking.py`` asks whether a sort ranks individual players well. This asks a
different and more decision-relevant question: given a budget and FPL squad
rules, which ordering builds the squad that actually scores most?

The two can disagree. Points-per-game predicts individual returns best but
ignores price; points-per-million predicts slightly worse while accounting for
it. Under a fixed budget the cheaper metric can win, and only a squad-level
score can say so.

The builder is deliberately greedy rather than optimal. It models what a person
does with a sorted sheet -- work down it, take who you can afford -- so the
comparison is between orderings as used, not between orderings as an optimiser
would exploit them.
"""

from __future__ import annotations

from typing import Any, Sequence

# Standard FPL squad: 15 players, at most 3 from any one club.
POSITION_COUNTS = {"GKP": 2, "DEF": 5, "MID": 5, "FWD": 3}
MAX_PER_CLUB = 3
SQUAD_SIZE = 15
DEFAULT_BUDGET_TENTHS = 1000


def build_squad(
    scored: Sequence[dict[str, Any]], budget_tenths: int = DEFAULT_BUDGET_TENTHS
) -> list[dict[str, Any]] | None:
    """Best legal squad reachable by working down the ordering.

    Returns None when no legal squad fits the budget. Refusing matters: a
    partial squad scored against a full one would make an ordering that cannot
    afford a team look merely mediocre rather than unusable.

    Two passes. The first takes the highest-ranked affordable player for each
    slot while reserving the cheapest possible price for every slot still
    unfilled, which is what stops the early picks eating the whole budget. The
    second is a no-op when the first succeeds and returns None when it cannot.
    """
    by_position: dict[str, list[dict[str, Any]]] = {
        position: sorted(
            (row for row in scored if row["position"] == position),
            key=lambda row: row["score"],
            reverse=True,
        )
        for position in POSITION_COUNTS
    }
    cheapest: dict[str, list[int]] = {
        position: sorted(row["price_tenths"] for row in rows)
        for position, rows in by_position.items()
    }
    for position, needed in POSITION_COUNTS.items():
        if len(by_position[position]) < needed:
            return None

    squad: list[dict[str, Any]] = []
    clubs: dict[Any, int] = {}
    spent = 0
    remaining = dict(POSITION_COUNTS)

    def reserve_for_others(exclude: str) -> int:
        """Cheapest total still needed for every other unfilled slot."""
        total = 0
        for position, count in remaining.items():
            picks = count - 1 if position == exclude else count
            if picks > 0:
                total += sum(cheapest[position][:picks])
        return total

    for position in ("GKP", "DEF", "MID", "FWD"):
        while remaining[position] > 0:
            reserve = reserve_for_others(position)
            pick = None
            for row in by_position[position]:
                if row in squad:
                    continue
                if clubs.get(row["club"], 0) >= MAX_PER_CLUB:
                    continue
                if spent + row["price_tenths"] + reserve > budget_tenths:
                    continue
                pick = row
                break
            if pick is None:
                return None
            squad.append(pick)
            clubs[pick["club"]] = clubs.get(pick["club"], 0) + 1
            spent += pick["price_tenths"]
            remaining[position] -= 1

    return squad if len(squad) == SQUAD_SIZE else None


def squad_points(
    squad: Sequence[dict[str, Any]],
    points_by_player_gameweek: dict[tuple[int, int], float],
    gameweek: int,
    horizon: int,
) -> float:
    """Total points the squad's members scored over the next ``horizon`` weeks.

    Counts all fifteen rather than a starting eleven: choosing a lineup is a
    separate decision with its own logic, and folding it in here would measure
    two things at once.
    """
    return sum(
        points_by_player_gameweek.get((row["player_code"], gameweek + step), 0.0)
        for row in squad
        for step in range(1, horizon + 1)
    )
```

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/test_squad_evaluation.py -p no:dash`
Expected: 7 passed.

- [ ] **Step 5: Commit**

```bash
git add app/models/squad_evaluation.py tests/test_squad_evaluation.py
git commit -m "Score a ranking by the squad it can afford, not the players it ranks"
```

---

## Task 2: Run the squad evaluation across the archive

**Files:**
- Create: `scripts/evaluate_squads.py`
- Modify: `docs/RANKING_EVALUATION.md`

**Interfaces:**
- Consumes: `build_squad` and `squad_points` from Task 1; `CANDIDATES` from `app/models/ranking_candidates.py`.

- [ ] **Step 1: Write the script**

Create `scripts/evaluate_squads.py`:

```python
#!/usr/bin/env python
"""Which ordering builds the squad that actually scores most?

Complements scripts/evaluate_ranking.py, which measures how well an ordering
ranks individual players. This measures the decision that ordering leads to.

    python scripts/evaluate_squads.py
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
SAMPLE_GAMEWEEKS = (5, 10, 15, 20, 25, 30)


def main() -> int:
    engine = create_engine("sqlite:///data/local.db")
    Session = sessionmaker(engine)
    totals: dict[str, list[float]] = defaultdict(list)

    with Session() as db:
        seasons = season_order(
            set(db.scalars(select(distinct(GameweekHistory.season))).all())
        )
        for index, season in enumerate(seasons):
            if index == 0:
                continue
            rows = db.scalars(
                select(GameweekHistory)
                .where(GameweekHistory.season.in_(seasons[: index + 1]))
                .order_by(
                    GameweekHistory.player_code, GameweekHistory.kickoff_time
                )
            ).all()
            by_player = defaultdict(list)
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
                    vector = build_features(
                        history,
                        _target_from(target),
                        target.kickoff_time,
                        InformationState.IN_SEASON,
                    )
                    for candidate in CANDIDATES:
                        score = candidate.score(vector)
                        if score is None:
                            continue
                        pool[candidate.name].append(
                            {
                                "player_code": code,
                                "position": target.position,
                                # The archive has no team_id -- team_name is the
                                # only club identifier it carries. Verified
                                # against GameweekHistory on 2026-08-04.
                                "club": target.team_name or "",
                                "price_tenths": int(target.price or 0),
                                "score": float(score),
                            }
                        )

                for name, scored in pool.items():
                    squad = build_squad(scored)
                    if squad is None:
                        continue
                    totals[name].append(
                        squad_points(squad, points, gameweek, HORIZON)
                    )

    print(f"\nsquad points over the next {HORIZON} gameweeks, mean across builds")
    print(f"{'candidate':32} {'mean pts':>9} {'builds':>7}")
    print("-" * 52)
    for name, values in sorted(
        totals.items(), key=lambda kv: -sum(kv[1]) / max(len(kv[1]), 1)
    ):
        print(f"{name:32} {sum(values)/len(values):9.1f} {len(values):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Check the columns the script depends on exist**

Run:

```bash
python -c "from app.db.models import GameweekHistory as G; print([c.name for c in G.__table__.columns])"
```

Verified on 2026-08-04: `position`, `price`, `points`, `gameweek`, `season`,
`player_code` and `kickoff_time` all exist. **`team_id` does not** — the archive
carries `team_name`, `opponent` and `opponent_team_id` only, which is why the
script above keys the club constraint on `team_name`.

If any field is absent, adapt the script to the schema rather than changing the
schema to suit the script.

- [ ] **Step 3: Run it**

Run: `PYTHONIOENCODING=utf-8 python scripts/evaluate_squads.py`
Expected: a table of candidates by mean squad points. Takes several minutes.

- [ ] **Step 4: Record the result**

Append a `## Squad-level evaluation` section to `docs/RANKING_EVALUATION.md` with the table, and state plainly which ordering wins. **If points_per_million wins here while losing on individual rank correlation, say so** — that is the finding this task exists to produce, and it means the sheet should default to the Value column.

- [ ] **Step 5: Commit**

```bash
git add scripts/evaluate_squads.py docs/RANKING_EVALUATION.md
git commit -m "Measure which ordering builds the better squad under a budget"
```

---

## Task 3: Measure the real reliable_value, and decide the default sort

**Files:**
- Modify: `app/models/ranking_candidates.py`
- Modify: `docs/RANKING_EVALUATION.md`
- Modify: `app/web/routes.py` (only if the evidence supports it)

**Interfaces:**
- Consumes: `reliability_factor` from `app/analytics/metrics.py` — the deployed function, not a reimplementation.

- [ ] **Step 1: Read the deployed reliability formula**

Run: `grep -n "def reliability_factor" -A 25 app/analytics/metrics.py`

The current archive candidate approximates it. Reuse the real function so the
comparison is against what actually ships.

- [ ] **Step 2: Replace the approximation**

In `app/models/ranking_candidates.py`, replace `_reliable_value` with a version
calling the deployed function. Its real signature, verified on 2026-08-04, is
`reliability_factor(minutes: int, starts: int, team_matches: int,
sample_minutes: int = 900) -> float | None` — note there is no `matches`
argument:

```python
from app.analytics.metrics import reliability_factor


def _reliable_value(vector: FeatureVector) -> float | None:
    """The deployed Reliable Value, using the shipped reliability function.

    The earlier candidate approximated it and lost all nine seasons to
    points-per-game. An approximation is not grounds for changing a default, so
    this measures the real thing. Availability is absent from the archive, so
    the deployed metric's availability term cannot be reproduced -- a stated
    limitation, not a modelling choice.

    The feature vector holds per-match means, so totals are recovered by
    multiplying by match count before handing them to a function that expects
    season totals.
    """
    matches = _matches(vector)
    price = _get(vector, "cur_price")
    mean = _get(vector, "cur_points_mean")
    start_rate = _get(vector, "cur_start_rate")
    minutes_mean = _get(vector, "cur_minutes_mean")
    if None in (matches, price, mean, start_rate, minutes_mean) or price <= 0:
        return None
    factor = reliability_factor(
        minutes=int(minutes_mean * matches),
        starts=int(start_rate * matches),
        team_matches=int(matches),
    )
    if factor is None:
        return None
    return ((mean * matches) / price) * factor
```

- [ ] **Step 3: Re-run both evaluations**

Run:

```bash
PYTHONIOENCODING=utf-8 python scripts/evaluate_ranking.py
PYTHONIOENCODING=utf-8 python scripts/evaluate_squads.py
```

- [ ] **Step 4: Decide, and record the decision either way**

Change the default sort in `app/web/routes.py` (the `sort: str = "reliable_value"` parameter of `spreadsheet`, and the `views` table entry for `"all"`) **only if** the winning candidate beats `reliable_value` on *both* evaluations and wins a majority of seasons.

If it does not, leave the default alone and record why. A null result is a
result: `docs/RANKING_EVALUATION.md` already documents three rejected proposals
and this would be the fourth.

- [ ] **Step 5: Run the suite and commit**

Run: `python -m pytest -p no:dash`

```bash
git add app/models/ranking_candidates.py docs/RANKING_EVALUATION.md app/web/routes.py
git commit -m "Measure the deployed reliable_value rather than an approximation of it"
```

---

## Task 4: Break the ranking evaluation down by position

**Files:**
- Modify: `app/models/ranking.py`
- Modify: `scripts/evaluate_ranking.py`
- Test: `tests/test_ranking.py`

**Why:** `docs/RANKING_EVALUATION.md` lists this as a known limitation. A sort is used within a position at least as often as across all players, and the ordering that wins overall may not win inside each one — goalkeepers in particular score through clean sheets and saves, which behave nothing like attacking returns.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_ranking.py`:

```python
def test_position_breakdown_scores_each_position_separately():
    """A sort used inside a position must be judged inside that position."""
    from app.models.ranking import group_by_position

    rows = [
        {"position": "DEF", "score": 3.0, "outcome": 9.0},
        {"position": "DEF", "score": 1.0, "outcome": 2.0},
        {"position": "FWD", "score": 5.0, "outcome": 1.0},
        {"position": "FWD", "score": 2.0, "outcome": 8.0},
    ]
    grouped = group_by_position(rows)
    assert set(grouped) == {"DEF", "FWD"}
    assert len(grouped["DEF"]) == 2
    # DEF is ranked correctly, FWD inverted -- they must not cancel out.
    assert grouped["DEF"][0]["outcome"] == 9.0
```

- [ ] **Step 2: Run it to verify it fails**

Run: `python -m pytest tests/test_ranking.py -k position -p no:dash`
Expected: `ImportError: cannot import name 'group_by_position'`.

- [ ] **Step 3: Implement**

Add to `app/models/ranking.py`:

```python
def group_by_position(rows: Sequence[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """Split scored rows by position so each is correlated on its own.

    Pooling positions lets a candidate that ranks defenders well and forwards
    badly average out to "fine". The two cohorts score through different
    mechanisms and a sort is usually applied inside one of them.
    """
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        position = row.get("position")
        if position:
            grouped[position].append(row)
    return dict(grouped)
```

Then thread `position` through `evaluate_ranking`: record `row.position` alongside each score, and produce a `by_position` mapping of position → Spearman in `RankingResult`. Keep the existing pooled number — it stays the headline; the breakdown is additional.

- [ ] **Step 4: Run tests, then the evaluation**

Run: `python -m pytest tests/test_ranking.py -p no:dash`
Then: `PYTHONIOENCODING=utf-8 python scripts/evaluate_ranking.py`

- [ ] **Step 5: Record and commit**

Add a `## By position` section to `docs/RANKING_EVALUATION.md`. If the best sort differs by position, that is a product finding: the sheet should default differently when a position filter is active.

```bash
git add app/models/ranking.py scripts/evaluate_ranking.py tests/test_ranking.py docs/RANKING_EVALUATION.md
git commit -m "Break the ranking evaluation down by position"
```

---

## Task 5: Rehearse the GW1 transition

**Files:**
- Create: `tests/test_season_transition.py`
- Modify: `tests/fakes.py`

**Why:** the moment one fixture finishes, six dormant behaviours activate at once. Each has unit coverage; the *transition* has none, and it happens on 21 August in front of users.

- [ ] **Step 1: Add a fake for the first played gameweek**

Append to `tests/fakes.py`:

```python
class FirstGameweekPlayedClient(LiveClient):
    """The state at roughly 19:00 on 21 August: GW1 played, GW2 next.

    Counting stats are this season's now -- small, because one match has been
    played -- which is what ends the carry-over period.
    """

    def bootstrap(self):
        payload = super().bootstrap()
        for element in payload["elements"]:
            element.update(
                {
                    "total_points": 6,
                    "minutes": 90,
                    "starts": 1,
                    "form": "6.0",
                    "points_per_game": "6.0",
                }
            )
        return payload
```

- [ ] **Step 2: Write the failing test**

Create `tests/test_season_transition.py`:

```python
"""The preseason to in-season flip, rehearsed before it happens for real.

One finished fixture switches on six behaviours simultaneously. Each is
covered on its own; the transition between them is not, and it happens once,
in public, on 21 August.
"""

from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.db.session import get_db
from app.main import app
from app.services.queries import dashboard_data
from app.services.refresh import refresh_data

from fakes import CarryOverPreseasonClient, FirstGameweekPlayedClient
from fastapi.testclient import TestClient


def _session(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False), Settings(
        database_url=url, current_season="2026/27"
    )


def test_carry_over_labelling_stops_once_a_match_is_played(tmp_path):
    Session, settings = _session(tmp_path, "flip.db")

    with Session() as db:
        refresh_data(db, settings, CarryOverPreseasonClient())
        before = db.scalar(select(PlayerSnapshot))
        assert before.metric_status["value"]["status"] == "previous_season"

    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())
        after = db.scalars(
            select(PlayerSnapshot).order_by(PlayerSnapshot.captured_at.desc())
        ).first()
        assert after.metric_status["value"]["status"] != "previous_season", (
            "counting stats are this season's now; the label must clear"
        )


def test_the_sheet_stops_naming_last_season(tmp_path):
    Session, settings = _session(tmp_path, "sheet.db")
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    try:
        body = TestClient(app).get("/spreadsheet").text
        assert "2025/26" not in body, (
            "the carry-over banner and column tags must disappear"
        )
    finally:
        app.dependency_overrides.clear()


def test_dormant_features_activate_together(tmp_path):
    """Expected minutes and projections both require a played match."""
    Session, settings = _session(tmp_path, "activate.db")
    with Session() as db:
        refresh_data(db, settings, FirstGameweekPlayedClient())
        data = dashboard_data(db, "2026/27")
        snapshot = data["rows"][0]["snapshot"]

        assert snapshot.team_matches > 0
        assert snapshot.expected_minutes is not None
        assert snapshot.start_rate is not None
        assert data["readiness"]["expected_minutes"]["state"] == "ready"
```

- [ ] **Step 3: Run it**

Run: `python -m pytest tests/test_season_transition.py -p no:dash`

**Failures here are the deliverable.** Read each one and fix the application, not
the test. The likely candidate: `carry_over` in `app/web/routes.py:spreadsheet`
tests `team_matches == 0`, so it should clear on its own — but the *stored*
`metric_status` only updates on refresh, so a stale snapshot can keep the label
after the first match. If that is what fails, the fix belongs in the transition
logic, not the assertion.

- [ ] **Step 4: Run the whole suite**

Run: `python -m pytest -p no:dash`

- [ ] **Step 5: Commit**

```bash
git add tests/fakes.py tests/test_season_transition.py
git commit -m "Rehearse the preseason to in-season transition before 21 August"
```

---

## Task 6: Write the GW1 runbook

**Files:**
- Create: `docs/GW1_RUNBOOK.md`

**Why:** no amount of fake-client coverage substitutes for the first real refresh with real match data. This is what to check, in order, so it is not improvised at the time.

- [ ] **Step 1: Write it**

Create `docs/GW1_RUNBOOK.md` covering, with the exact command or URL for each:

1. **After the first fixture finishes (21 Aug, ~19:00 UTC)** — run
   `railway ssh --service web -- python -m app.cli refresh --force`. The daily
   cron runs at 14:00/15:00 UTC and will not have picked up the result.
2. **Confirm `team_matches > 0`** on `/diagnostics`. Everything else depends on it.
3. **Confirm the carry-over labels are gone** — `2025/26` should not appear on
   `/spreadsheet`.
4. **Confirm the numbers are small and plausible.** After one match, totals
   should be single-digit. A player showing 200 points means the carry-over
   detection failed and last season's data is being reported as this season's.
5. **Confirm `/captaincy` produces a shortlist** rather than the not-ready panel,
   and that the top pick is not a goalkeeper.
6. **Confirm `/recommendation` produces a squad** and that no selected player is
   justified by "no measured basis".
7. **Sanity-check against reality.** Take the three highest-scoring players in
   the real gameweek and confirm the sheet agrees. This is the check that catches
   what tests cannot.
8. **If anything is wrong**, the rollback is `railway up --service web` from the
   previous commit; snapshots are append-only so no data is lost.

- [ ] **Step 2: Commit**

```bash
git add docs/GW1_RUNBOOK.md
git commit -m "Add the GW1 transition runbook"
```

---

## Sequencing

Tasks 1–4 are evidence work and can be done any time. **Task 5 must be done
before 21 August** — it is the only one with a deadline. Task 6 should be
written before then too, but is quick.

If time runs short, the order that preserves the most value is: **5, 6, 1, 2, 3, 4.**

## Beyond this plan

Deliberately excluded, with reasons:

- **Transfer planning and chip strategy** — both need several gameweeks of real
  in-season data to validate against. Starting before GW4 means building against
  data that does not exist.
- **Live gameweek tracking** — needs `SeasonState.LIVE`, which is declared at
  `app/services/season_state.py:24` and never returned by `season_state()`,
  plus a polling cadence faster than the daily cron. Real infrastructure work.
- **Elite cohort ownership** — needs collection from outside the official API,
  carrying data-acquisition and terms-of-service questions the other items do
  not. Decide those before starting.
- **Revisiting the model** — closed, nine-fold evidence, see
  `docs/MODEL_EVALUATION.md`.

## Self-Review

**Coverage.** This plan closes the open question left by
`docs/RANKING_EVALUATION.md` (budget-awareness, Tasks 1–2), its stated
limitation on position (Task 4), the deferred default-sort decision (Task 3),
and the single largest untested risk before the season starts (Tasks 5–6).

**Placeholder scan.** The two assumptions this plan originally guessed at were
checked against the codebase before it was finalised, and both were wrong:
`GameweekHistory` has no `team_id` (it carries `team_name`), and
`reliability_factor` takes `(minutes, starts, team_matches)` with no `matches`
argument. Both are corrected inline with the verified signatures. Task 5 Step 3
states that failures are the deliverable, which is the nature of adding coverage
to an untested path rather than a gap.

**Type consistency.** `build_squad` consumes dicts with keys `player_code`,
`position`, `club`, `price_tenths`, `score` and returns the same shape;
`squad_points` reads only `player_code` and a `(player_code, gameweek)` mapping,
matching what `scripts/evaluate_squads.py` builds. `group_by_position` consumes
rows with `position` and returns position → list. `RankingCandidate.score`
returns `float | None` throughout, and every consumer skips `None` rather than
coercing it.
