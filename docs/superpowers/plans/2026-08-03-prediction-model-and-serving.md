# Prediction Model and Serving Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Train a two-stage minutes-then-events model, prove on held-out seasons whether it beats the deployed heuristic, and serve the winner as a versioned artefact that carries its own uncertainty and provenance.

**Architecture:** A dataset builder materialises point-in-time features once per information state, so folds slice arrays instead of rebuilding features. A gradient-boosted candidate goes first because it is cheap and answers whether the feature set has signal at all. The network then adds the two-stage decomposition and a points distribution. Whatever wins is exported to ONNX and served without PyTorch in the production image.

**Tech Stack:** Python 3.12, numpy 2.2, scikit-learn 1.7, PyTorch 2.7 (training only), ONNX 1.18 / onnxruntime 1.19 (serving), SQLAlchemy 2, Alembic, pytest.

## Global Constraints

- Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`.
- Alembic revision for this increment is `0008`, `down_revision = "0007"`.
- **`torch` must never be imported by anything the web application loads.** It is a training-only dependency. Serving uses `onnxruntime`. A test asserts this.
- A model ships only if it clears the gate in `docs/MODEL_EVALUATION.md`:
  - In-season: MAE below **1.0638** and Spearman above **0.6899**
  - Preseason: MAE below **1.2862** and Spearman above **0.3066**
- Training is reproducible: a fixed seed, and the seed is recorded in the artefact.
- A prediction that cannot be produced is `not_ready` with a reason. Never zero.
- No feature may use a row at or after the prediction time. `app/models/features.py` owns that guarantee; nothing in this plan may work around it.

## Baseline context

From `docs/MODEL_EVALUATION.md`, 230,211 test examples over nine walk-forward folds:

| State | Best baseline | MAE | Spearman |
| --- | --- | ---: | ---: |
| In-season | `existing_heuristic` | 1.0638 | 0.6899 |
| Preseason | `minutes_weighted` (MAE), `fixture_adjusted` (Spearman) | 1.2862 | 0.3066 |

Preseason is where the room is: all six baselines sit within 0.0008 Spearman of
each other because, with current-season features masked, they all reduce to last
season's points per game.

## File structure

| File | Responsibility |
| --- | --- |
| `app/models/dataset.py` | Materialise features into arrays once per information state |
| `app/models/candidates.py` | Trainable candidates: gradient-boosted, and the network wrapper |
| `app/models/network.py` | The two-stage PyTorch model. Training-only import. |
| `app/models/artefact.py` | Artefact manifest, save, load, ONNX export/import |
| `app/models/evaluation.py` | Extended to fit trainables per fold before scoring |
| `app/services/predictions.py` | Produce and store predictions from a served artefact |
| `alembic/versions/0008_predictions.py` | `predictions` table |
| `scripts/train.py` | Offline training entrypoint |

---

### Task 1: Dataset builder

Materialising features once is not an optimisation detail. The baseline run
rebuilt features per fold and took roughly twenty minutes; training needs many
passes, so per-fold rebuilding would make the rest of this plan impractical.

**Files:**
- Create: `app/models/dataset.py`
- Test: `tests/test_dataset.py`

**Interfaces:**
- Consumes: `build_features`, `InformationState`, `FEATURE_NAMES` from `app.models.features`; `GameweekHistory`.
- Produces: `Dataset` dataclass with `x: list[list[float]]`, `mask: list[list[float]]`, `y: list[float]`, `minutes: list[float]`, `started: list[bool]`, `season: list[str]`, `gameweek: list[int]`, `player_code: list[int]`, `information_state: str`, `feature_names: tuple[str, ...]`; `build_dataset(db, seasons=None, information_state=..., limit=None) -> Dataset`; `Dataset.slice_seasons(seasons) -> Dataset`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_dataset.py
from app.models.dataset import build_dataset
from app.models.features import FEATURE_NAMES, InformationState


def test_dataset_rows_align_across_every_parallel_array(seeded_history_db):
    data = build_dataset(seeded_history_db, information_state=InformationState.IN_SEASON)
    n = len(data.y)
    assert n > 0
    for name in ("x", "mask", "minutes", "started", "season", "gameweek", "player_code"):
        assert len(getattr(data, name)) == n, f"{name} is not aligned with y"
    assert data.feature_names == FEATURE_NAMES
    assert all(len(row) == len(FEATURE_NAMES) for row in data.x)


def test_slicing_by_season_preserves_alignment(seeded_history_db):
    data = build_dataset(seeded_history_db, information_state=InformationState.IN_SEASON)
    seasons = sorted(set(data.season))
    sliced = data.slice_seasons([seasons[0]])
    assert set(sliced.season) == {seasons[0]}
    assert len(sliced.x) == len(sliced.y) == len(sliced.season)


def test_preseason_dataset_masks_current_season_features(seeded_history_db):
    data = build_dataset(seeded_history_db, information_state=InformationState.PRESEASON)
    current = [i for i, n in enumerate(FEATURE_NAMES) if n.startswith("cur_")]
    for row_mask, row_values in zip(data.mask, data.x):
        for index in current:
            assert row_mask[index] == 0.0
            assert row_values[index] == 0.0


def test_target_is_the_points_scored_in_that_fixture(seeded_history_db):
    data = build_dataset(seeded_history_db, information_state=InformationState.IN_SEASON)
    assert all(value >= -10 for value in data.y)
    assert any(value > 0 for value in data.y)
```

Add a `seeded_history_db` fixture to `tests/conftest.py` creating an in-memory
database with two players and twelve `GameweekHistory` rows across two seasons,
each with a distinct `kickoff_time`.

- [ ] **Step 2: Run to verify it fails**

Run: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_dataset.py -q -p no:warnings`
Expected: FAIL, module not found.

- [ ] **Step 3: Implement `app/models/dataset.py`**

One pass over all requested seasons, grouped by `player_code`, building a feature
vector per row at that row's `kickoff_time`. `slice_seasons` filters every
parallel array by the same index set so alignment cannot drift.

- [ ] **Step 4: Run the tests, then commit**

```bash
git add app/models/dataset.py tests/test_dataset.py tests/conftest.py
git commit -m "Materialise point-in-time features into aligned arrays"
```

---

### Task 2: Fit trainable candidates inside the harness

**Files:**
- Modify: `app/models/evaluation.py`
- Create: `app/models/candidates.py`
- Test: `tests/test_candidates.py`

**Interfaces:**
- Produces: `TrainableModel` protocol with `name: str`, `fit(dataset: Dataset) -> None`, `predict_batch(x, mask) -> list[float]`; `GradientBoostedCandidate(seed: int = 17)`; `evaluate_fold(..., trainables: Sequence[TrainableModel] = ())` fitting each trainable on the training seasons before scoring on the test season.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_candidates.py
import pytest

from app.models.candidates import GradientBoostedCandidate


def test_a_candidate_refuses_to_predict_before_it_is_fitted(tiny_dataset):
    model = GradientBoostedCandidate()
    with pytest.raises(RuntimeError, match="not fitted"):
        model.predict_batch(tiny_dataset.x, tiny_dataset.mask)


def test_fitting_then_predicting_returns_one_value_per_row(tiny_dataset):
    model = GradientBoostedCandidate()
    model.fit(tiny_dataset)
    predictions = model.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    assert len(predictions) == len(tiny_dataset.y)
    assert all(isinstance(value, float) for value in predictions)


def test_training_is_reproducible_from_the_seed(tiny_dataset):
    first = GradientBoostedCandidate(seed=17)
    second = GradientBoostedCandidate(seed=17)
    first.fit(tiny_dataset)
    second.fit(tiny_dataset)
    assert first.predict_batch(tiny_dataset.x, tiny_dataset.mask) == (
        second.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    )


def test_predictions_are_never_negative():
    """FPL points can be negative, but a projection below zero is not useful
    and never beats simply predicting the floor."""
    ...
```

- [ ] **Step 2: Run to verify it fails, then implement**

`GradientBoostedCandidate` wraps `sklearn.ensemble.HistGradientBoostingRegressor`
with `random_state=seed`. It concatenates `x` and `mask` as input, so the model
can learn to distrust a feature that is masked. `predict_batch` raises
`RuntimeError("model is not fitted")` before `fit`.

`evaluate_fold` gains `trainables`: build the training slice, fit each, then
score on the test slice alongside the baselines.

- [ ] **Step 3: Commit**

```bash
git add app/models/candidates.py app/models/evaluation.py tests/test_candidates.py
git commit -m "Fit trainable candidates within each walk-forward fold"
```

---

### Task 3: Run the gradient-boosted candidate against the gate

This task produces a **decision**, not just code. It answers whether the feature
set carries signal beyond the heuristics before any effort goes into a network.

**Files:**
- Modify: `docs/MODEL_EVALUATION.md`

- [ ] **Step 1: Run the full evaluation including the candidate**

```bash
python -m app.cli evaluate --output docs/evaluation-gbdt.json
```

- [ ] **Step 2: Record the result in `docs/MODEL_EVALUATION.md`**

Add a "Gradient-boosted candidate" section with MAE, RMSE and Spearman for both
information states, and state plainly whether it clears the gate.

- [ ] **Step 3: Decide and write the decision down**

If the candidate does not beat the preseason baselines, the feature set is the
problem, not the model class, and the next work is features — most obviously the
opponent-strength proxy noted as limitation 1. Say so in the document rather
than proceeding to a network that will inherit the same ceiling.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "Record gradient-boosted candidate against the ship gate"
```

---

### Task 4: Two-stage network

**Files:**
- Create: `app/models/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Produces: `TwoStageNet(nn.Module)` with `forward(x, mask) -> dict` containing `appearance_logits` (3), `minutes_if_start`, `minutes_if_sub`, `event_rates` (6); `NetworkCandidate(TrainableModel)` with `fit`, `predict_batch`, `predict_distribution(x, mask) -> list[tuple[float, float, float]]`.
- Consumes: `Dataset`.

Head one predicts appearance over `{start, substitute, unused}` plus expected
minutes for each branch, which is where FPL variance actually lives. Head two
predicts per-90 event rates conditional on minutes. Composition applies the
scoring rules to produce a points distribution.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_network.py
import pytest

torch = pytest.importorskip("torch")

from app.models.network import NetworkCandidate, TwoStageNet


def test_appearance_probabilities_sum_to_one():
    net = TwoStageNet(n_features=36)
    x = torch.zeros(4, 36)
    mask = torch.ones(4, 36)
    out = net(x, mask)
    probabilities = torch.softmax(out["appearance_logits"], dim=-1)
    assert torch.allclose(probabilities.sum(dim=-1), torch.ones(4), atol=1e-5)


def test_expected_minutes_stay_within_a_football_match():
    net = TwoStageNet(n_features=36)
    out = net(torch.randn(8, 36) * 5, torch.ones(8, 36))
    assert (out["minutes_if_start"] >= 0).all()
    assert (out["minutes_if_start"] <= 90).all()
    assert (out["minutes_if_sub"] <= 90).all()


def test_event_rates_are_non_negative():
    net = TwoStageNet(n_features=36)
    out = net(torch.randn(8, 36) * 5, torch.ones(8, 36))
    assert (out["event_rates"] >= 0).all()


def test_a_masked_feature_cannot_change_the_output():
    """Masked means unavailable. Whatever sits behind the mask is not evidence."""
    net = TwoStageNet(n_features=36).eval()
    mask = torch.ones(1, 36)
    mask[0, 5] = 0.0
    a, b = torch.zeros(1, 36), torch.zeros(1, 36)
    b[0, 5] = 99.0
    with torch.no_grad():
        assert torch.allclose(
            net(a, mask)["appearance_logits"], net(b, mask)["appearance_logits"]
        )


def test_distribution_is_ordered_floor_median_ceiling(tiny_dataset):
    model = NetworkCandidate(seed=17, epochs=2)
    model.fit(tiny_dataset)
    for floor, median, ceiling in model.predict_distribution(
        tiny_dataset.x, tiny_dataset.mask
    ):
        assert floor <= median <= ceiling
```

- [ ] **Step 2: Run to verify it fails, then implement**

The masked-input test is the load-bearing one: `TwoStageNet.forward` must
multiply `x` by `mask` before the first layer, so a value behind a zero mask
cannot reach any weight.

Minutes heads use `90 * sigmoid(.)`; event rates use `softplus(.)`.
`predict_distribution` samples appearance, then minutes, then events, applies
the FPL scoring rules by position, and returns the 10th, 50th and 90th
percentiles.

- [ ] **Step 3: Commit**

```bash
git add app/models/network.py tests/test_network.py
git commit -m "Add two-stage minutes-then-events network"
```

---

### Task 5: Run the network against the gate

**Files:**
- Modify: `docs/MODEL_EVALUATION.md`

- [ ] **Step 1: Evaluate the network on the same folds**
- [ ] **Step 2: Record MAE, RMSE, Spearman and calibration for both states**
- [ ] **Step 3: Declare the outcome**

State which model wins each information state. If the network loses both, the
honest outcome is that it does not ship and the document says so. That is a
result, not a failure, and it is why the gate was written before the model.

- [ ] **Step 4: Commit**

---

### Task 6: Artefact format and ONNX export

**Files:**
- Create: `app/models/artefact.py`
- Test: `tests/test_artefact.py`

**Interfaces:**
- Produces: `Manifest` dataclass with `model_name`, `model_version`, `feature_names`, `feature_version`, `information_states`, `training_seasons`, `training_cutoff`, `seed`, `metrics`, `created_at`; `save_artefact(directory, model, manifest)`; `load_artefact(directory) -> ServedModel`; `ServedModel.predict(x, mask) -> list[float]` backed by `onnxruntime`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_artefact.py
def test_a_prediction_requires_a_manifest(tmp_path):
    """An artefact without provenance cannot be served."""
    (tmp_path / "model.onnx").write_bytes(b"not a model")
    with pytest.raises(FileNotFoundError):
        load_artefact(tmp_path)


def test_round_trip_preserves_predictions(tmp_path, tiny_dataset):
    model = GradientBoostedCandidate(seed=17)
    model.fit(tiny_dataset)
    before = model.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    save_artefact(tmp_path, model, _manifest())
    served = load_artefact(tmp_path)
    after = served.predict(tiny_dataset.x, tiny_dataset.mask)
    assert all(abs(a - b) < 1e-4 for a, b in zip(before, after))


def test_feature_mismatch_is_refused_not_coerced(tmp_path, tiny_dataset):
    save_artefact(tmp_path, _fitted_model(tiny_dataset), _manifest(feature_names=("a", "b")))
    served = load_artefact(tmp_path)
    with pytest.raises(ValueError, match="feature"):
        served.predict(tiny_dataset.x, tiny_dataset.mask)


def test_serving_does_not_import_torch(tmp_path, tiny_dataset):
    """torch is a training dependency and must stay out of production."""
    import sys
    save_artefact(tmp_path, _fitted_model(tiny_dataset), _manifest())
    for module in [m for m in sys.modules if m.startswith("torch")]:
        del sys.modules[module]
    served = load_artefact(tmp_path)
    served.predict(tiny_dataset.x, tiny_dataset.mask)
    assert not any(m.startswith("torch") for m in sys.modules), (
        "loading or serving an artefact imported torch"
    )
```

- [ ] **Step 2: Implement, run, commit**

---

### Task 7: Predictions table and serving

**Files:**
- Create: `alembic/versions/0008_predictions.py`
- Modify: `app/db/models.py`
- Create: `app/services/predictions.py`
- Test: `tests/test_predictions.py`

**Interfaces:**
- Produces: `Prediction` model with `player_code`, `player_id`, `season`, `gameweek`, `fixture_id`, `horizon`, `expected_points`, `floor`, `median`, `ceiling`, `expected_minutes`, `start_probability`, `confidence`, `model_name`, `model_version`, `feature_version`, `information_state`, `created_at`; `generate_predictions(db, settings, served) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
def test_predictions_record_the_model_that_made_them(...):
    """A number without provenance cannot be audited later."""


def test_no_artefact_yields_not_ready_rather_than_zero(...):
    result = generate_predictions(db, settings, served=None)
    assert result["readiness"] == "not_ready"
    assert result["reason"]
    assert result["written"] == 0


def test_distribution_is_ordered_when_stored(...):
    assert all(p.floor <= p.median <= p.ceiling for p in stored)
```

- [ ] **Step 2: Register `predictions` in the readiness registry**

Add to `FEATURES` in `app/services/season_state.py` so an absent model surfaces
as `not_ready` with an activation condition, exactly as projections do now.

- [ ] **Step 3: Implement, run the full suite, commit**

---

### Task 8: Training entrypoint and documentation

**Files:**
- Create: `scripts/train.py`
- Modify: `docs/MODEL_EVALUATION.md`, `README.md`, `ARCHITECTURE.md`, `docs/METRICS.md`

- [ ] **Step 1: Write `scripts/train.py`**

Trains on all seasons up to a cutoff, evaluates against the gate, refuses to
write an artefact that does not clear it unless `--force` is passed, and prints
the comparison either way.

- [ ] **Step 2: Document**

How to train, where artefacts live, how to roll back to a previous version, and
what each stored prediction field means.

- [ ] **Step 3: Commit**

---

## Self-review notes

Spec coverage against `2026-08-03-prediction-model-design.md`:

- Layer C two-stage decomposition → Task 4. Minutes head, event head, composition.
- Masked features → Tasks 1 and 4; the network multiplies by the mask before the first layer and a test proves a masked value cannot change the output.
- Distributional output → Task 4 `predict_distribution`, stored in Task 7.
- Ship gate → Tasks 3 and 5, with thresholds copied verbatim from the recorded baselines.
- Layer D artefact, ONNX, no torch in production → Task 6, with an explicit test.
- Predictions table with provenance → Task 7.
- Readiness integration, never zero → Task 7 Step 2.
- Rollback → Task 8, by pointing at a previous artefact version.

Deliberate ordering: the gradient-boosted candidate (Task 3) runs before the
network (Task 4) because it is cheap and answers whether the features carry
signal. If it fails the preseason gate, the network inherits the same ceiling and
the next work is features, not architecture.

Type consistency: `Dataset` field names in Task 1 are used unchanged in Tasks 2,
4 and 6. `TrainableModel.predict_batch(x, mask)` has the same signature in Tasks
2, 4 and 6. `Manifest.feature_names` in Task 6 matches `Dataset.feature_names`
in Task 1.
