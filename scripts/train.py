#!/usr/bin/env python
"""Train a prediction model offline and export it for serving.

Refuses to write an artefact that has not cleared the evaluation gate recorded
in docs/MODEL_EVALUATION.md. The gate exists so that a model which is worse
than the heuristic it would replace cannot ship by accident; ``--force``
overrides it deliberately and records that it was overridden.

Training uses PyTorch and is expected to run on a development machine, never in
the deployed container. The exported artefact is ONNX and is served through
onnxruntime.

    python scripts/train.py --output models/two_stage_v1
    python scripts/train.py --model gradient_boosted --state preseason
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db.session import SessionLocal  # noqa: E402
from app.models.artefact import Manifest, save_artefact  # noqa: E402
from app.models.dataset import build_dataset  # noqa: E402
from app.models.features import (  # noqa: E402
    FEATURE_NAMES,
    VERSION as FEATURE_VERSION,
    InformationState,
)
from app.models.evaluation import season_order  # noqa: E402
from app.models.metrics import spearman_correlation, summarise  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
logger = logging.getLogger("train")

# Copied verbatim from docs/MODEL_EVALUATION.md. A model must beat the deployed
# heuristic on both accuracy and ranking to replace it.
#
# The preseason thresholds were corrected on 2026-08-04. They previously held
# the best MAE and best Spearman across *different* baselines, which described a
# composite no single model could match -- the heuristic itself failed it on
# both counts, so the gate was unachievable by construction.
GATE = {
    InformationState.IN_SEASON: {"mae": 1.0638, "spearman": 0.6899},
    InformationState.PRESEASON: {"mae": 1.2891, "spearman": 0.3065},
}


def _build_model(name: str, seed: int, epochs: int):
    if name == "gradient_boosted":
        from app.models.candidates import GradientBoostedCandidate

        return GradientBoostedCandidate(seed=seed, loss="absolute_error")
    if name == "two_stage_network":
        from app.models.network import NetworkCandidate

        return NetworkCandidate(seed=seed, epochs=epochs)
    raise SystemExit(f"Unknown model {name!r}")


def _clears_gate(
    state: str, mean_scores: dict, ceiling_spearman: float | None
) -> tuple[bool, list[str]]:
    """Judge each output on the metric it actually serves.

    The mean is displayed as a projected total, so it is judged on MAE. The
    ceiling is what players are ranked by, so it is judged on Spearman. Holding
    one statistic to both was the original specification and it is not
    achievable: minimising error pulls the mean toward the conditional centre,
    and that shrinkage compresses the spread ranking depends on.
    """
    thresholds = GATE[state]
    failures = []

    mae = mean_scores.get("mae")
    if mae is None or mae >= thresholds["mae"]:
        failures.append(
            f"displayed total: MAE {mae} is not below {thresholds['mae']}"
        )

    if ceiling_spearman is None or ceiling_spearman <= thresholds["spearman"]:
        failures.append(
            f"ranking: ceiling Spearman {ceiling_spearman} is not above "
            f"{thresholds['spearman']}"
        )

    return not failures, failures


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, help="Artefact directory")
    parser.add_argument("--model", default="two_stage_network")
    parser.add_argument("--state", default=InformationState.PRESEASON,
                        choices=[InformationState.PRESEASON, InformationState.IN_SEASON])
    parser.add_argument("--holdout", default=None,
                        help="Season held out for the gate check. Defaults to the latest.")
    parser.add_argument("--seed", type=int, default=17)
    parser.add_argument("--epochs", type=int, default=12)
    parser.add_argument("--version", default="1.0.0")
    parser.add_argument("--force", action="store_true",
                        help="Write the artefact even if it fails the gate.")
    args = parser.parse_args()

    with SessionLocal() as db:
        data = build_dataset(db, information_state=args.state)

    if not data.y:
        raise SystemExit(
            "No training rows. Run `python -m app.cli import-archive` first."
        )

    seasons = season_order(set(data.season))
    holdout = args.holdout or seasons[-1]
    if holdout not in seasons:
        raise SystemExit(f"Holdout season {holdout!r} is not in the dataset")
    train_seasons = [s for s in seasons if s < holdout]
    if not train_seasons:
        raise SystemExit("No seasons earlier than the holdout to train on")

    train = data.slice_seasons(train_seasons)
    test = data.slice_seasons([holdout])
    logger.info(
        "training on %s (%s rows), holding out %s (%s rows)",
        ", ".join(train_seasons), len(train.y), holdout, len(test.y),
    )

    model = _build_model(args.model, args.seed, args.epochs)
    model.fit(train)

    scores = summarise(test.y, model.predict_batch(test.x, test.mask))
    logger.info("holdout scores (displayed total): %s", json.dumps(scores, default=str))

    # Ranking is judged on the ceiling where the model produces a distribution.
    ceiling_spearman = scores.get("spearman")
    if hasattr(model, "predict_distribution"):
        quantiles = model.predict_distribution(test.x, test.mask)
        ceiling_spearman = spearman_correlation(test.y, [q[2] for q in quantiles])
        logger.info("holdout ranking (ceiling) Spearman: %s", ceiling_spearman)

    passed, failures = _clears_gate(args.state, scores, ceiling_spearman)
    if passed:
        logger.info("model clears the %s gate", args.state)
    else:
        logger.warning("model FAILS the %s gate: %s", args.state, "; ".join(failures))
        if not args.force:
            logger.warning(
                "Artefact not written. The deployed heuristic is better than "
                "this model on the held-out season, so shipping it would make "
                "the application worse. Re-run with --force to override."
            )
            return 1

    manifest = Manifest(
        model_name=args.model,
        model_version=args.version,
        feature_names=FEATURE_NAMES,
        feature_version=FEATURE_VERSION,
        information_states=(args.state,),
        training_seasons=tuple(train_seasons),
        training_cutoff=datetime.now(timezone.utc).isoformat(),
        seed=args.seed,
        metrics={
            "holdout_season": holdout,
            "displayed_total": scores,
            "ranking_ceiling_spearman": ceiling_spearman,
            "gate_passed": passed,
            "gate_failures": failures,
            "forced": bool(args.force and not passed),
        },
    )
    save_artefact(args.output, model, manifest)
    logger.info("artefact written to %s", args.output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
