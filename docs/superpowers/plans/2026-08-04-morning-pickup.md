# Morning Pickup Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Determine whether a trained model can honestly replace the deployed heuristic, and either ship it or record why not — starting from a confirmed nine-fold result rather than a promising three-fold one.

**Architecture:** The data, feature, evaluation, artefact and serving layers are all built and tested. What remains is empirical: confirm a result, regularise the network before spending compute on it, and act on whichever outcome the evidence supports.

**Tech Stack:** Python 3.12, numpy 2.2, scikit-learn 1.7, PyTorch 2.7 (training only), ONNX / onnxruntime (serving), SQLAlchemy 2, Alembic, pytest.

## Where things stand

| | State |
| --- | --- |
| Branch | `feat/railway-fpl-value-studio` at `8f416c2`, pushed |
| Tests | **224 passing** |
| Working tree | Clean |
| Production | Healthy, secure, **unchanged** — model migrations `0005`–`0008` deliberately unapplied |
| Local database | `data/local.db`, 253,890 archive rows across ten seasons |
| Deadline | **21 August 2026** — first gameweek deadline |

Read these two documents before starting anything:

- `docs/MODEL_EVALUATION.md` — every measurement taken, including two conclusions that were later overturned
- `docs/superpowers/specs/2026-08-03-prediction-model-design.md` — the design and the ship gate

## Global Constraints

- Run tests with `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`. An installed `dash` pytest plugin crashes collection on Windows without it.
- Point the CLI at the local training database: `DATABASE_URL="sqlite:///./data/local.db"`.
- The ship gate, copied verbatim from `docs/MODEL_EVALUATION.md`:
  - In-season: MAE below **1.0638** and Spearman above **0.6899**
  - Preseason: MAE below **1.2862** and Spearman above **0.3066**
- A model must clear **both** thresholds in a state to ship for that state.
- **Never conclude from fewer than the full nine folds.** This has produced a wrong answer twice already; see "Lessons already paid for" below.
- `torch` must never be imported by anything the web application loads. `tests/test_artefact.py::test_serving_does_not_import_torch` enforces it in a subprocess.
- Training is reproducible: fixed seed, single-threaded, seed recorded in the artefact manifest.
- A prediction that cannot be produced is `not_ready` with a reason. Never zero.

## Lessons already paid for

Three mistakes were made yesterday and corrected. Repeating them costs a day each.

1. **A single fold is not evidence.** Absolute-error loss scored preseason Spearman 0.3678 on one fold — clearing the gate — and 0.2983 pooled over nine. The single fold was the most recent and data-richest, and it was not representative.
2. **Check capacity before blaming features.** A model trained directly on a rank target lost to the heuristic, which looked like proof the feature set was the ceiling. It was not: constraining the model from 200 iterations at unlimited depth to 40 at depth 2 raised preseason Spearman from 0.3297 to 0.3614, past the heuristic's 0.3356.
3. **An intermittent test failure is a real failure.** A reproducibility test passed on retry. The cause was multi-threaded CPU reductions summing in nondeterministic order, so the same seed produced different weights. Training now runs single-threaded.

---

### Task 1: Confirm the regularised candidate on nine folds

**This is the first thing to run in the morning, before anything else.** It takes roughly 30–45 minutes unattended and every subsequent decision depends on its answer.

**Files:**
- Modify: `docs/MODEL_EVALUATION.md`

**Interfaces:**
- Consumes: `GradientBoostedCandidate(loss, max_depth, max_iter, name)` from `app.models.candidates`; the three candidates already registered in `app/cli.py`.
- Produces: `docs/evaluation-regularised.json`, and a recorded decision.

- [ ] **Step 1: Start the run**

```bash
cd w:/development-directory/projects/fpl_value_studio_railway
DATABASE_URL="sqlite:///./data/local.db" python -m app.cli evaluate \
  --candidates --output docs/evaluation-regularised.json
```

This scores six baselines plus three regularised candidates (`gbdt_sq_d2`,
`gbdt_poisson_d3`, `gbdt_abs_d3`) across nine walk-forward folds in two
information states — 230,211 test examples.

- [ ] **Step 2: Compare each candidate against the gate**

The three-fold result to be confirmed or refuted:

| Candidate | State | MAE (3 folds) | Spearman (3 folds) |
| --- | --- | ---: | ---: |
| `gbdt_sq_d2` | in-season | 1.0537 | 0.6905 |
| `gbdt_poisson_d3` | preseason | 1.3988 | 0.3683 |
| `gbdt_abs_d3` | preseason | 1.1034 | 0.2603 |

`gbdt_sq_d2` cleared both in-season thresholds by **0.0006 Spearman**. That
margin is inside the noise this evaluation resolves. Expect it not to hold.

- [ ] **Step 3: Record the outcome in `docs/MODEL_EVALUATION.md`**

Add a "Regularised candidates, nine folds" section with MAE, RMSE and Spearman
for all three in both states, and state plainly which if any clears the gate.
If the three-fold result does not replicate, say so explicitly — that is the
third data point on why partial runs mislead, and it belongs in the record.

- [ ] **Step 4: Commit**

```bash
git add docs/
git commit -m "Confirm regularised candidates across nine folds"
```

**Decision point.** If a candidate clears both thresholds in a state, go to
Task 4 and ship it for that state. If none does, go to Task 2.

---

### Task 2: Regularise the network before evaluating it

Do this **only if Task 1 produced no winner**, and do it **before** any full
network evaluation. `NetworkCandidate` currently has 128 hidden units across two
layers with no dropout, no weight decay and no early stopping. A 40-iteration
depth-2 tree just beat a 200-iteration unlimited-depth one on the same data, so
the network as written will overfit at least as badly. Evaluating it first would
burn hours to reproduce a known failure.

**Files:**
- Modify: `app/models/network.py`
- Test: `tests/test_network.py`

**Interfaces:**
- Consumes: `Dataset` from `app.models.dataset`.
- Produces: `TwoStageNet(n_features, hidden=32, dropout=0.2)`; `NetworkCandidate(seed, epochs, hidden, dropout, weight_decay, patience, deterministic)`.

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_network.py
def test_dropout_is_active_in_training_and_inert_in_evaluation():
    """Dropout must not perturb a served prediction."""
    net = TwoStageNet(n_features=N_FEATURES, hidden=16, dropout=0.5)
    x, mask = torch.randn(4, N_FEATURES), torch.ones(4, N_FEATURES)

    net.eval()
    with torch.no_grad():
        first = net(x, mask)["appearance_logits"]
        second = net(x, mask)["appearance_logits"]
    assert torch.allclose(first, second), "eval-mode output is not deterministic"

    net.train()
    torch.manual_seed(1)
    a = net(x, mask)["appearance_logits"]
    torch.manual_seed(2)
    b = net(x, mask)["appearance_logits"]
    assert not torch.allclose(a, b), "dropout had no effect in training mode"


def test_the_default_network_is_small():
    """Capacity, not the loss function, was what beat the heuristic on ranking.
    A 40-iteration depth-2 tree outperformed a 200-iteration unlimited-depth
    one on identical data; the network default should reflect that."""
    model = NetworkCandidate()
    assert model.hidden <= 64
    assert model.dropout > 0.0
    assert model.weight_decay > 0.0


def test_early_stopping_halts_before_the_epoch_cap(tiny_dataset):
    model = NetworkCandidate(seed=17, epochs=200, patience=2, hidden=16)
    model.fit(tiny_dataset)
    assert model.epochs_run < 200, "training ignored early stopping"
```

- [ ] **Step 2: Run to verify it fails**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_network.py -q -p no:warnings
```

Expected: FAIL — `TwoStageNet.__init__` has no `dropout` parameter.

- [ ] **Step 3: Add regularisation to `TwoStageNet`**

```python
class TwoStageNet(nn.Module):
    def __init__(self, n_features: int, hidden: int = 32, dropout: float = 0.2) -> None:
        super().__init__()
        # Small by default. See docs/MODEL_EVALUATION.md: an unconstrained
        # gradient-boosted fit memorised the training seasons and lost to the
        # heuristic on ranking; constraining capacity reversed that.
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
```

`forward` is unchanged: it must still compute `gated = x * mask` before the
encoder, which is what makes the masked-feature guarantee structural.

- [ ] **Step 4: Add weight decay, early stopping and an epoch counter**

In `NetworkCandidate.__init__` add `hidden: int = 32`, `dropout: float = 0.2`,
`weight_decay: float = 1e-4`, `patience: int = 3`, and `self.epochs_run = 0`.

In `_fit`, pass `weight_decay=self.weight_decay` to `torch.optim.Adam`, hold out
the last 15% of rows as a validation split, evaluate validation L1 after each
epoch, and stop when it has not improved for `patience` epochs. Set
`self.epochs_run` to the number of epochs actually executed. Call
`self._net.train()` before each epoch and `self._net.eval()` before validating,
so dropout is active for training and inert for measurement.

- [ ] **Step 5: Run the network tests**

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest tests/test_network.py -q -p no:warnings
```

Expected: PASS. Then run the file five times — the reproducibility test was
intermittently failing before and stability is the property being verified.

- [ ] **Step 6: Commit**

```bash
git add app/models/network.py tests/test_network.py
git commit -m "Regularise the network before evaluating it"
```

---

### Task 3: Evaluate the network on nine folds

**Files:**
- Modify: `app/cli.py`, `docs/MODEL_EVALUATION.md`

**Interfaces:**
- Consumes: `NetworkCandidate` from `app.models.network`.

- [ ] **Step 1: Screen on three folds first**

The network costs far more than a tree, so confirm it is in contention before
committing to the full run. Adapt the screening script pattern already used
yesterday, with folds `(2020/21+2021/22 → 2022/23)`, `(+2022/23 → 2023/24)`,
`(+2023/24 → 2024/25)`.

If the network is not within roughly 0.02 Spearman of the heuristic on this
screen, stop. It will not clear the gate on nine folds, and the full run is
hours. Record that and go to Task 5.

- [ ] **Step 2: Register the network as a candidate**

In `command_evaluate` in `app/cli.py`, alongside the three tree candidates:

```python
        from app.models.network import NetworkCandidate

        trainables.append(NetworkCandidate(seed=17, epochs=30, name="two_stage_net"))
```

`NetworkCandidate` needs a `name` parameter added, defaulting to
`"two_stage_network"`, matching how `GradientBoostedCandidate` takes one.

- [ ] **Step 3: Run the full evaluation**

```bash
DATABASE_URL="sqlite:///./data/local.db" python -m app.cli evaluate \
  --candidates --output docs/evaluation-network.json
```

Expect hours. Run it in the background and do Task 5 meanwhile.

- [ ] **Step 4: Record the result and commit**

State plainly whether the network clears the gate in either state. If it does
not, that is a result: the heuristic stays, and the document says so.

---

### Task 4: Ship the winner, if there is one

Do this **only** when a candidate has cleared both thresholds in a state **on
nine folds**.

**Files:**
- Modify: `docs/MODEL_EVALUATION.md`, `README.md`

- [ ] **Step 1: Train and export the artefact**

```bash
python scripts/train.py --output models/preseason_v1 --state preseason
python scripts/train.py --output models/in_season_v1 --state in_season
```

`scripts/train.py` re-checks the gate on a held-out season and refuses to write
an artefact that fails. If it refuses, the nine-fold result and the holdout
disagree — investigate before using `--force`.

- [ ] **Step 2: Generate predictions locally**

```python
from app.db.session import SessionLocal
from app.models.artefact import load_artefact
from app.services.predictions import generate_predictions

served = load_artefact("models/preseason_v1")
with SessionLocal() as db:
    print(generate_predictions(db, "2026/27", 1, served, "preseason"))
```

Confirm `readiness == "ready"` and a sensible written count.

- [ ] **Step 3: Apply migrations and deploy**

```bash
git push origin feat/railway-fpl-value-studio
railway up --service web --detach
```

`scripts/start-web.sh` runs `alembic upgrade head`, applying `0005`–`0008`.
Watch the deploy logs for `Running upgrade 0007 -> 0008` before assuming the
predictions table exists.

- [ ] **Step 4: Verify in production**

```bash
B=https://web-production-f5979.up.railway.app
for p in /health / /my-team; do printf '%-12s %s\n' "$p" "$(curl -s -o /dev/null -w '%{http_code}' $B$p)"; done
curl -sL $B/ | grep -icE "Leif Heaney|Ol Dirty Ballard|183790"
```

Expect `200 200 303` and `0`. Anything else, roll back by redeploying the
previous artefact directory.

---

### Task 5: Validate Docker on linux-leif

Independent of everything above and the last unverified item from the Trust
Foundation work. Ten minutes, and it needs your Linux machine — the Docker
daemon is not running on the Windows box.

**Files:**
- Modify: `docs/FEATURE_GAP_REPORT.md`

- [ ] **Step 1: Build and start**

```bash
cd <repo> && docker compose up --build
```

- [ ] **Step 2: Verify the container**

```bash
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/health   # expect 200
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8000/         # expect 303
```

`docker-compose.yml` sets `ACCESS_MODE=private`, so `/` redirecting to login is
correct. Sign in with `admin` / `admin`.

- [ ] **Step 3: Confirm migrations ran inside the container**

```bash
docker compose logs web | grep -i "Running upgrade"
```

Expect the chain through `0007 -> 0008`.

- [ ] **Step 4: Record the result**

Update the "Blocked" entry in `docs/FEATURE_GAP_REPORT.md` to Complete, or
record the failure with its output.

---

### Task 6: If nothing clears the gate — improve the features

Reach this only when Tasks 1–3 have all failed on nine folds. At that point
capacity has been ruled out and the feature set genuinely is the constraint.

The highest-value gap is already identified as limitation 1 in
`docs/MODEL_EVALUATION.md`: **the archive carries no fixture difficulty**, so
`app/models/dataset.py:_Target` passes a neutral `difficulty = 3` for every
fixture. Every fixture-aware model and baseline is therefore blind to opponent
strength, which is one of the strongest signals in the sport.

- [ ] **Step 1: Derive opponent strength point-in-time**

For each `(season, team)` compute, from rows strictly before the fixture's
kickoff, goals scored and conceded per match. Attach the opponent's figures to
the target as `opponent_attack` and `opponent_defence`.

The point-in-time constraint is the whole difficulty. Computing it from
full-season totals would leak the future into every early-season row, and the
leakage tests in `tests/test_features.py` exist precisely to catch that.

- [ ] **Step 2: Add the features and bump the version**

Add `fix_opponent_attack` and `fix_opponent_defence` to `FEATURE_NAMES`, and
bump `VERSION` in `app/models/features.py`. The version bump matters: artefacts
record `feature_version`, and `app/services/predictions.py` refuses to serve a
model whose features no longer match.

- [ ] **Step 3: Re-run Task 1 and compare against this document's numbers**

---

## Recommended order for the morning

1. **Start Task 1 immediately** and leave it running — everything depends on it.
2. **Do Task 5 while it runs.** Independent, ten minutes, closes the last open item from the deployment work.
3. **Read the Task 1 result.** Winner → Task 4. No winner → Task 2, then Task 3.
4. **Task 6 only if 1–3 all fail.**

Expect roughly a full day if the network needs evaluating, or two hours if
Task 1 produces a winner.

## What must not be done

- Do not deploy migrations `0005`–`0008` until a model is worth serving. Production is healthy and there is no benefit to the schema change alone.
- Do not conclude from three folds. Screening is for deciding whether to spend compute, never for deciding what ships.
- Do not use `scripts/train.py --force` to work around a failing gate. If the gate fails, the model is worse than what it would replace.

## Self-review notes

Spec coverage against `2026-08-03-prediction-model-design.md`:

- Ship gate → Tasks 1, 3, 4, with thresholds copied verbatim.
- Two-stage network → Task 2 regularises it, Task 3 evaluates it.
- Artefact, ONNX, rollback → Task 4; the format and its tests are already built.
- Predictions table and readiness → Task 4 Step 2; built and tested already.
- Feature limitations → Task 6, addressing the recorded limitation 1.

Deliberate ordering: Task 2 precedes Task 3 because the network's capacity is
larger than the configuration that has already been shown to overfit on this
data, so evaluating it unregularised would spend hours confirming a known
failure mode.

Type consistency: `GradientBoostedCandidate(loss, max_depth, max_iter, name)`
in Task 1 matches the current signature. `NetworkCandidate` gains `hidden`,
`dropout`, `weight_decay`, `patience`, `epochs_run` in Task 2 and `name` in
Task 3, and Task 3 depends on Task 2 having added them. `generate_predictions(db,
season, gameweek, served, information_state)` in Task 4 matches
`app/services/predictions.py`.
