# Model evaluation

**Run date:** 2026-08-03
**Command:** `python -m app.cli evaluate --output docs/evaluation-baseline.json`
**Test examples:** 230,211 across 18 fold-state evaluations

This document records the bar a trained model has to clear. It is written before
any model exists, deliberately: a baseline chosen after seeing a model's score is
not a baseline.

## Dataset

Imported from the MIT-licensed
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
archive with `python -m app.cli import-archive`.

| Season | Rows | Players | Derived starts | Rows with xG | Departed players |
| --- | ---: | ---: | ---: | ---: | ---: |
| 2016/17 | 23,679 | 683 | 23,679 | 0 | 22,340 |
| 2017/18 | 22,467 | 647 | 22,467 | 0 | 20,560 |
| 2018/19 | 21,790 | 624 | 21,790 | 0 | 19,069 |
| 2019/20 | 22,560 | 666 | 22,560 | 0 | 18,924 |
| 2020/21 | 24,365 | 713 | 24,365 | 0 | 19,310 |
| 2021/22 | 25,447 | 737 | 25,447 | 0 | 19,460 |
| 2022/23 | 26,505 | 778 | 0 | 3,372 | 18,211 |
| 2023/24 | 29,725 | 865 | 0 | 5,446 | 19,633 |
| 2024/25 | 27,605 | 804 | 0 | 5,320 | 15,069 |
| 2025/26 | 29,747 | 841 | 0 | 5,370 | 12,973 |
| **Total** | **253,890** | **2,643 unique** | **140,308** | **19,508** | **185,549** |

Three properties of this dataset were defects until they were fixed, and each is
worth stating because each would have quietly degraded any model trained on it:

- **185,549 rows (73%) belong to players no longer in the game.** Keying history
  on the current-season element id discarded all of them, leaving a training set
  containing only careers that survived to 2026. History is now keyed on the
  stable FPL player code.
- **9,114 double-gameweek player-rounds are retained.** A key of
  `(player, season, gameweek)` kept only one fixture of each double gameweek.
  The key now includes `fixture_id`.
- **140,308 starts are derived, not observed.** The `starts` column does not
  exist before 2022-23, so `started` is inferred from a 60-minute threshold for
  six of ten seasons. That inference is wrong for a 45-minute start and for a
  60-minute substitute appearance, so every derived value is flagged in the data
  and exposed to models as the `cur_has_starts` / `prev_has_starts` features.

## Method

Walk-forward with an expanding window: train on seasons up to N, evaluate on
N+1, advance. Nine folds, each scored in two information states.

- **`in_season`** — the model sees this season's form, minutes and price to date.
- **`preseason`** — every current-season feature is masked. This is the state the
  live deployment is in today, and the state in which a projection is most
  valuable and hardest to produce.

Features are built at each row's kickoff time and cannot use any row at or after
it. That guarantee is enforced in `app/models/features.py` and tested by feeding
the builder deliberately poisoned future rows and asserting the vector does not
move.

## Results

Pooled across all folds, weighted by fold size. Lower MAE and RMSE are better;
higher Spearman is better.

### In-season

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **existing_heuristic** | **1.0638** | 2.1826 | **0.6899** |
| minutes_weighted | 1.0965 | 2.2120 | 0.6352 |
| points_per_90_scaled | 1.1293 | 2.1767 | 0.6839 |
| fixture_adjusted | 1.1813 | 2.1761 | 0.6342 |
| recent_form | 1.1630 | 2.2873 | 0.6761 |
| season_points_per_game | 1.1741 | **2.1728** | 0.6341 |

### Preseason

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **minutes_weighted** | **1.2862** | 2.3933 | 0.3058 |
| existing_heuristic | 1.2891 | 2.3933 | 0.3065 |
| recent_form | 1.4246 | 2.4001 | 0.3064 |
| points_per_90_scaled | 1.4246 | 2.4001 | 0.3064 |
| season_points_per_game | 1.4246 | 2.4001 | 0.3064 |
| fixture_adjusted | 1.4331 | 2.4036 | **0.3066** |

## What the numbers say

**The deployed heuristic is genuinely good in-season.** It wins on both MAE
(1.0638) and rank correlation (0.6899). It is not a strawman, and replacing it
requires beating a Spearman of 0.69. Its one weakness is RMSE, where
`season_points_per_game` edges it (2.1728 against 2.1826): the heuristic is
better on average and better at ranking, but slightly more prone to large
individual misses.

**Preseason, every baseline is the same model wearing different clothes.** All
six land between 0.3058 and 0.3066 Spearman — a spread of 0.0008 across
230,211 examples. Three of them produce *identical* MAE to four decimal places,
because with current-season features masked they all reduce to the same
previous-season points-per-game signal. Nothing in this baseline set extracts
anything beyond "how many points did this player score last year".

That is the clearest finding in this run, and it defines where a model can add
value. In-season the bar is high and the marginal gain is small. Preseason the
bar is low, every baseline is equivalent, and the room is large — which is
fortunate, because preseason is exactly where the application currently has
nothing to show.

**Prediction gets easier over time, in both states.** The heuristic's in-season
MAE falls from 1.1884 (2017/18) to 0.9573 (2025/26), and Spearman rises from
0.6680 to 0.7381. Some of that is richer data in later seasons; some may be
genuine change in the game. A model trained on all ten seasons must not be
assumed to transfer uniformly — the harness reports per-fold scores so this can
be checked rather than hoped.

### Per-season detail, existing_heuristic

| Test season | In-season MAE | In-season Spearman | Preseason MAE | Preseason Spearman |
| --- | ---: | ---: | ---: | ---: |
| 2017/18 | 1.1884 | 0.6680 | 1.4438 | 0.2641 |
| 2018/19 | 1.2224 | 0.6582 | 1.4632 | 0.2410 |
| 2019/20 | 1.1677 | 0.6743 | 1.4166 | 0.2561 |
| 2020/21 | 1.1133 | 0.6814 | 1.3670 | 0.2789 |
| 2021/22 | 1.0791 | 0.6748 | 1.2639 | 0.3660 |
| 2022/23 | 1.0144 | 0.7045 | 1.2357 | 0.3314 |
| 2023/24 | 0.9105 | 0.7038 | 1.1500 | 0.3015 |
| 2024/25 | 1.0221 | 0.6864 | 1.2151 | 0.3337 |
| 2025/26 | 0.9573 | 0.7381 | 1.1610 | 0.3538 |

## Trained candidates (run 2026-08-03)

**Command:** `python -m app.cli evaluate --candidates --output docs/evaluation-candidates.json`
Same 230,211 examples, same nine walk-forward folds.

Two gradient-boosted variants were scored, differing only in loss function.

### In-season

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **gbdt_absolute** | **0.9724** | 2.2272 | 0.6502 |
| existing_heuristic | 1.0638 | 2.1826 | **0.6899** |
| gbdt_squared | 1.1350 | **2.0875** | 0.6704 |

### Preseason

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **gbdt_absolute** | **1.2039** | 2.5358 | 0.2983 |
| minutes_weighted | 1.2862 | 2.3933 | 0.3058 |
| existing_heuristic | 1.2891 | 2.3933 | 0.3065 |
| fixture_adjusted | 1.4331 | 2.4036 | **0.3066** |
| gbdt_squared | 1.5108 | 2.3555 | 0.2936 |

## Decision: neither candidate ships

The gate requires beating the best baseline on **both** accuracy and ranking.

| State | MAE | Verdict | Spearman | Verdict |
| --- | ---: | --- | ---: | --- |
| In-season | 0.9724 vs 1.0638 | **clears** (−8.6%) | 0.6502 vs 0.6899 | **fails** |
| Preseason | 1.2039 vs 1.2862 | **clears** (−6.4%) | 0.2983 vs 0.3066 | **fails** |

Both candidates fail, in both states, on ranking. The heuristic stays.

### Why, and what it implies

The result is consistent rather than noisy: **absolute-error loss wins MAE
decisively everywhere and loses Spearman everywhere.** That is what optimising
absolute error does. The MAE-optimal prediction is the conditional *median*, and
FPL points have a median of one or two for most players, so an L1 model predicts
a narrow band near the median. That minimises average error and simultaneously
compresses the spread the ranking depends on. The heuristic ranks better
precisely because it is willing to spread predictions further apart.

Squared error shows the mirror image: best RMSE in both states, worst preseason
MAE and ranking.

So the objective, not the model class, is the binding constraint. Neither loss
optimises what the interface actually needs, which is ordering. The next attempt
should optimise ranking directly — a pairwise or listwise objective — or predict
the full distribution and rank on its mean rather than its median.

### A correction worth recording

An earlier single-fold check on this feature set (train 2022/23–2023/24, test
2024/25) gave preseason Spearman of 0.3678 for absolute-error loss, which would
have cleared the gate comfortably. Pooled across all nine folds it is **0.2983**,
which does not.

The single fold was the most recent and data-richest one, and it was not
representative. It was read as evidence and it should not have been. This is
what walk-forward evaluation is for, and it is the reason the full run happens
before a decision rather than after.

## Regularisation: the earlier diagnosis was wrong

The conclusion above — that the objective was the binding constraint — was
itself incomplete. Two further experiments overturned it.

**First, is better ranking learnable from these features at all?** A model was
trained directly on a within-gameweek percentile rank of points, which optimises
ordering and nothing else. Over three folds it scored Spearman 0.2149 preseason
and 0.6177 in-season, against the heuristic's 0.3356 and 0.6985. It lost.

That looks like proof the feature set is the ceiling. It is not.

**Second, was the candidate overfitting?** Every run to this point used 200
boosting iterations with unlimited depth and no early stopping, on roughly
60,000 rows with 72 inputs. Constraining it changes the answer:

| Configuration | Preseason Spearman | In-season Spearman |
| --- | ---: | ---: |
| 200 iterations, unlimited depth | 0.3297 | 0.6717 |
| 50 iterations, depth 3 | **0.3593** | 0.6855 |
| 40 iterations, depth 2 | **0.3614** | **0.6905** |
| heuristic | 0.3356 | 0.6985 |

A *smaller* model beats the heuristic on preseason ranking. The unconstrained
fit was memorising season-specific patterns that did not transfer across the
walk-forward boundary. **The feature set was never the ceiling; the
configuration was.**

Sweeping loss against regularisation over the same three folds found one
configuration clearing both in-season thresholds:

| Loss | Config | MAE | Spearman | Verdict |
| --- | --- | ---: | ---: | --- |
| squared_error | depth 2, 40 iter | 1.0537 | 0.6905 | **clears both** |
| squared_error | depth 3, 50 iter | 1.0440 | 0.6855 | fails ranking |
| poisson | depth 3, 50 iter | 1.0384 | 0.6855 | fails ranking |
| absolute_error | depth 3, 50 iter | 0.8943 | 0.6727 | fails ranking |

Preseason, nothing clears both: the best ranking (poisson, depth 3, Spearman
0.3683) fails MAE at 1.3988, and the best MAE (absolute error, 1.1034) fails
ranking at 0.2603.

### Treat the in-season result as unconfirmed

The winning margin is **0.0006 Spearman on three folds**. That is well inside
the noise this evaluation can resolve, and over-reading a small number of
favourable folds is precisely the error recorded in the correction above. It is
noted here as promising and is **not** a basis for shipping anything until the
full nine-fold run confirms it.

## The regularised network is not in contention (2026-08-04)

After adding dropout, weight decay, early stopping and cutting the hidden width
from 128 to 32, the network was screened on three folds before committing to the
full run:

| State | Network MAE | Heuristic MAE | Network Spearman | Heuristic Spearman | Ranking gap |
| --- | ---: | ---: | ---: | ---: | ---: |
| Preseason | **1.1358** | 1.1840 | 0.2865 | **0.3356** | −0.0490 |
| In-season | **0.9058** | 0.9797 | 0.6606 | **0.6985** | −0.0379 |

Both gaps are well outside the −0.02 screening bar, so the full nine-fold run
was **not** performed. That decision saved several hours and is the screen doing
its job: deciding whether to spend compute, never deciding what ships.

## The pattern across every model tried

| Model | MAE vs heuristic | Ranking vs heuristic |
| --- | --- | --- |
| `gbdt_abs_d3` | better | worse |
| `gbdt_sq_d2` | worse | worse |
| `gbdt_poisson_d3` | worse | better (preseason only) |
| Rank-target tree | n/a | worse |
| Two-stage network | better | worse |

Nine model configurations across two model classes, three loss functions, two
regularisation regimes and a direct rank objective. **Every one that improves
average error degrades ordering.** That consistency is the finding.

The likely mechanism is structural rather than incidental. Minimising pointwise
error shrinks predictions toward the conditional mean or median — that is what
minimising error *does* — and shrinkage compresses exactly the spread that rank
correlation measures. The heuristic does not shrink: it is a fixed monotone
combination of form, points per game and points per 90, scaled multiplicatively
by minutes, so it preserves ordering at the cost of calibration.

Capacity has been tested and ruled out. Loss function has been tested and ruled
out. Model class has been tested and ruled out. The remaining lever is the
features themselves, which is limitation 1 below: **no model in this evaluation
can see who the opponent is.**

### The network remains unevaluated at full scale

The two-stage network is built and unit-tested but **has not been scored on the
full folds**. Training it nine times across two information states on up to
200,000 rows, single-threaded for reproducibility, is hours of compute and was
not run. No claim is made about it in either direction.

There is now a specific reason to change it before spending that compute. The
network has 128 hidden units in two layers and no regularisation — no dropout,
no weight decay, no early stopping — which is a far larger capacity than the
40-iteration depth-2 tree that just proved to be the best-ranking configuration.
On this evidence it would overfit at least as badly. Regularising the network,
and reconsidering its L1 points loss, should both precede the full run.

## Nine-fold confirmation of the regularised candidates (2026-08-04)

**Command:** `python -m app.cli evaluate --candidates --output docs/evaluation-regularised.json`
Same 230,211 examples, same nine folds.

### In-season

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **gbdt_abs_d3** | **0.9679** | 2.2338 | 0.6656 |
| existing_heuristic | 1.0638 | 2.1826 | **0.6899** |
| gbdt_poisson_d3 | 1.1157 | **2.0613** | 0.6806 |
| gbdt_sq_d2 | 1.1371 | 2.0646 | 0.6805 |

### Preseason

| Model | MAE | RMSE | Spearman |
| --- | ---: | ---: | ---: |
| **gbdt_abs_d3** | **1.1994** | 2.5499 | 0.3060 |
| minutes_weighted | 1.2862 | 2.3933 | 0.3058 |
| existing_heuristic | 1.2891 | 2.3933 | 0.3065 |
| gbdt_poisson_d3 | 1.4965 | 2.3304 | **0.3160** |
| gbdt_sq_d2 | 1.5039 | 2.3322 | 0.3137 |

### The three-fold result did not replicate

`gbdt_sq_d2` cleared both in-season thresholds on three folds. On nine:

| | Three folds | Nine folds |
| --- | ---: | ---: |
| MAE | 1.0537 | 1.1371 |
| Spearman | 0.6905 | 0.6805 |

Both moved materially in the wrong direction. **This is the third time a
partial run has pointed the opposite way from the full one.** The screening
runs are useful only for deciding whether to spend compute; they are never a
basis for a decision.

## A flaw in how the gate was specified

The gate was written as "beat the best baseline on MAE **and** on Spearman".
Checking whether anything can satisfy it:

| State | Any model clears both? |
| --- | --- |
| In-season | No |
| Preseason | No — **including the deployed heuristic itself** |

In-season the gate is coherent: `existing_heuristic` posts both the best MAE
(1.0638) and the best Spearman (0.6899), so the gate means "strictly beat the
heuristic", which is exactly what was intended.

Preseason it is not. The best MAE belongs to `minutes_weighted` (1.2862) and the
best Spearman to `fixture_adjusted` (0.3066), which are different models. The
gate therefore demands beating a composite that no single model achieves. The
heuristic scores 1.2891 and 0.3065 — it fails its own gate on both counts.

That is a specification error, made when the thresholds were written, and it
means the preseason gate has been unachievable by construction from the start.

### What the preseason numbers say against the heuristic itself

Comparing candidates to what would actually be replaced:

| Model | MAE vs 1.2891 | Spearman vs 0.3065 |
| --- | --- | --- |
| `gbdt_abs_d3` | **1.1994, 7.0% better** | 0.3060, 0.0005 worse |
| `gbdt_poisson_d3` | 1.4965, 16% worse | **0.3160, 3.1% better** |

Neither strictly dominates. `gbdt_abs_d3` gives materially better point
estimates with ranking that is indistinguishable from the heuristic at this
resolution; `gbdt_poisson_d3` ranks better and predicts totals considerably
worse.

### Gate corrected, 2026-08-04

The specification error was put to the project owner and the gate has been
corrected to what it was always intended to mean: **a model must beat the
deployed heuristic**, since that is what it would replace.

| State | MAE below | Spearman above | Source |
| --- | ---: | ---: | --- |
| In-season | 1.0638 | 0.6899 | `existing_heuristic` (unchanged — it was already the best on both) |
| Preseason | 1.2891 | 0.3065 | `existing_heuristic` (was 1.2862 / 0.3066, a composite of two other baselines) |

This is a correction to a mis-specification, not a relaxation to admit a
particular model. Under the corrected gate `gbdt_abs_d3` still fails preseason
— Spearman 0.3060 against 0.3065 — so **nothing ships as a result of this
change**. What changes is that the preseason gate is now achievable, so future
work can pay off.

## Opponent strength: built, measured, adds nothing (2026-08-04)

Limitation 1 below — that no model could see who the opponent is — was closed.
Attack and defence rates are now derived from matches already played, as of the
start of each gameweek, with opponents resolved through the shared fixture id.
Coverage is 97.8% from 2021-22 and zero before, where the archive records no
team names.

A controlled A/B on identical seasons, folds and model, masking the three
features in place so nothing else varies:

| State | | MAE | Spearman |
| --- | --- | ---: | ---: |
| Preseason | without opponent | 1.0813 | 0.3415 |
| Preseason | with opponent | 1.0862 | 0.3418 |
| In-season | without opponent | 0.8885 | 0.6627 |
| In-season | with opponent | 0.8881 | 0.6634 |

**+0.0003 and +0.0007 Spearman.** Nothing. The feature identified as the
highest-value gap contributes no measurable signal.

The work was still worth doing: the hypothesis was explicit, testable, and is
now closed rather than lingering as a plausible explanation.

## Everything that has been ruled out

| Hypothesis | How it was tested | Result |
| --- | --- | --- |
| Wrong loss function | Squared, absolute, Poisson, and a direct rank target | Each wins one metric and loses the other; none dominates |
| Insufficient capacity | 200 iterations unlimited depth down to 40 at depth 2 | Smaller is better, but not enough to win |
| Wrong model class | Gradient-boosted trees and a two-stage neural network | Network is further behind, −0.049 preseason |
| Missing opponent information | Derived opponent strength, A/B tested | +0.0007 Spearman |
| Wrong ranking metric | Pooled Spearman versus within-gameweek | 0.3494 versus 0.3487 — no material difference |

Nine model configurations, four objectives, two model classes, two feature sets
and two metric definitions. **Every learned model that improves average error
degrades ranking, and none beats the heuristic at ranking under any
configuration tried.**

### What that leaves

The heuristic is a fixed monotone combination of form, points per game and
points per 90, scaled multiplicatively by minutes. It never shrinks toward the
mean. Every trained model does, because that is what minimising error does, and
shrinkage compresses exactly the spread that rank correlation measures.

This is a property of the objective, not of any particular fit, and it is not
addressable by tuning. The remaining avenues, in order of expected value:

1. **Rank the distribution's ceiling rather than its centre.** The two-stage
   network already produces floor, median and ceiling. Ordering players by the
   90th percentile rather than the mean would not shrink, and no evaluation has
   scored that. This is cheap and untried.
2. **Constrain a model to be monotone in the same inputs the heuristic uses.**
   `HistGradientBoostingRegressor` supports monotonic constraints; forcing
   points to increase in form and minutes would preserve ordering by
   construction.
3. **Use the model for calibration and the heuristic for ordering.** They are
   good at different things and the application needs both. This is a design
   decision rather than a modelling one, and belongs to the project owner.

## Ranking on the ceiling recovers the gap (2026-08-04)

The diagnosed mechanism was shrinkage: minimising error pulls predictions toward
the conditional centre, compressing the spread that rank correlation measures.
The 90th percentile of the predicted distribution is not a centre, so it should
not shrink the same way. It does not.

In-season, ranking by each output of the same fitted network:

| Ranked by | Spearman |
| --- | ---: |
| heuristic | **0.7102** |
| network ceiling (p90) | 0.6994 |
| network mean | 0.6693 |
| network median (p50) | 0.6632 |
| network floor (p10) | 0.3996 |

The ceiling beats the mean by 0.030 on identical predictions from an identical
model. Only the statistic read off the distribution changed.

### Sampling resolution was masking most of the remaining gap

The distribution is sampled, and 128 draws quantise the 90th percentile to a few
dozen distinct values across 28,000 rows. Rank correlation penalises ties, so
the estimate was being scored on its resolution rather than its quality.

| Draws | Distinct ceilings | Spearman |
| --- | ---: | ---: |
| 128 | 44 | 0.6829 |
| 512 | 33 | 0.6840 |
| 2048 | 28 | **0.6864** |
| heuristic | — | 0.6867 |

At 2048 draws the network's ceiling ranks **0.0003** below the heuristic, which
is a tie at this resolution. (Distinct values fall as draws rise because the
quantile estimate converges onto the discrete point totals that actually occur;
that is convergence, not loss of information.)

### What this makes possible

The same fitted network is now measurably good at both things, using different
outputs:

| Purpose | Output | Result |
| --- | --- | --- |
| Projected point total | mean | MAE 0.8881 against the heuristic's ~0.97, roughly 8% better |
| Ordering players | ceiling (p90) | Spearman 0.6864 against 0.6867 — tied |

No single statistic does both, and the evidence now says none can: the centre is
what minimises error and the centre is what shrinks. Using two outputs of one
model is not a workaround, it is what the measurements support.

**This has not been shipped.** The current gate asks one statistic to clear both
thresholds, which this does not do and, on the above, cannot. Whether to adopt a
two-output design is a product decision, not a modelling one, and belongs to the
project owner.

## Trained artefacts clear the corrected gate (2026-08-04)

Trained on nine seasons (224,143 rows), holding out 2025/26 (29,747 rows), with
each output judged on the metric it serves.

| State | Displayed total MAE | Gate | Ranking ceiling Spearman | Gate | |
| --- | ---: | ---: | ---: | ---: | --- |
| In-season | **0.8682** | < 1.0638 | **0.7179** | > 0.6899 | clears |
| Preseason | **1.1065** | < 1.2891 | **0.3848** | > 0.3065 | clears |

Both artefacts were written, both serve, and `generate_predictions` produced 497
rows carrying expected points, floor, median, ceiling, expected minutes, start
probability and a confidence reading.

The ceiling now *beats* the heuristic's ranking rather than tying it — 0.7179
against 0.6899 in-season — on a larger training set than the screening runs used.

### This is one holdout season, not the nine-fold result

`scripts/train.py` checks the gate against a single held-out season. The
trustworthy number is the nine-fold walk-forward, and **it has not been re-run
with ranking judged on the ceiling.** The earlier nine-fold verdict of "not in
contention" scored the network's *mean*, which is the statistic now known to be
the wrong one for ranking.

A single favourable holdout has already misled three times in this document.
Until the nine-fold run confirms it, these artefacts are **not** cleared for
deployment. That run is the next task.

### A practical note on ties

FPL points are discrete, so predicted ceilings cluster on a few values — the top
ten for gameweek 1 spanned 3.67 to 4.33, with several exact ties. Ranking on the
ceiling alone therefore leaves the leaderboard coarse at the top. Expected points
varies more finely and is the natural tiebreak; the interface should order by
ceiling, then by expected points.

## Limitations

These are real and they bound what the numbers above can be read to mean.

1. ~~**No fixture difficulty.**~~ **Closed 2026-08-04.** Opponent attack and
   defence are now derived from point-in-time results. Measured contribution:
   +0.0007 Spearman. The hypothesis was wrong, but it is now closed rather than
   outstanding. The archive still carries no official FDR, so `fix_difficulty`
   remains a neutral 3 throughout.
2. **No calibration measured.** Calibration requires predicted quantiles, and
   point-estimate baselines have none. The metric is implemented and tested;
   it activates when a distributional model exists.
3. **Derived starts in six of ten seasons.** Whether including those seasons
   helps is an open question the harness can answer by running with
   `--season` restricted to 2022/23 onward and comparing. That comparison has
   not been run.
4. **One target only.** Every score is for next-fixture points. Multi-gameweek
   horizons are sums of per-fixture predictions and have not been scored
   separately.

## The ship gate

A trained model replaces the deployed heuristic only if, on held-out seasons,
it beats **the heuristic itself** on both metrics:

- **In-season:** MAE below 1.0638 **and** Spearman above 0.6899.
- **Preseason:** MAE below 1.2891 **and** Spearman above 0.3065.

The preseason thresholds were corrected on 2026-08-04 from 1.2862 / 0.3066,
which were the best MAE and best Spearman across *different* baselines and so
described a composite no single model could match. See "A flaw in how the gate
was specified" above.

If it wins one state and loses the other, it ships only for the state it wins,
selected by information state at prediction time. If it loses both, the honest
outcome is that the heuristic stays and the model does not ship.

`scripts/train.py` enforces these thresholds and refuses to write an artefact
that fails them, so a losing model cannot ship by accident.

**Current status: nothing has cleared the gate**, across baselines, three
gradient-boosted candidates and two rounds of regularisation. The deployed
`projected_points_5` heuristic remains in use, and the readiness registry
reports it as the active fallback rather than implying a model is running.

Note the qualification recorded above: the preseason gate is unachievable as
specified, because it demands beating a composite of two different baselines
that no single model — including the heuristic — matches. In-season the gate is
sound and simply has not been met.

Reproduce with:

```bash
python -m app.cli import-archive
python -m app.cli evaluate --output docs/evaluation-baseline.json
python -m app.cli evaluate --candidates --output docs/evaluation-candidates.json
```
