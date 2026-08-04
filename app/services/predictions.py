"""Produce and store predictions from a served artefact.

The rule that governs this module is the same one that governs every other
number in the application: an absent prediction is reported as absent, with a
reason, and never as zero. A missing model is a readiness state, not a value of
0.0 points.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Sequence

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory, Player, Prediction
from app.models.features import (
    FEATURE_NAMES,
    VERSION as FEATURE_VERSION,
    InformationState,
    build_features,
)
from app.services.season_state import Readiness

logger = logging.getLogger(__name__)


class _Target:
    __slots__ = ("season", "gameweek", "is_home", "difficulty")

    def __init__(self, season: str, gameweek: int, is_home: bool) -> None:
        self.season = season
        self.gameweek = gameweek
        self.is_home = is_home
        # The archive carries no fixture difficulty rating. See limitation 1 in
        # docs/MODEL_EVALUATION.md.
        self.difficulty = 3


def ranked_predictions(
    db: Session,
    season: str,
    gameweek: int,
    limit: int | None = None,
) -> list[Prediction]:
    """Stored predictions in the order the interface should present them.

    Ordered by ceiling, then by expected points. The ceiling is what ranks
    players -- the mean shrinks toward the conditional centre and compresses
    ordering -- but FPL points are discrete, so ceilings cluster on a few values
    and leave the top of the table heavily tied. Expected points varies more
    finely and breaks those ties sensibly.
    """
    query = (
        select(Prediction)
        .where(
            Prediction.season == season,
            Prediction.gameweek == gameweek,
            Prediction.horizon == "next",
        )
        .order_by(
            Prediction.ceiling.desc().nulls_last(),
            Prediction.expected_points.desc().nulls_last(),
        )
    )
    if limit is not None:
        query = query.limit(limit)
    return list(db.scalars(query).all())


def _confidence(start_probability: float, spread: float) -> str:
    """A plain-language reading of how certain a prediction is.

    Driven by the two things that actually make a projection uncertain: whether
    the player will be on the pitch at all, and how wide the outcome range is
    once they are.
    """
    if start_probability >= 0.8 and spread <= 4.0:
        return "High"
    if start_probability >= 0.5:
        return "Medium"
    return "Low"


def _not_ready(reason: str, activates_when: str) -> dict[str, Any]:
    return {
        "readiness": Readiness.NOT_READY,
        "written": 0,
        "reason": reason,
        "activates_when": activates_when,
    }


def generate_predictions(
    db: Session,
    season: str,
    gameweek: int,
    served: Any | None,
    information_state: str = InformationState.PRESEASON,
) -> dict[str, Any]:
    """Predict the given gameweek for every player with history.

    Returns a readiness verdict rather than raising, so a caller can surface
    "not ready, because..." in the interface exactly as other features do.
    """
    if served is None:
        return _not_ready(
            "No trained model artefact is available.",
            "Predictions activate once a model has been trained and has "
            "cleared the evaluation gate recorded in docs/MODEL_EVALUATION.md.",
        )

    manifest = served.manifest
    if tuple(manifest.feature_names) != FEATURE_NAMES:
        return _not_ready(
            "The stored model was trained on a different feature set, so its "
            "predictions would not mean what they claim.",
            "Predictions activate once a model trained on feature version "
            f"{FEATURE_VERSION} is available.",
        )

    rows = db.scalars(
        select(GameweekHistory).order_by(
            GameweekHistory.player_code, GameweekHistory.kickoff_time
        )
    ).all()
    if not rows:
        return _not_ready(
            "No gameweek history has been collected, so there is nothing to "
            "predict from.",
            "Predictions activate once historical gameweek data exists.",
        )

    by_player: dict[int, list[GameweekHistory]] = {}
    for row in rows:
        by_player.setdefault(row.player_code, []).append(row)

    current_players = {
        code: player_id
        for player_id, code in db.execute(select(Player.id, Player.code))
        if code is not None
    }

    now = datetime.now(timezone.utc)
    target = _Target(season, gameweek, is_home=True)

    codes: list[int] = []
    x: list[list[float]] = []
    mask: list[list[float]] = []
    for code, history in by_player.items():
        # Only players who are still in the game can be selected, so only they
        # are worth predicting for.
        if code not in current_players:
            continue
        vector = build_features(history, target, now, information_state)
        codes.append(code)
        x.append(vector.as_list())
        mask.append(vector.mask_list())

    if not codes:
        return _not_ready(
            "No current player has enough history to build a feature vector.",
            "Predictions activate once current players have gameweek history.",
        )

    # Two outputs, because no single statistic serves both purposes. The mean
    # is what to display as a projected total: it beat the heuristic's MAE by
    # roughly 8%. The ceiling is what to rank on: minimising error pulls the
    # mean toward the centre, and that shrinkage compresses the spread ordering
    # depends on, whereas a 90th percentile does not shrink. Measured, the mean
    # ranks 0.6693 and the ceiling 0.6864 against a heuristic at 0.6867.
    expected = served.predict(x, mask, feature_names=FEATURE_NAMES)
    quantiles = served.predict_distribution(x, mask, feature_names=FEATURE_NAMES)
    minutes, start_probability = served.predict_minutes(
        x, mask, feature_names=FEATURE_NAMES
    )

    # Replace rather than accumulate: one row per player, gameweek and model
    # version, so re-running is idempotent.
    db.execute(
        delete(Prediction).where(
            Prediction.season == season,
            Prediction.gameweek == gameweek,
            Prediction.model_version == manifest.model_version,
            Prediction.horizon == "next",
        )
    )

    for index, (code, points) in enumerate(zip(codes, expected)):
        floor, median, ceiling = quantiles[index]
        db.add(
            Prediction(
                player_code=code,
                player_id=current_players.get(code),
                season=season,
                gameweek=gameweek,
                horizon="next",
                expected_points=points,
                floor=floor,
                median=median,
                # Rank players on this, not on expected_points.
                ceiling=ceiling,
                expected_minutes=minutes[index],
                start_probability=start_probability[index],
                confidence=_confidence(start_probability[index], ceiling - floor),
                model_name=manifest.model_name,
                model_version=manifest.model_version,
                feature_version=manifest.feature_version,
                information_state=information_state,
                created_at=now,
            )
        )

    db.commit()
    logger.info(
        "predictions written season=%s gameweek=%s model=%s rows=%s",
        season, gameweek, manifest.model_version, len(codes),
    )
    return {
        "readiness": Readiness.READY,
        "written": len(codes),
        "reason": "",
        "model_name": manifest.model_name,
        "model_version": manifest.model_version,
        "information_state": information_state,
    }
