# Prediction Model Design

**Date:** 2026-08-03
**Status:** Approved
**Depends on:** `2026-08-02-trust-foundation-design.md` (season identity, metric contracts, readiness registry)

## Why this exists

Every projection-dependent feature in the application currently refuses to
produce a result, correctly, because there is nothing to project from. The
existing `projected_points_5` is a weighted blend of form, points per game and
points per 90, scaled by expected minutes and fixture difficulty. It is
transparent and it is not a model: it has no training, no evaluation, no
uncertainty, and no way to know whether it is any good.

This increment replaces it with a trained, evaluated, distributional model, and
— equally important — builds the harness that can tell whether the replacement
is actually better.

## Decisions taken

| Decision | Choice | Consequence |
| --- | --- | --- |
| Training data | Import the `vaastav/Fantasy-Premier-League` archive (MIT licensed) | Per-gameweek labels available immediately, back to 2016-17 |
| Compute | Train offline, serve an artefact | PyTorch never enters the production image |
| Decomposition | Two-stage: minutes, then events | Expected minutes and start probability become first-class outputs |
| Serving runtime | ONNX via `onnxruntime` | ~50MB in production instead of ~800MB |
| Ship gate | Must beat the heuristic and a GBDT baseline on held-out seasons | A worse model cannot ship silently |

## The central idea: one masked model, not three

Framing preseason projection as "predict a season total" yields roughly seven
season-transitions × 600 players ≈ 4,200 examples. That is far too few to train
a network, and it is why preseason initially looks intractable.

Framing it as "predict each individual fixture, using only the features known at
prediction time" yields the same archive as roughly 600 players × 38 gameweeks ×
N seasons ≈ 10⁵ examples, with identical output shape.

So there is one model, and an explicit **feature availability mask** describing
what is known when the prediction is made:

```
preseason      mask hides every current-season feature
after GW6      mask reveals six gameweeks of form, minutes and role
after GW20     mask reveals twenty
```

Training randomises the mask across examples, so the network learns to predict
under any information state, including the empty one. Prediction horizons are
then sums over per-fixture distributions: next gameweek, next four, next six and
rest of season need no separate models.

The mask serves a second purpose. The archive has three schema eras, so the same
mechanism hides features that a given season never recorded.

## Archive reality

Verified against the live repository on 2026-08-03. This is the constraint the
ingestion layer must be built around.

| Seasons | Layout | `starts` | `expected_goals` / `expected_assists` |
| --- | --- | --- | --- |
| 2016-17 (and 2017-18) | Quoted headers, 56 columns, legacy detail stats (`attempted_passes`, `dribbles`, `ea_index`) | No | No |
| 2018-19 to 2021-22 | Modern layout, 40-ish columns | No | No |
| 2022-23 to 2025-26 | Modern layout, 49 columns | Yes | Yes |

Consequences:

- Full-feature training data covers **four seasons**, roughly 90,000
  player-gameweek rows.
- Core-feature data (minutes, points, price, ownership, opponent, home/away)
  covers **ten seasons**, roughly 200,000 rows.
- Where `starts` is absent it must be **derived** as `minutes >= 60`, which is
  wrong for a 45-minute start and for a 60-minute substitute appearance. Derived
  starts are flagged in the data so the evaluation harness can measure whether
  including those seasons helps or hurts. The harness decides; the spec does
  not guess.

## Architecture

### Layer A — data and features

Archive rows land in the existing `gameweek_history` table. It already carries
`season` and a `(player_id, season, gameweek)` unique key from migration 0004,
so no new table is needed. Columns absent from the current model are added.

`app/services/archive_import.py` owns ingestion: per-season column mapping,
player identity resolution, idempotency, and explicit season tagging. It never
infers a season.

`app/models/features.py` owns the point-in-time feature builder. Its single
contract is: **given a prediction time T, no feature may use information from
after T.** Season aggregates, rolling form, minutes history and role signals are
all computed from rows strictly before T. This is where a leak would do the most
damage and be least visible, so it is a pure function with adversarial tests.

Each feature vector carries its availability mask.

### Layer B — evaluation

`app/models/evaluation.py` implements walk-forward backtesting: train on seasons
up to N, evaluate on N+1, advance. Never train on the future.

Baselines, all of which the network must beat:

1. Season points per game
2. Recent-form average
3. Points per 90 scaled by expected minutes
4. Fixture-adjusted average
5. The existing `projected_points_5` heuristic
6. A gradient-boosted tree on the same features

Metrics: mean absolute error, root mean squared error, Spearman rank
correlation, calibration of the predicted distribution, and log-likelihood.
Reported per position, per horizon, and per information state, because a model
can be good in aggregate and useless preseason.

### Layer C — the network

Two heads over shared encoders. Embeddings for player, team, opponent and
position; continuous features standardised with statistics stored in the
artefact.

- **Minutes head** — categorical over `{start, substitute, unused}`, plus a
  minutes distribution conditional on each. Yields start probability and
  expected minutes directly.
- **Event head** — per-90 rates conditional on minutes: goals, assists, saves
  and bonus as count distributions, clean sheet and cards as probabilities.
- **Composition** — sample the minutes head, then the event head conditional on
  the sampled minutes, then apply the FPL scoring rules for the player's
  position. The result is a points distribution, from which floor (10th
  percentile), median, ceiling (90th) and a prediction interval are read.

Composition applies the scoring rules from configuration rather than constants,
so a scoring change is a configuration change.

### Layer D — serving

Training exports ONNX plus a manifest: feature specification, normalisation
statistics, training cutoff, season coverage, metric results and model version.
The manifest is the contract; a prediction cannot be produced without one.

`app/services/predictions.py` loads the artefact and writes to a `predictions`
table carrying model version, horizon, distribution, expected minutes, start
probability, feature attributions and the prediction timestamp.

Predictions integrate with the existing metric-status and readiness machinery.
A prediction that cannot be produced is `not_ready` with a reason. It is never
zero. Rollback is pointing at a previous artefact version.

## What could go wrong, and how it is caught

**Leakage.** The most likely failure and the hardest to see. Mitigated by making
the feature builder a pure function of rows strictly before T, and by tests that
deliberately offer it future rows and assert they are not used.

**The network is worse than the heuristic.** Entirely possible on tabular data
at this scale. This is why Layer B is built before Layer C. If the network loses
on held-out seasons, the honest outcome is to ship the gradient-boosted baseline
and keep the network as a candidate.

**Derived starts poison the minutes head.** Six of ten seasons have no `starts`
column. The harness measures models trained with and without those seasons and
the result decides inclusion.

**Distribution shift.** Football in 2016-17 is not football in 2026-27. Walk-
forward evaluation measures this directly rather than assuming recency helps.

## Scope boundary

In scope: Layers A and B, then C and D under a second plan once B has reported
on data quality.

Explicitly out of scope: elite cohorts, live gameweek scoring, Monte Carlo rank
simulation, transfer planning, chips, the AI analyst. Those consume predictions
and are separate increments.

## Success criteria

1. The archive ingests reproducibly, tagged by season, with era differences
   recorded rather than silently coerced.
2. The feature builder provably cannot see the future.
3. The evaluation harness reports every baseline on held-out seasons.
4. A model ships only if it beats every baseline on accuracy and calibration.
5. Predictions carry distribution, uncertainty, model version and attributions.
6. An unavailable prediction is `not_ready`, never zero.
