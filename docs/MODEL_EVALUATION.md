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

## Limitations

These are real and they bound what the numbers above can be read to mean.

1. **No fixture difficulty.** The archive does not carry an FDR, so the harness
   passes a neutral difficulty of 3 for every fixture. The two fixture-aware
   baselines therefore see only home/away, and their scores understate what
   fixture information is worth. Deriving an opponent-strength proxy from
   point-in-time goals scored and conceded would fix this and is the single
   highest-value improvement to the harness.
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
it beats:

- **In-season:** MAE below 1.0638 **and** Spearman above 0.6899.
- **Preseason:** MAE below 1.2862 **and** Spearman above 0.3066.

If it wins one state and loses the other, it ships only for the state it wins,
selected by information state at prediction time. If it loses both, the honest
outcome is that the heuristic stays and the model does not ship.

Reproduce with:

```bash
python -m app.cli import-archive
python -m app.cli evaluate --output docs/evaluation-baseline.json
```
