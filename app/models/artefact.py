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

    def predict(
        self,
        x: Sequence[Sequence[float]],
        mask: Sequence[Sequence[float]],
        feature_names: Sequence[str] | None = None,
    ) -> list[float]:
        if feature_names is not None and tuple(feature_names) != self.feature_names:
            raise ValueError(
                "feature mismatch: this artefact was trained on a different "
                "feature set and its predictions would be meaningless. "
                f"Expected {len(self.feature_names)} features, "
                f"got {len(feature_names)}."
            )
        if not x:
            return []
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
        outputs = self._session.run(None, {name: inputs})[0]
        return [max(0.0, float(value)) for value in np.asarray(outputs).reshape(-1)]


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
            def __init__(self, net):
                super().__init__()
                self.net = net

            def forward(self, combined):
                half = combined.shape[-1] // 2
                values, flags = combined[:, :half], combined[:, half:]
                out = self.net(values, flags)
                probabilities = torch.softmax(out["appearance_logits"], dim=-1)
                expected_minutes = (
                    probabilities[:, 0] * out["minutes_if_start"]
                    + probabilities[:, 1] * out["minutes_if_sub"]
                )
                rates = out["event_rates"] * (expected_minutes / 90.0).unsqueeze(-1)
                points = torch.tensor(
                    [5.0, 3.0, 1.0, 1.0 / 3.0, 1.0, -1.0]
                ).to(rates.device)
                appearance = probabilities[:, 0] * 2.0 + probabilities[:, 1] * 1.0
                return (rates * points).sum(dim=-1) + appearance

        torch.onnx.export(
            _Wrapper(inner),
            torch.cat([dummy_x, dummy_mask], dim=-1),
            str(destination),
            input_names=["features"],
            output_names=["expected_points"],
            dynamic_axes={"features": {0: "batch"}, "expected_points": {0: "batch"}},
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
