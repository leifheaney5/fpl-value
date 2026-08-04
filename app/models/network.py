"""Two-stage minutes-then-events network.

FPL points are dominated by whether a player is on the pitch at all, so the
model says so explicitly rather than folding that into a single regression:

    head one   appearance over {start, substitute, unused}
               plus expected minutes conditional on each
    head two   per-90 event rates conditional on minutes
    compose    sample both, apply the scoring rules, read percentiles

That decomposition buys three things a direct regression does not. Expected
minutes and start probability come out as first-class outputs, which the
interface already has columns for. A low projection can be explained -- "may not
play" is a different answer from "plays and returns little". And the dominant
source of variance is modelled where it actually lives.

**Training only.** This module imports torch and must never be imported by
anything the web application loads. Serving goes through
``app/models/artefact.py`` and onnxruntime.
"""

from __future__ import annotations

import logging
from typing import Sequence

import torch
from torch import nn

from app.models.dataset import Dataset

logger = logging.getLogger(__name__)

# start, substitute, unused
N_APPEARANCE_CLASSES = 3
# goals, assists, clean sheet, saves, bonus, cards
N_EVENT_RATES = 6

MAX_MINUTES = 90.0


class TwoStageNet(nn.Module):
    def __init__(
        self, n_features: int, hidden: int = 32, dropout: float = 0.2
    ) -> None:
        super().__init__()
        # Small by default. Measured on this data: an unconstrained
        # gradient-boosted fit (200 iterations, unlimited depth) memorised the
        # training seasons and lost to the deployed heuristic on ranking, while
        # a 40-iteration depth-2 fit beat it. Capacity was the deciding factor,
        # not the model class or the loss, so the network starts constrained.
        #
        # The mask is concatenated to the values so the network can learn that a
        # masked feature carries no information, rather than reading its zero as
        # a measurement.
        self.encoder = nn.Sequential(
            nn.Linear(n_features * 2, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
            nn.Linear(hidden, hidden),
            nn.ReLU(),
            nn.Dropout(dropout),
        )
        self.appearance = nn.Linear(hidden, N_APPEARANCE_CLASSES)
        self.minutes = nn.Linear(hidden, 2)
        self.events = nn.Linear(hidden, N_EVENT_RATES)

    def forward(
        self, x: torch.Tensor, mask: torch.Tensor
    ) -> dict[str, torch.Tensor]:
        # Zero out masked values before the first layer. This is what makes the
        # masked-feature guarantee structural rather than a training-time hope:
        # a value behind a zero mask cannot reach any weight.
        gated = x * mask
        hidden = self.encoder(torch.cat([gated, mask], dim=-1))

        minutes = torch.sigmoid(self.minutes(hidden)) * MAX_MINUTES
        return {
            "appearance_logits": self.appearance(hidden),
            "minutes_if_start": minutes[:, 0],
            "minutes_if_sub": minutes[:, 1],
            "event_rates": nn.functional.softplus(self.events(hidden)),
        }


# Points per event, applied after sampling. Deliberately a table rather than
# constants scattered through the composition, so a scoring change is an edit
# here: goals, assists, clean sheet, saves (per three), bonus, cards.
EVENT_POINTS = torch.tensor([5.0, 3.0, 1.0, 1.0 / 3.0, 1.0, -1.0])


class NetworkCandidate:
    """Trainable wrapper matching the TrainableModel protocol."""

    name = "two_stage_network"

    def __init__(
        self,
        seed: int = 17,
        epochs: int = 30,
        hidden: int = 32,
        learning_rate: float = 1e-3,
        batch_size: int = 512,
        samples: int = 128,
        deterministic: bool = True,
        dropout: float = 0.2,
        weight_decay: float = 1e-4,
        patience: int = 3,
        name: str | None = None,
    ) -> None:
        self.seed = seed
        self.epochs = epochs
        self.hidden = hidden
        self.learning_rate = learning_rate
        self.batch_size = batch_size
        self.samples = samples
        self.deterministic = deterministic
        self.dropout = dropout
        self.weight_decay = weight_decay
        self.patience = patience
        # How many epochs actually ran, which early stopping makes less than
        # `epochs`. Recorded so a training run can be audited afterwards.
        self.epochs_run = 0
        if name is not None:
            self.name = name
        self._net: TwoStageNet | None = None
        self._n_features = 0

    # -- training ---------------------------------------------------------

    def fit(self, dataset: Dataset) -> None:
        if not dataset.y:
            raise ValueError("no training rows: cannot fit a model on an empty dataset")

        # Multi-threaded CPU reductions sum in nondeterministic order, so the
        # same seed can produce slightly different gradients between runs. That
        # surfaced as an intermittently failing reproducibility test. Training
        # single-threaded makes the seed mean what it claims; an artefact whose
        # training cannot be repeated cannot be audited or rolled back to.
        previous_threads = torch.get_num_threads()
        if self.deterministic:
            torch.set_num_threads(1)
        try:
            self._fit(dataset)
        finally:
            torch.set_num_threads(previous_threads)

    def _fit(self, dataset: Dataset) -> None:
        torch.manual_seed(self.seed)
        self._n_features = len(dataset.feature_names)
        self._net = TwoStageNet(
            self._n_features, hidden=self.hidden, dropout=self.dropout
        )

        x = torch.tensor(dataset.x, dtype=torch.float32)
        mask = torch.tensor(dataset.mask, dtype=torch.float32)
        points = torch.tensor(dataset.y, dtype=torch.float32)
        minutes = torch.tensor(dataset.minutes, dtype=torch.float32)
        started = torch.tensor(dataset.started, dtype=torch.bool)

        # Appearance label: 0 start, 1 substitute, 2 unused.
        appearance = torch.where(
            started,
            torch.zeros_like(minutes, dtype=torch.long),
            torch.where(
                minutes > 0,
                torch.ones_like(minutes, dtype=torch.long),
                torch.full_like(minutes, 2, dtype=torch.long),
            ),
        )

        optimiser = torch.optim.Adam(
            self._net.parameters(),
            lr=self.learning_rate,
            weight_decay=self.weight_decay,
        )
        appearance_loss = nn.CrossEntropyLoss()

        # Hold out the tail for early stopping. The split is by position rather
        # than at random because the rows arrive ordered by player and time, so
        # a tail split is closer to predicting unseen players than a shuffle.
        n_all = len(points)
        cut = max(1, int(n_all * 0.85))
        train_index = torch.arange(cut)
        validate_index = torch.arange(cut, n_all)

        best_validation = float("inf")
        best_state = None
        since_improvement = 0

        n = cut
        for epoch in range(self.epochs):
            self._net.train()
            permutation = train_index[torch.randperm(n)]
            total = 0.0
            for start in range(0, n, self.batch_size):
                index = permutation[start : start + self.batch_size]
                optimiser.zero_grad()
                out = self._net(x[index], mask[index])

                loss = appearance_loss(out["appearance_logits"], appearance[index])

                # Minutes are supervised only where they were observed: a player
                # who did not appear tells us nothing about how long he would
                # have played.
                played = minutes[index] > 0
                if played.any():
                    predicted = torch.where(
                        started[index][played],
                        out["minutes_if_start"][played],
                        out["minutes_if_sub"][played],
                    )
                    loss = loss + nn.functional.mse_loss(
                        predicted / MAX_MINUTES, minutes[index][played] / MAX_MINUTES
                    )

                # The event head is trained against realised points, which is the
                # quantity the application actually reports.
                #
                # L1, not MSE. FPL points are heavily right-skewed -- most
                # returns are 0-2 and a few are 15+ -- so a squared-error fit
                # chases the tail at the expense of the median and the ordering.
                # Measured on the gradient-boosted candidate over the same
                # folds: switching squared error to absolute error cut preseason
                # MAE from 1.4169 to 1.1345 and raised Spearman from 0.3575 to
                # 0.3678.
                expected = self._expected_points(out)
                loss = loss + nn.functional.l1_loss(expected, points[index])

                loss.backward()
                optimiser.step()
                total += float(loss.item())

            self.epochs_run = epoch + 1

            # Early stopping on held-out absolute error. Evaluated in eval mode
            # so dropout is inert and the measurement reflects what would be
            # served.
            if len(validate_index) == 0:
                logger.info("epoch %s/%s loss=%.4f", epoch + 1, self.epochs, total)
                continue

            self._net.eval()
            with torch.no_grad():
                out = self._net(x[validate_index], mask[validate_index])
                validation = float(
                    nn.functional.l1_loss(
                        self._expected_points(out), points[validate_index]
                    ).item()
                )

            logger.info(
                "epoch %s/%s loss=%.4f validation_l1=%.4f",
                epoch + 1, self.epochs, total, validation,
            )

            if validation < best_validation - 1e-4:
                best_validation = validation
                best_state = {
                    key: value.detach().clone()
                    for key, value in self._net.state_dict().items()
                }
                since_improvement = 0
            else:
                since_improvement += 1
                if since_improvement >= self.patience:
                    logger.info(
                        "early stop at epoch %s: no improvement for %s epochs",
                        epoch + 1, self.patience,
                    )
                    break

        # Restore the best weights seen, not the last: the final epochs are the
        # ones that triggered the stop by getting worse.
        if best_state is not None:
            self._net.load_state_dict(best_state)

    # -- inference --------------------------------------------------------

    def _require_fitted(self) -> TwoStageNet:
        if self._net is None:
            raise RuntimeError(f"{self.name} is not fitted")
        return self._net

    @staticmethod
    def _expected_points(out: dict[str, torch.Tensor]) -> torch.Tensor:
        """Compose the two heads into an expected points total."""
        probabilities = torch.softmax(out["appearance_logits"], dim=-1)
        expected_minutes = (
            probabilities[:, 0] * out["minutes_if_start"]
            + probabilities[:, 1] * out["minutes_if_sub"]
        )
        rates = out["event_rates"] * (expected_minutes / MAX_MINUTES).unsqueeze(-1)
        event_points = (rates * EVENT_POINTS.to(rates.device)).sum(dim=-1)
        # An appearance itself scores, so carry the appearance probability.
        appearance_points = probabilities[:, 0] * 2.0 + probabilities[:, 1] * 1.0
        return event_points + appearance_points

    def _forward(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> dict[str, torch.Tensor]:
        net = self._require_fitted()
        net.eval()
        with torch.no_grad():
            return net(
                torch.tensor(x, dtype=torch.float32),
                torch.tensor(mask, dtype=torch.float32),
            )

    def predict_batch(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> list[float]:
        self._require_fitted()
        if not x:
            return []
        out = self._forward(x, mask)
        return [max(0.0, float(value)) for value in self._expected_points(out)]

    def predict_minutes(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> tuple[list[float], list[float]]:
        """Expected minutes and start probability, as first-class outputs."""
        self._require_fitted()
        if not x:
            return [], []
        out = self._forward(x, mask)
        probabilities = torch.softmax(out["appearance_logits"], dim=-1)
        expected_minutes = (
            probabilities[:, 0] * out["minutes_if_start"]
            + probabilities[:, 1] * out["minutes_if_sub"]
        )
        return (
            [float(min(MAX_MINUTES, max(0.0, v))) for v in expected_minutes],
            [float(v) for v in probabilities[:, 0]],
        )

    def predict_distribution(
        self, x: Sequence[Sequence[float]], mask: Sequence[Sequence[float]]
    ) -> list[tuple[float, float, float]]:
        """Floor, median and ceiling as the 10th, 50th and 90th percentiles.

        Sampled rather than derived analytically: appearance is categorical and
        the event rates are conditional on it, so the composed distribution has
        no closed form worth trusting.
        """
        self._require_fitted()
        if not x:
            return []

        torch.manual_seed(self.seed)
        out = self._forward(x, mask)
        probabilities = torch.softmax(out["appearance_logits"], dim=-1)
        n = probabilities.shape[0]

        draws = torch.multinomial(
            probabilities, num_samples=self.samples, replacement=True
        )
        minutes = torch.where(
            draws == 0,
            out["minutes_if_start"].unsqueeze(-1),
            torch.where(
                draws == 1,
                out["minutes_if_sub"].unsqueeze(-1),
                torch.zeros(n, 1),
            ),
        )

        rates = out["event_rates"].unsqueeze(1) * (minutes / MAX_MINUTES).unsqueeze(-1)
        events = torch.poisson(rates.clamp(min=0.0))
        points = (events * EVENT_POINTS.to(events.device)).sum(dim=-1)
        points = points + torch.where(
            draws == 0,
            torch.full_like(points, 2.0),
            torch.where(draws == 1, torch.ones_like(points), torch.zeros_like(points)),
        )
        points = points.clamp(min=0.0)

        quantiles = torch.quantile(
            points, torch.tensor([0.1, 0.5, 0.9]), dim=1
        )
        return [
            (float(quantiles[0, i]), float(quantiles[1, i]), float(quantiles[2, i]))
            for i in range(n)
        ]
