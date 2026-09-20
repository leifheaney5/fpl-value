# Multi-Season History — Design

**Date:** 2026-09-19
**Status:** Approved in session (layout and thresholds delegated to the implementer)

## Goal

Let the Master Spreadsheet and the player page show how a player performed in
past seasons, so one good season can be told apart from a dependable record.

## Decisions

| Question | Decision |
| --- | --- |
| What appears | Cross-season columns on the current sheet. The 2026/27 roster and ratings are unchanged; history is added beside them. Past seasons are not viewable as their own sheet, and no historical `value`/`reliable_value`/`forward_value` is computed. |
| Measures | Raw per-season totals, season-to-season output, within-season gameweek variance, availability track record. |
| Surfaces | `/spreadsheet` behind a `history=1` toggle; `/players/{id}` always. |
| Computation | Precomputed `player_season_aggregates` table. Grouping all ten seasons per request measured 3.67s on the local database. |
| Lookback | All stored seasons except the current one. No window control. |
| Sheet layout | A derived column group, then one points column per stored season, newest first, `—` where the player has no record. |

## Data

`player_season_aggregates`, one row per `(player_code, season)`, built from
`gameweek_history`:

- `fixtures` — rows in the season. The archive records every fixture a player
  was registered for, including those with no minutes, so this is the
  availability denominator.
- `appearances` — fixtures with `minutes > 0`.
- `minutes`, `starts`, `starts_derived` (true when any start in the season was
  inferred from minutes — the 2016/17–2021/22 archive seasons).
- `points`, `goals`, `assists`, `clean_sheets`, `bonus`.
- `appearance_points`, `appearance_points_sq` — sum and sum of squares of
  points over appearances. Standard deviations over any set of seasons are
  derived from these; per-season SDs cannot be averaged.
- `blanks` (appearances with ≤ 2 points), `hauls` (appearances with ≥ 10).
- `price_min`, `price_max`, `position`, `team_name` (from the latest fixture).

Completed seasons never change, so this is a derived table rather than a cache.
`build-season-aggregates` rebuilds it; `import-archive` rebuilds the seasons it
imported.

## Derived measures

All follow the existing rule that an absent measurement is null, never zero.

| Measure | Definition | Withheld when |
| --- | --- | --- |
| Season P/90 | `points × 90 / minutes` | season minutes < 270 (the app's existing P/90 floor) |
| Qualifying season | season minutes ≥ `reliability_sample_minutes` (900) | — |
| Avg P/90 | mean of season P/90 over qualifying seasons | no qualifying season |
| Spread | sample SD of season P/90 over qualifying seasons | fewer than 2 qualifying seasons |
| Consistency | `Spread / Avg P/90`: ≤ 0.15 Steady, ≤ 0.30 Variable, else Volatile | fewer than 2 qualifying seasons |
| GW SD | SD of points per appearance, pooled over all past seasons | fewer than 10 appearances |
| Blank % / Haul % | `blanks / appearances`, `hauls / appearances`, pooled | fewer than 10 appearances |
| Minutes share | `minutes / (90 × fixtures)`, pooled | fewer than 19 fixtures |
| Start % | `starts / fixtures`, pooled; marked as an estimate if any season's starts were derived | fewer than 19 fixtures |
| Durability | minutes share ≥ 0.70 High, ≥ 0.40 Medium, else Low | fewer than 19 fixtures |

## Components

- `app/db/models.py` — `PlayerSeasonAggregate`; migration `0012`.
- `app/services/season_history.py` — `rebuild_season_aggregates`,
  `summarise_career` (pure), `career_summaries`, `stored_seasons`.
- `app/cli.py` — `build-season-aggregates`; `import-archive` rebuilds after import.
- `app/web/routes.py` — `history` parameter on `/spreadsheet`; career summary on
  `/players/{id}`.
- Templates, `column_help.py`, `METRICS.md`, README, CHANGELOG.

## Out of scope

Exports, the other analysis pages, a lookback selector, historical ratings, and
the live `collect_gameweek_history` path (which writes rows without
`player_code` and is disabled by default).

## Testing

Service tests against `seeded_history_db`: aggregate correctness, idempotent
rebuild, withholding rules, current-season exclusion, single-query lookup.
Web tests: toggle renders the column group and season columns and is preserved
by tabs and filters; the player page renders the season table and the empty
state. Migration drift test covers the new table.
