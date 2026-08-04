# Which sort actually surfaces better players?

Walk-forward over ten archive seasons, 2016/17–2025/26, nine evaluated folds.
Run with `scripts/evaluate_ranking.py`; raw output in `ranking-evaluation.json`.

## Method

A spreadsheet sort is a different question from a points projection, so it is
measured differently from `docs/MODEL_EVALUATION.md`:

- **Target: the next five gameweeks**, not the next fixture. Someone sorting
  the sheet is choosing who to own for a stretch. A gameweek with no row counts
  as zero — a player left out returns nothing, and that is the outcome the sort
  should have steered away from. Rows whose horizon runs past the end of the
  season are dropped rather than credited with a short window.
- **Rank correlation within each gameweek**, then pooled by cohort size. A sort
  compares players against each other at one moment. Pooling every row across a
  season measures how points drift over time, which no sort control can act on.
- **Candidates read the point-in-time feature vector** and honour its mask, so
  they inherit the no-leakage guarantee proven for `build_features`. A masked
  feature yields `None` and the candidate declines to score that player, rather
  than substituting a zero.

## Results

| Candidate | Spearman | Seasons won |
| --- | --- | --- |
| **points_per_game** | **0.7368** | **8 / 9** |
| minutes_mean *(control)* | 0.7229 | 1 / 9 |
| points_per_million | 0.7213 | 0 / 9 |
| points_per_game_shrunk | 0.7118 | 0 / 9 |
| form_last3 | 0.7111 | 0 / 9 |
| points_per_million_shrunk | 0.6400 | 0 / 9 |
| composite_with_form | 0.5906 | 0 / 9 |
| composite_quality_security | 0.5647 | 0 / 9 |
| minutes_weighted_points | 0.5594 | 0 / 9 |
| points_per_90 | 0.0270 | 0 / 9 |

## What this settles

**Points per game is the best sort available, and it is not close.** It wins
eight of nine seasons. It works because it blends quality and availability in
one number: it averages over gameweeks including the ones the player was
benched for, so a nailed-on average player correctly outranks a brilliant
substitute.

**Minutes alone is the second-best predictor.** A control candidate that knows
nothing about quality scores 0.7229. Over a five-gameweek horizon, whether
someone plays matters nearly as much as how good they are. Any future scoring
proposal that cannot beat "how many minutes does he play" is not worth shipping.

**Points per 90 is worthless as a sort — 0.027.** This is not a subtle result
and it is not a bug. At gameweek 20 of 2024/25 the top eight players by P/90
all averaged 0.1 minutes: one point in a one-minute cameo is a rate of 90.00
per 90. 174 of 496 players averaged under 20 minutes. The column is dominated
by cameos. It is the same defect class as the recommender audit — a rate with
no minutes floor — surviving in the display layer.

**Sample-size shrinkage makes ranking worse, not better.** Shrunk PPG (0.7118)
loses to plain PPG (0.7368); shrunk points-per-million (0.6400) loses badly to
plain (0.7213). Shrinkage penalises every thin sample, including genuinely good
players early in a season, and that costs more than the cameo noise it removes.

This does not retire the sample-size marker added in Track 1. A visual warning
tells a human "do not read this number as settled", which is a different
intervention from silently altering the ranking. But the marker must stay
informational: **shrinkage must not be baked into any score.**

**Both Track 2 composites lost, badly.** `composite_quality_security` (0.5647)
and `composite_with_form` (0.5906) are far below the simplest column on the
sheet, and lose all nine seasons. They were built on the reasoning that quality
times minutes-security times sample-confidence should beat a plain average.
It does not. Their weakness is inherited from their P/90 core.

The harness was built to stop a plausible-sounding composite reaching the
interface on the strength of its story. The first thing it did was reject the
proposal that motivated building it.

## The gate

Any new ordering must, on this nine-fold walk-forward:

1. Beat **points_per_game at 0.7368** on pooled within-gameweek Spearman.
2. Beat the **minutes_mean control at 0.7229**, or it is not adding anything to
   "does he play".
3. Win a **majority of the nine seasons**, not just the pooled mean. A mean can
   hide a candidate that wins narrowly everywhere and loses badly once.

## Limitations

- The target is raw points, not points per pound. `points_per_million` scores
  0.7213 while also accounting for price, which under a budget constraint may
  be the better trade — this harness cannot say, because it does not model a
  budget. A squad-level evaluation would be needed to settle it.
- Fixture difficulty is absent from the archive and is held at the neutral
  value 3, so no candidate here can express a fixture swing.
- Position is not controlled for. A sort is used within a position at least as
  often as across all players, and the ordering that wins overall may not win
  inside each position.
