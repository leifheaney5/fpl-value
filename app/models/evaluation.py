"""Walk-forward evaluation.

Train on seasons up to N, evaluate on N+1, advance. The expanding window is what
makes the result honest: a model is never scored on a season it could have
learned from, and the fold structure is asserted in tests rather than assumed.

Results are reported separately for each information state. A model can be
strong in-season, where it sees form and minutes, and useless in preseason,
where it sees only last season -- and preseason is exactly the state the live
deployment is in today.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory
from app.models.baselines import BASELINES, Baseline
from app.models.features import InformationState, build_features
from app.models.metrics import summarise

logger = logging.getLogger(__name__)

INFORMATION_STATES = (InformationState.PRESEASON, InformationState.IN_SEASON)


def season_order(seasons: Iterable[str]) -> list[str]:
    """Sort season labels chronologically.

    ``2024/25`` sorts after ``2020/21`` lexically too, but relying on that is
    luck rather than design, so the starting year is parsed explicitly.
    """
    def key(season: str) -> tuple[int, str]:
        head = season.split("/")[0]
        try:
            return (int(head), season)
        except ValueError:
            return (0, season)

    return sorted(set(seasons), key=key)


def walk_forward_folds(
    seasons: Iterable[str], min_train_seasons: int = 1
) -> list[tuple[list[str], str]]:
    """Expanding-window folds: (train seasons, test season)."""
    ordered = season_order(seasons)
    folds: list[tuple[list[str], str]] = []
    for index in range(min_train_seasons, len(ordered)):
        folds.append((ordered[:index], ordered[index]))
    return folds


@dataclass
class FoldResult:
    train_seasons: list[str]
    test_season: str
    information_state: str
    n_examples: int
    scores: dict[str, dict[str, float | None]] = field(default_factory=dict)


@dataclass
class EvaluationReport:
    seasons: list[str]
    folds: list[FoldResult]
    by_model: dict[str, dict[str, float | None]]
    generated_at: str

    def as_dict(self) -> dict[str, Any]:
        return {
            "seasons": self.seasons,
            "generated_at": self.generated_at,
            "by_model": self.by_model,
            "folds": [asdict(fold) for fold in self.folds],
        }


def _target_from(row: Any) -> Any:
    """The fixture being predicted, described only by what is knowable before it."""
    class _Target:
        season = row.season
        gameweek = row.gameweek
        is_home = bool(row.is_home)
        # Fixture difficulty is not in the archive; 3 is the neutral value the
        # deployed heuristic also falls back to. Recorded as a limitation.
        difficulty = 3

    return _Target()


def _history_by_player(
    db: Session, seasons: Sequence[str]
) -> dict[int, list[GameweekHistory]]:
    rows = db.scalars(
        select(GameweekHistory)
        .where(GameweekHistory.season.in_(list(seasons)))
        .order_by(GameweekHistory.player_code, GameweekHistory.kickoff_time)
    ).all()
    grouped: dict[int, list[GameweekHistory]] = defaultdict(list)
    for row in rows:
        grouped[row.player_code].append(row)
    return grouped


def evaluate_fold(
    db: Session,
    train_seasons: Sequence[str],
    test_season: str,
    information_state: str,
    models: Sequence[Baseline] = BASELINES,
    limit: int | None = None,
) -> FoldResult:
    """Score every model on one held-out season."""
    history = _history_by_player(db, list(train_seasons) + [test_season])

    actual: list[float] = []
    predictions: dict[str, list[float]] = {model.name: [] for model in models}
    examples = 0

    for player_code, rows in history.items():
        test_rows = [
            row
            for row in rows
            if row.season == test_season and row.kickoff_time is not None
        ]
        for row in test_rows:
            vector = build_features(
                rows, _target_from(row), row.kickoff_time, information_state
            )
            actual.append(float(row.points))
            for model in models:
                predictions[model.name].append(float(model.predict(vector)))
            examples += 1
            if limit is not None and examples >= limit:
                break
        if limit is not None and examples >= limit:
            break

    return FoldResult(
        train_seasons=list(train_seasons),
        test_season=test_season,
        information_state=information_state,
        n_examples=examples,
        scores={
            name: summarise(actual, values) for name, values in predictions.items()
        },
    )


def _aggregate(folds: Sequence[FoldResult]) -> dict[str, dict[str, float | None]]:
    """Pool fold scores by model and information state, weighted by sample size."""
    totals: dict[str, dict[str, float]] = defaultdict(
        lambda: {"n": 0.0, "mae": 0.0, "rmse": 0.0, "spearman": 0.0, "spearman_n": 0.0}
    )
    for fold in folds:
        for name, scores in fold.scores.items():
            key = f"{name}::{fold.information_state}"
            n = float(scores.get("n") or 0)
            if n <= 0:
                continue
            bucket = totals[key]
            bucket["n"] += n
            for metric in ("mae", "rmse"):
                value = scores.get(metric)
                if value is not None:
                    bucket[metric] += value * n
            spearman = scores.get("spearman")
            if spearman is not None:
                bucket["spearman"] += spearman * n
                bucket["spearman_n"] += n

    result: dict[str, dict[str, float | None]] = {}
    for key, bucket in totals.items():
        n = bucket["n"]
        result[key] = {
            "n": int(n),
            "mae": bucket["mae"] / n if n else None,
            "rmse": bucket["rmse"] / n if n else None,
            "spearman": (
                bucket["spearman"] / bucket["spearman_n"]
                if bucket["spearman_n"]
                else None
            ),
        }
    return result


def walk_forward(
    db: Session,
    seasons: Sequence[str] | None = None,
    models: Sequence[Baseline] = BASELINES,
    min_train_seasons: int = 1,
    limit_per_fold: int | None = None,
) -> EvaluationReport:
    available = [
        season
        for (season,) in db.execute(select(GameweekHistory.season).distinct())
    ]
    target_seasons = season_order(seasons or available)
    folds: list[FoldResult] = []

    for train_seasons, test_season in walk_forward_folds(
        target_seasons, min_train_seasons
    ):
        for state in INFORMATION_STATES:
            logger.info(
                "evaluate fold train=%s test=%s state=%s",
                train_seasons, test_season, state,
            )
            folds.append(
                evaluate_fold(
                    db, train_seasons, test_season, state, models, limit_per_fold
                )
            )

    return EvaluationReport(
        seasons=target_seasons,
        folds=folds,
        by_model=_aggregate(folds),
        generated_at=datetime.now(timezone.utc).isoformat(),
    )
