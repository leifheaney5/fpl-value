# Changelog

## Unreleased

### Hourly data sync

- `REFRESH_HOURLY=true` makes every scheduled refresh run, so an hourly cron
  keeps the data current to the hour. The self-hosted stack sets it and moves
  cron from `0 10 * * *` to `5 * * * *`; the watchdog's staleness limit drops
  from 30 hours to 3.
- Snapshots older than `SNAPSHOT_HOURLY_KEEP_HOURS` collapse to one per local
  day, so the cadence does not grow storage (about 12 GB a year otherwise).

### Perfect Pick

- A single reference score, shown between Pos %ile and Reliable (migration
  `0013`): points per team match, nudged towards the last three matches, scaled
  by recent minutes and by flagged availability.
- The first composite to clear the ranking gate. It was chosen by walk-forward
  evaluation, not by intuition: prior-season anchoring, full minutes scaling
  and price were all tested and all made the ordering worse.

### Past seasons

- "Show past seasons" on the Master Spreadsheet adds season-to-season
  consistency, gameweek volatility, durability and one points column per stored
  season. Player pages always show the season-by-season table.
- Read from a new `player_season_aggregates` table (migration `0012`), built by
  `python -m app.cli build-season-aggregates` and refreshed by `import-archive`.
  Grouping ten seasons of history per request took 3.7s.
- Thin samples are withheld, not scored, and pooled measures count from a
  player's first 900-minute season so academy years do not read as fragility.
- The sheet's player-name column is sticky again; a row-tint rule had been
  overriding it.

### Trust and access control

- Fail-closed access control with `demo`, `private` and `local` modes. Absent
  credentials now keep personal pages and mutations shut rather than disabling
  authentication entirely.
- Explicit season identity on every snapshot; cross-season comparison rejected.
- Missing-versus-zero metric semantics with a per-metric status and reason.
- Centralised season state and per-feature readiness.

### Honesty of the numbers

- Carry-over stats are labelled, not hidden or mislabelled. Until a match is
  played, `value` and `points_per_90` are computed from last season's figures
  and carry `MetricStatus.PREVIOUS_SEASON`; the sheet names the season they
  describe. Points-per-million was previously suppressed entirely.
- `points_per_90` requires 270 minutes. One point in a one-minute cameo used to
  display as 90.00 per 90.
- Sample-size markers on rates. Shrinkage was measured and rejected: it makes
  ranking worse.
- Position-relative ranks for points-per-million (migration `0009`). Ranked
  globally it is six defenders and three keepers in the top ten.
- Row colour-coding by position-relative value tier.

### Recommender audit

- Removed a fallback that derived projections from last season's
  points-per-game, which ranked a third-choice keeper above Haaland on the
  strength of 7 points from one appearance.
- Captaincy excludes goalkeepers on position, not on projection.
- Replaced the `"best available fit"` default that every player received once
  the metrics behind it were null.

### Decision surfaces

- Next-gameweek captaincy ranking that counts double gameweeks and stays silent
  without expected minutes.

### Evaluation

- Ten seasons of archive data, point-in-time feature builder with a proven
  no-leakage guarantee, and walk-forward backtesting.
- Prediction models evaluated and **not shipped**: the deployed heuristic wins
  on ranking. See `docs/MODEL_EVALUATION.md`.
- Sort orders evaluated individually, by squad built under a budget, and by
  position. See `docs/RANKING_EVALUATION.md`. The default sort was measured and
  left unchanged.

## 1.1.0

- Added explicit history/import migration and idempotent legacy CSV importer.
- Added transfer-finder and model-diagnostics pages.
- Added retrying FPL client requests, secure production cookies, expanded
  configuration, and multi-sheet Excel exports.
