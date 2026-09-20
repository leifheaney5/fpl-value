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

## Perfect Pick

The single reference ordering, abbreviated PP and shown between Pos %ile and
Reliable. It is stored and evaluated under the name `pick_score`.

```
quality  = 0.85 * points_per_team_match + 0.15 * recent_points_per_match
security = 0.25 + 0.75 * min(recent_minutes_per_match / 90, 1)
pick     = quality * security * availability_factor
```

Source: `pick_score` and `recent_window` in `app/analytics/metrics.py`. The
unit is points per match, so a 6.2 reads as roughly 6.2 points a game.

- **Points per team match, not FPL's points per game.** It divides by every
  match the team has played, benched ones included. That is why it was already
  the best single ordering: availability is inside the number.
- **Recent** is the last three team matches, taken as the difference between
  the current season totals and the snapshot from three matches back. Until
  three have been played it is the season so far. If stored snapshots do not
  reach back three matches, a shorter window is used.
- **The minutes term is partial on purpose.** Scaling fully by recent minutes
  (a floor of 0) ranked players *worse* than plain points per game, 0.7345
  against 0.7368. One missed match is not grounds to write a player off.
- **Price is not in it.** See `docs/RANKING_EVALUATION.md` for the measured
  effect of dividing by price. Value and Reliable remain the price-aware columns.
- **Points per 90 is not in it**, despite being an obvious ingredient. It has a
  negative rank correlation for midfielders and forwards.
- **Last season is not in it.** Blending in the previous season's rate made the
  ordering worse the more weight it was given (0.7208, 0.7086, 0.6875).
- **`availability_factor` is the one untested term.** The archive carries no
  injury flags, so the evaluated score omits it, as it does for Reliable.
- Null until the team has played a match this season.

The weights were chosen on the 2017/18–2021/22 folds and confirmed on
2022/23–2025/26. Five neighbouring settings score within 0.002 of each other,
so the gain comes from the structure, not the particular weights.

## Past-season measures

Shown by the spreadsheet's "Show past seasons" toggle and on player pages. They
describe completed seasons only; the current season is never pooled into them.
Source: `app/services/season_history.py`, reading `player_season_aggregates`.

| Measure | Definition | Withheld when |
| --- | --- | --- |
| Season P/90 | `points × 90 / minutes` | season minutes < 270 |
| Qualifying season | minutes ≥ `RELIABILITY_SAMPLE_MINUTES` (900) | — |
| Avg P/90 | mean of season P/90 over qualifying seasons | no qualifying season |
| Spread | sample standard deviation of those season P/90s | fewer than 2 qualifying seasons |
| Consistency | `Spread / Avg P/90`: ≤ 0.15 Steady, ≤ 0.30 Variable, else Volatile | fewer than 2 qualifying seasons |
| GW SD | standard deviation of points per appearance, pooled | fewer than 10 appearances |
| Blank % / Haul % | appearances with ≤ 2 / ≥ 10 points, pooled | fewer than 10 appearances |
| Start % | starts / fixtures registered for, pooled | fewer than 19 fixtures |
| Durability | minutes / (90 × fixtures registered for): ≥ 70% High, ≥ 40% Medium, else Low | fewer than 19 fixtures |

- **Pooled** means summed from the player's first qualifying season onward.
  Pooling every season rated Saka and Palmer as fragile because of years spent
  registered as academy players. A player with no qualifying season is pooled
  over everything he has.
- The archive has a row for every fixture a player was registered for, played
  or not. That is the availability denominator, so a January signing is judged
  only on the fixtures he was there for.
- Starts before 2022/23 are inferred from minutes. A `~` marks any start rate
  that includes such a season.
- Standard deviations across seasons are derived from stored sums and sums of
  squares; per-season deviations cannot be averaged.
- The bands are descriptive conventions chosen against the 2016/17–2025/26
  data, not fitted to an outcome. They have not been evaluated as predictors.

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
