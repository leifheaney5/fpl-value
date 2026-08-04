"""Model artefacts: what gets shipped, and what has to travel with it.

An artefact is a model plus a manifest. The manifest is not documentation; it is
a contract that is checked at load and at every prediction. A model whose feature
list does not match the caller's is refused rather than coerced, because silently
reordering or truncating features produces confident nonsense.

Serving deliberately does not import torch. Training uses PyTorch; production
loads an ONNX graph through onnxruntime, which is roughly fifty megabytes rather
than eight hundred. A test asserts torch stays out.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

from app.models.scoring import (
    EVENT_POINTS,
    HEAD_LAYOUT,
    MAX_MINUTES,
    POINTS_FOR_START,
    POINTS_FOR_SUBSTITUTE,
)

logger = logging.getLogger(__name__)

MANIFEST_NAME = "manifest.json"
MODEL_NAME = "model.onnx"


@dataclass
class Manifest:
    """Everything needed to interpret, audit or roll back a model."""

    model_name: str
    model_version: str
    feature_names: tuple[str, ...]
    feature_version: str
    information_states: tuple[str, ...]
    training_seasons: tuple[str, ...]
    training_cutoff: str
    seed: int
    metrics: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def __post_init__(self) -> None:
        if not self.created_at:
            self.created_at = datetime.now(timezone.utc).isoformat()

    def to_json(self) -> str:
        payload = asdict(self)
        payload["feature_names"] = list(self.feature_names)
        payload["information_states"] = list(self.information_states)
        payload["training_seasons"] = list(self.training_seasons)
        return json.dumps(payload, indent=2)

    @staticmethod
    def from_json(text: str) -> "Manifest":
        payload = json.loads(text)
        payload["feature_names"] = tuple(payload["feature_names"])
        payload["information_states"] = tuple(payload["information_states"])
        payload["training_seasons"] = tuple(payload["training_seasons"])
        return Manifest(**payload)


class ServedModel:
    """An ONNX model plus its manifest, ready to predict."""

    def __init__(self, session: Any, manifest: Manifest) -> None:
        self._session = session
        self.manifest = manifest

    @property
    def feature_names(self) -> tuple[str, ...]:
        return self.manifest.feature_names

    def _heads(
        self,
        x: Sequence[Sequence[float]],
        mask: Sequence[Sequence[float]],
        feature_names: Sequence[str] | None,
    ):
        if feature_names is not None and tuple(feature_names) != self.feature_names:
            raise ValueError(
                "feature mismatch: this artefact was trained on a different "
                "feature set and its predictions would be meaningless. "
                f"Expected {len(self.feature_names)} features, "
                f"got {len(feature_names)}."
            )
        if not x:
            return None
        if len(x[0]) != len(self.feature_names):
            raise ValueError(
                f"feature mismatch: artefact expects {len(self.feature_names)} "
                f"features, received {len(x[0])}."
            )

        import numpy as np

        inputs = np.asarray(
            [list(values) + list(flags) for values, flags in zip(x, mask)],
            dtype=np.float32,
        )
        name = self._session.get_inputs()[0].name
        return np.asarray(self._session.run(None, {name: inputs})[0])

    @staticmethod
    def _split(heads):
        import numpy as np

        def part(key):
            start, stop = HEAD_LAYOUT[key]
            return heads[:, start:stop]

        logits = part("appearance_logits")
        shifted = logits - logits.max(axis=-1, keepdims=True)
        exponentiated = np.exp(shifted)
        probabilities = exponentiated / exponentiated.sum(axis=-1, keepdims=True)
        return (
            probabilities,
            part("minutes_if_start").reshape(-1),
            part("minutes_if_sub").reshape(-1),
            part("event_rates"),
        )

    def predict(
        self,
        x: Sequence[Sequence[float]],
        mask: Sequence[Sequence[float]],
        feature_names: Sequence[str] | None = None,
    ) -> list[float]:
        """Expected points: the statistic to display as a projected total."""
        import numpy as np

        heads = self._heads(x, mask, feature_names)
        if heads is None:
            return []

        probabilities, minutes_start, minutes_sub, rates = self._split(heads)
        expected_minutes = (
            probabilities[:, 0] * minutes_start + probabilities[:, 1] * minutes_sub
        )
        scaled = rates * (expected_minutes / MAX_MINUTES)[:, None]
        event_points = (scaled * np.asarray(EVENT_POINTS)).sum(axis=-1)
        appearance = (
            probabilities[:, 0] * POINTS_FOR_START
            + probabilities[:, 1] * POINTS_FOR_SUBSTITUTE
        )
        return [max(0.0, float(v)) for v in event_points + appearance]

    def predict_minutes(
        self,
        x: Sequence[Sequence[float]],
        mask: Sequence[Sequence[float]],
        feature_names: Sequence[str] | None = None,
    ) -> tuple[list[float], list[float]]:
        """Expected minutes and start probability, as first-class outputs."""
        heads = self._heads(x, mask, feature_names)
        if heads is None:
            return [], []
        probabilities, minutes_start, minutes_sub, _ = self._split(heads)
        expected_minutes = (
            probabilities[:, 0] * minutes_start + probabilities[:, 1] * minutes_sub
        )
        return (
            [float(min(MAX_MINUTES, max(0.0, v))) for v in expected_minutes],
            [float(v) for v in probabilities[:, 0]],
        )

    def predict_distribution(
        self,
        x: Sequence[Sequence[float]],
        mask: Sequence[Sequence[float]],
        feature_names: Sequence[str] | None = None,
        samples: int = 2048,
        seed: int = 17,
    ) -> list[tuple[float, float, float]]:
        """Floor, median and ceiling as the 10th, 50th and 90th percentiles.

        Sampled here rather than inside the graph, so the exported model stays a
        plain deterministic function and serving needs only numpy.

        The default of 2048 draws is not arbitrary. At 128 the quantile estimate
        is coarse enough that rank correlation is measurably penalised for ties:
        the ceiling scored 0.6829 at 128 draws and 0.6864 at 2048, against a
        heuristic at 0.6867. Since ranking uses the ceiling, resolution here is
        load-bearing.
        """
        import numpy as np

        heads = self._heads(x, mask, feature_names)
        if heads is None:
            return []

        probabilities, minutes_start, minutes_sub, rates = self._split(heads)
        rng = np.random.default_rng(seed)
        n = probabilities.shape[0]

        cumulative = probabilities.cumsum(axis=-1)
        draws = (rng.random((n, samples))[:, :, None] > cumulative[:, None, :]).sum(
            axis=-1
        )

        minutes = np.where(
            draws == 0,
            minutes_start[:, None],
            np.where(draws == 1, minutes_sub[:, None], 0.0),
        )
        scaled = rates[:, None, :] * (minutes / MAX_MINUTES)[:, :, None]
        events = rng.poisson(np.clip(scaled, 0.0, None))
        points = (events * np.asarray(EVENT_POINTS)).sum(axis=-1)
        points = points + np.where(
            draws == 0, POINTS_FOR_START, np.where(draws == 1, POINTS_FOR_SUBSTITUTE, 0.0)
        )
        points = np.clip(points, 0.0, None)

        quantiles = np.quantile(points, [0.1, 0.5, 0.9], axis=1)
        return [
            (float(quantiles[0, i]), float(quantiles[1, i]), float(quantiles[2, i]))
            for i in range(n)
        ]


def save_artefact(directory: str | Path, model: Any, manifest: Manifest) -> Path:
    """Export a fitted model to ONNX alongside its manifest."""
    path = Path(directory)
    path.mkdir(parents=True, exist_ok=True)

    n_inputs = len(manifest.feature_names) * 2
    _export_onnx(model, n_inputs, path / MODEL_NAME)
    (path / MANIFEST_NAME).write_text(manifest.to_json(), encoding="utf-8")

    logger.info(
        "artefact saved name=%s version=%s path=%s",
        manifest.model_name, manifest.model_version, path,
    )
    return path


def _export_onnx(model: Any, n_inputs: int, destination: Path) -> None:
    """Export whichever candidate type this is.

    The network exports through torch; the tree model through skl2onnx when it
    is available, and otherwise through a small ONNX graph built from the
    fitted trees is not attempted -- instead the caller is told plainly.
    """
    inner = getattr(model, "_net", None)
    if inner is not None:
        import torch

        inner.eval()
        n_features = n_inputs // 2
        dummy_x = torch.zeros(1, n_features)
        dummy_mask = torch.ones(1, n_features)

        class _Wrapper(torch.nn.Module):
            """Emit the raw heads, not a composed total.

            Composition and sampling happen outside the graph so that serving
            can produce the full distribution -- floor, median and ceiling --
            without needing torch. Ranking uses the ceiling, so exporting only
            an expected value would throw away the output the interface orders
            players by.
            """

            def __init__(self, net):
                super().__init__()
                self.net = net

            def forward(self, combined):
                half = combined.shape[-1] // 2
                values, flags = combined[:, :half], combined[:, half:]
                out = self.net(values, flags)
                return torch.cat(
                    [
                        out["appearance_logits"],
                        out["minutes_if_start"].unsqueeze(-1),
                        out["minutes_if_sub"].unsqueeze(-1),
                        out["event_rates"],
                    ],
                    dim=-1,
                )

        torch.onnx.export(
            _Wrapper(inner),
            torch.cat([dummy_x, dummy_mask], dim=-1),
            str(destination),
            input_names=["features"],
            output_names=["heads"],
            dynamic_axes={"features": {0: "batch"}, "heads": {0: "batch"}},
            opset_version=17,
        )
        return

    sklearn_model = getattr(model, "_model", None)
    if sklearn_model is None:
        raise ValueError(
            "model is not fitted, so there is nothing to export"
        )

    try:
        from skl2onnx import to_onnx
    except ImportError as exc:  # pragma: no cover - depends on environment
        raise RuntimeError(
            "Exporting a scikit-learn model to ONNX requires skl2onnx "
            "(`pip install skl2onnx`). The model is fitted but cannot be "
            "serialised for serving without it."
        ) from exc

    import numpy as np

    onnx_model = to_onnx(
        sklearn_model, np.zeros((1, n_inputs), dtype=np.float32)
    )
    destination.write_bytes(onnx_model.SerializeToString())


def load_artefact(directory: str | Path) -> ServedModel:
    """Load an artefact for serving. Refuses anything without a manifest."""
    path = Path(directory)
    manifest_path = path / MANIFEST_NAME
    model_path = path / MODEL_NAME

    if not manifest_path.exists():
        raise FileNotFoundError(
            f"No {MANIFEST_NAME} in {path}. An artefact without provenance "
            "cannot be served: there would be no way to say which model "
            "produced a number, or on what data."
        )
    if not model_path.exists():
        raise FileNotFoundError(f"No {MODEL_NAME} in {path}")

    import onnxruntime

    manifest = Manifest.from_json(manifest_path.read_text(encoding="utf-8"))
    session = onnxruntime.InferenceSession(
        str(model_path), providers=["CPUExecutionProvider"]
    )
    logger.info(
        "artefact loaded name=%s version=%s", manifest.model_name, manifest.model_version
    )
    return ServedModel(session, manifest)
