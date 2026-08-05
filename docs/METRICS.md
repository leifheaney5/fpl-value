# Metrics: what they mean and when they have no value

## Zero is a measurement, not a fallback

Every derived metric column was previously `NOT NULL DEFAULT 0.0`. Preseason,
no team has played, so `reliability_factor`, `expected_minutes`, `start_rate`
and every projection evaluated to `0.0` and were stored as though measured. The
interface then showed `0.00` for all of them, ranked players on those zeros, and
excluded from the rankings anyone whose metric was "not greater than zero" —
which was everyone.

Those columns are now nullable. A `NULL` says there is no number. A companion
`metric_status` column on each snapshot says why, using one of:

| Status | Meaning |
| --- | --- |
| `value` | A calculated number. |
| `real_zero` | The calculation ran and the answer was zero. |
| `missing` | An input the source should have provided was absent. |
| `not_yet_available` | The inputs do not exist yet, typically because no match has been played. |
| `not_applicable` | The metric does not apply to this player or period. |
| `not_calculated` | The calculation was not attempted. |
| `failed` | The calculation was attempted and errored. |
| `stale` | A previously calculated value that is now outside its freshness window. |
| `invalid` | Source data failed validation. |
| `suppressed_low_confidence` | Calculated, but withheld as too uncertain to show. |

Only `value` and `real_zero` are numbers. Everything else renders as an
explanation — `Not available — No matches played yet this season` — and exports
as an empty cell. An empty export cell is not a zero, and the workbook's Guide
sheet says so.

## Contracts

`app/analytics/contracts.py` declares each metric's formula, inputs, input
seasons, target season, unit, valid range, minimum sample, null behaviour and
version. `tests/test_contracts.py` asserts every contract is fully populated —
but note what that does **not** guarantee: it checks the contracts exist, not
that this table matches them. The two did drift on 2026-08-04, when the null
behaviour of `value` and `points_per_90` changed in code and this table kept
describing the old behaviour for a day. When you change a contract, change the
row below in the same commit.

| Metric | Formula | Inputs | Unit | Null when |
| --- | --- | --- | --- | --- |
| `value` | `total_points / price` | total_points, price | pts per £m | Price is not positive, or no counting stats exist. **Not** null before a match: it is computed from last season's total and marked `previous_season`. |
| `reliability_factor` | `min(minutes/sample, 1) × (0.60·minutes/(team_matches·90) + 0.40·starts/team_matches)` | minutes, starts, team_matches | ratio | The team has played no matches this season. |
| `reliable_value` | `value × reliability_factor` | value, reliability_factor | pts per £m | Either input is null. |
| `start_rate` | `100 × starts / team_matches` | starts, team_matches | % | The team has played no matches this season. |
| `points_per_90` | `total_points × 90 / minutes` | total_points, minutes | pts per 90 | Fewer than **270 minutes** played — three full matches. One point in a one-minute cameo would otherwise read as 90.00 per 90. Marked `previous_season` before a match is played. |
| `expected_minutes` | `(0.65·minutes/team_matches + 0.35·90·starts/team_matches) × availability` | minutes, starts, team_matches, availability_factor | minutes | Before the season starts. |
| `projected_points_5` | Σ over upcoming fixtures of `(0.40·form + 0.35·ppg + 0.25·p90) × expected_minutes/90 × availability × difficulty × home` | form, ppg, p90, expected_minutes, availability, fixtures | pts | No upcoming fixtures, or expected minutes are null. |
| `forward_value` | `projected_points_5 / price` | projected_points_5, price | pts per £m | Projection is null or price is not positive. |
| `rotation_risk` | `100 × weighted mean of (1 − start share) and (1 − minute share)` | minutes, starts, team_matches | score | The team has played no matches this season. |

## Carry-over: a real number about the wrong season

Until the new season's first match, the FPL API keeps serving **last season's**
counting stats — total points, minutes, starts, points-per-game. They are real
measurements; they simply describe a season that has ended.

Metrics computable from those figures alone are calculated and carry
`MetricStatus.PREVIOUS_SEASON`, and the interface names the season they
describe (a banner plus per-column tags on the master sheet). Metrics needing a
this-season quantity stay null, because the denominator does not exist in any
form:

| Available, labelled as last season | Null until a match is played |
| --- | --- |
| `value`, `points_per_90`, `points_per_minute`, `points_per_start`, `average_minutes_per_start` | `start_rate`, `points_per_team_match`, `reliability_factor`, `reliable_value`, `expected_minutes`, `projected_points_5`, `forward_value`, `rotation_risk` |

The line is the denominator, not convenience. Everything in the right-hand
column divides by `team_matches`, which is zero, and the API does not report
last season's.

These were suppressed entirely until 2026-08-04, on the reasoning that they
would be *presented as* this season's. That is a presentation problem, and it
is now solved by the status and the labelling. Suppressing them removed
points-per-million, the single most useful preseason evaluation metric, for no
gain. See `docs/RANKING_EVALUATION.md`.

## Sample size

A rate is only as trustworthy as the minutes behind it.
`app/analytics/metrics.py:sample_confidence` grades a player's minutes as
`none`, `low` (under 900), `medium` (under 2000) or `high`, and the master
sheet marks thin samples so a 90-minute rate is not read with the weight of a
3000-minute one.

**This is informational only.** Baking shrinkage into a score was measured over
nine archive seasons and made ranking consistently *worse* — it penalises every
thin sample, including genuinely good players early in a season. Mark the
sample; do not adjust the number.

## Cross-season integrity

Every derived metric declares `input_seasons` and `target_season`, and both are
`current`. This is the rule that prevents the specific defect the audit found:
dividing a **previous-season** start count by a **current-season** team match
count. `start_rate` is null until the current season has matches, so the
division cannot be attempted with mismatched operands.

At the storage layer, `player_snapshots.season` and `gameweek_history.season`
identify which season a row describes, and `gameweek_history` is unique on
`(player_id, season, gameweek)` rather than `(player_id, gameweek)`, which
previously collided across seasons. Every query filters on season, and
`_history_comparison` raises `CrossSeasonError` rather than differencing two
seasons — a value delta across a rollover looks like player movement but is not.

`python -m app.cli import-history --directory <dir> --season 2025/26` requires
the season explicitly and never infers it.

## Ranking coverage

`assign_global_ranks` returns a count of exclusions by reason instead of
silently dropping rows. The dashboard shows players tracked, players ranked,
and the reason for each exclusion, so the two counts add up.

## Adding a metric

1. Add a `MetricContract` to `CONTRACTS`.
2. Return `None` from the calculation when the contract's null behaviour applies.
3. Record a status in the refresh pipeline's `metric_status` map.
4. Render with `metric_cell(snapshot, "name")` so absence displays as absence.
5. Add the column to the model as nullable, and to a migration.
