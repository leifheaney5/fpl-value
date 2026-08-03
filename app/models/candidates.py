"""Trainable prediction candidates.

A candidate is anything that can be fitted on a training slice and then scored
on a held-out season by the same harness that scores the baselines. Sharing the
harness is the point: a model and the heuristic it hopes to replace are measured
by identical code on identical folds.

The feature mask is concatenated to the feature values before fitting, so a
model can learn that a masked feature carries no information rather than reading
its zero as a measurement. That is the same distinction the rest of this
application draws between a real zero and an absent value.
"""

from __future__ import annotations

import logging
from typing import Protocol, Sequence

from app.models.dataset import Dataset

logger = logging.getLogger(__name__)


class TrainableModel(Protocol):
    name: str

    def fit(self, dataset: Dataset) -> None: ...

    def predict_batch(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> list[float]: ...


def _inputs(
    x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
) -> list[list[float]]:
    return [list(values) + list(flags) for values, flags in zip(x, mask)]


class GradientBoostedCandidate:
    """Gradient-boosted trees over the masked feature set.

    This runs before any neural network deliberately. It is cheap, and it
    answers the question that decides whether a network is worth building: does
    this feature set carry signal beyond the heuristics at all? If it does not,
    a network inherits the same ceiling and the next work is features.
    """

    name = "gradient_boosted"

    def __init__(
        self,
        seed: int = 17,
        max_iter: int = 200,
        loss: str = "absolute_error",
        name: str | None = None,
    ) -> None:
        # Squared error is the sklearn default and it is the wrong objective
        # here. FPL points are heavily right-skewed -- most returns are 0-2 and
        # a few are 15+ -- so a squared-error fit chases the tail and gives up
        # median accuracy and ranking, which is what the interface actually
        # uses. Measured: squared error scored the best RMSE of any model in
        # both information states while scoring the worst preseason MAE and
        # Spearman.
        self.seed = seed
        self.max_iter = max_iter
        self.loss = loss
        if name is not None:
            self.name = name
        self._model = None
        self.n_inputs = 0

    def fit(self, dataset: Dataset) -> None:
        if not dataset.y:
            raise ValueError("no training rows: cannot fit a model on an empty dataset")

        from sklearn.ensemble import HistGradientBoostingRegressor

        features = _inputs(dataset.x, dataset.mask)
        self.n_inputs = len(features[0])
        self._model = HistGradientBoostingRegressor(
            random_state=self.seed,
            max_iter=self.max_iter,
            loss=self.loss,
            early_stopping=False,
        )
        self._model.fit(features, list(dataset.y))
        logger.info(
            "fitted %s rows=%s inputs=%s seed=%s",
            self.name, len(dataset.y), self.n_inputs, self.seed,
        )

    def predict_batch(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> list[float]:
        if self._model is None:
            raise RuntimeError(f"{self.name} is not fitted")
        if not x:
            return []
        # A projection below zero is never the useful answer; the floor of the
        # distribution is where downside belongs.
        return [max(0.0, float(value)) for value in self._model.predict(_inputs(x, mask))]
