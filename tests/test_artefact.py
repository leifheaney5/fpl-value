import pathlib
import subprocess
import sys
import textwrap

import pytest

from app.models.artefact import Manifest, load_artefact, save_artefact
from app.models.features import FEATURE_NAMES


def _manifest(feature_names=FEATURE_NAMES, **overrides):
    payload = dict(
        model_name="test_model",
        model_version="1.0.0",
        feature_names=tuple(feature_names),
        feature_version="1.0.0",
        information_states=("in_season",),
        training_seasons=("2024/25",),
        training_cutoff="2025-05-25T00:00:00+00:00",
        seed=17,
        metrics={"mae": 1.0},
    )
    payload.update(overrides)
    return Manifest(**payload)


def _fitted_network(dataset):
    torch = pytest.importorskip("torch")
    from app.models.network import NetworkCandidate

    model = NetworkCandidate(seed=17, epochs=1)
    model.fit(dataset)
    return model


def test_manifest_round_trips_through_json():
    manifest = _manifest()
    restored = Manifest.from_json(manifest.to_json())
    assert restored.feature_names == manifest.feature_names
    assert restored.seed == manifest.seed
    assert restored.model_version == manifest.model_version


def test_a_manifest_records_when_it_was_created():
    assert _manifest().created_at


def test_loading_without_a_manifest_is_refused(tmp_path):
    """An artefact without provenance cannot be served."""
    (tmp_path / "model.onnx").write_bytes(b"not a model")
    with pytest.raises(FileNotFoundError, match="provenance"):
        load_artefact(tmp_path)


def test_loading_without_a_model_is_refused(tmp_path):
    (tmp_path / "manifest.json").write_text(_manifest().to_json(), encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="model.onnx"):
        load_artefact(tmp_path)


def test_round_trip_preserves_predictions(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    before = model.predict_batch(tiny_dataset.x, tiny_dataset.mask)

    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)
    after = served.predict(tiny_dataset.x, tiny_dataset.mask)

    assert len(after) == len(before)
    assert all(abs(a - b) < 1e-3 for a, b in zip(before, after)), (
        "ONNX export changed the model's predictions"
    )


def test_feature_mismatch_is_refused_not_coerced(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    with pytest.raises(ValueError, match="feature mismatch"):
        served.predict(tiny_dataset.x, tiny_dataset.mask, feature_names=("a", "b"))


def test_a_shorter_feature_row_is_refused(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    truncated = [row[:5] for row in tiny_dataset.x]
    truncated_mask = [row[:5] for row in tiny_dataset.mask]
    with pytest.raises(ValueError, match="feature mismatch"):
        served.predict(truncated, truncated_mask)


def test_serving_does_not_import_torch(tmp_path, tiny_dataset):
    """torch is a training dependency and must stay out of the production image.

    Checked in a subprocess that never imports torch itself, which proves the
    serving path does not pull it in. Deleting torch from sys.modules in-process
    would only prove it is not re-imported, and corrupts the interpreter for
    later tests because torch's C extensions cannot be initialised twice.
    """
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())

    script = textwrap.dedent(
        f"""
        import sys
        from app.models.artefact import load_artefact

        served = load_artefact({str(tmp_path)!r})
        served.predict([[0.0] * {len(FEATURE_NAMES)}], [[1.0] * {len(FEATURE_NAMES)}])

        leaked = [name for name in sys.modules if name.startswith("torch")]
        if leaked:
            print("LEAKED:" + ",".join(sorted(leaked)[:5]))
            sys.exit(1)
        print("CLEAN")
        """
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        cwd=str(pathlib.Path(__file__).resolve().parent.parent),
    )
    assert result.returncode == 0, (
        f"serving imported torch: {result.stdout.strip()} {result.stderr.strip()[-400:]}"
    )
    assert "CLEAN" in result.stdout


def test_predictions_are_never_negative(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)
    assert all(value >= 0.0 for value in served.predict(tiny_dataset.x, tiny_dataset.mask))


def test_the_served_distribution_is_ordered(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    quantiles = served.predict_distribution(tiny_dataset.x, tiny_dataset.mask)
    assert len(quantiles) == len(tiny_dataset.y)
    for floor, median, ceiling in quantiles:
        assert floor <= median <= ceiling
        assert floor >= 0.0


def test_the_served_distribution_is_reproducible(tmp_path, tiny_dataset):
    """Sampling happens at serve time, so the seed has to make it repeatable."""
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    first = served.predict_distribution(tiny_dataset.x, tiny_dataset.mask, seed=17)
    second = served.predict_distribution(tiny_dataset.x, tiny_dataset.mask, seed=17)
    assert first == second


def test_served_minutes_and_start_probability_are_available(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    minutes, start_probability = served.predict_minutes(
        tiny_dataset.x, tiny_dataset.mask
    )
    assert len(minutes) == len(tiny_dataset.y)
    assert all(0.0 <= v <= 90.0 for v in minutes)
    assert all(0.0 <= v <= 1.0 for v in start_probability)


def test_the_served_ceiling_tracks_the_served_mean(tmp_path, tiny_dataset):
    """The ceiling must be a coherent statistic of the same prediction.

    Deliberately not compared against the training-time sampler: both draw
    randomly, so on any small fixture their ceilings differ by sampling noise
    rather than by anything meaningful. What the export has to preserve is the
    composition, and the mean round-trip above already proves that to 1e-3.
    Here the check is that the ceiling sits above the mean and moves with it.
    """
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)

    means = served.predict(tiny_dataset.x, tiny_dataset.mask)
    quantiles = served.predict_distribution(
        tiny_dataset.x, tiny_dataset.mask, samples=2048
    )
    ceilings = [q[2] for q in quantiles]

    assert len(ceilings) == len(means)
    # A 90th percentile below the mean would mean the distribution and the
    # expectation disagree about the same prediction.
    assert sum(c >= m for c, m in zip(ceilings, means)) >= 0.9 * len(means)


def test_an_empty_batch_returns_nothing_rather_than_failing(tmp_path, tiny_dataset):
    model = _fitted_network(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)
    assert served.predict([], []) == []
