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
version. `docs/METRICS.md` and the code cannot drift apart silently, because
`tests/test_contracts.py` asserts every contract is fully populated.

| Metric | Formula | Inputs | Unit | Null when |
| --- | --- | --- | --- | --- |
| `value` | `total_points / price` | total_points, price | pts per £m | Price is not positive, **or the team has played no matches** — a points-per-million rate needs opportunity in its denominator. |
| `reliability_factor` | `min(minutes/sample, 1) × (0.60·minutes/(team_matches·90) + 0.40·starts/team_matches)` | minutes, starts, team_matches | ratio | The team has played no matches this season. |
| `reliable_value` | `value × reliability_factor` | value, reliability_factor | pts per £m | Either input is null. |
| `start_rate` | `100 × starts / team_matches` | starts, team_matches | % | The team has played no matches this season. |
| `points_per_90` | `total_points × 90 / minutes` | total_points, minutes | pts per 90 | No minutes played this season. |
| `expected_minutes` | `(0.65·minutes/team_matches + 0.35·90·starts/team_matches) × availability` | minutes, starts, team_matches, availability_factor | minutes | Before the season starts. |
| `projected_points_5` | Σ over upcoming fixtures of `(0.40·form + 0.35·ppg + 0.25·p90) × expected_minutes/90 × availability × difficulty × home` | form, ppg, p90, expected_minutes, availability, fixtures | pts | No upcoming fixtures, or expected minutes are null. |
| `forward_value` | `projected_points_5 / price` | projected_points_5, price | pts per £m | Projection is null or price is not positive. |
| `rotation_risk` | `100 × weighted mean of (1 − start share) and (1 − minute share)` | minutes, starts, team_matches | score | The team has played no matches this season. |

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
