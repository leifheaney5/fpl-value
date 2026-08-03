# Trust Foundation Design

**Date:** 2026-08-02
**Status:** Approved
**Supersedes:** nothing. Complements `2026-08-02-fpl-decision-foundation-design.md`, which delivered the differential / transfer-market / template surfaces.

## Why this increment exists

The master goal specifies a large analytics and decision engine. Almost all of it depends on the application being able to state, truthfully, what it knows and what it does not. Today it cannot:

- Personal data is served to anonymous visitors because authentication fails open.
- Unavailable measurements are stored as `0.0`, so "no matches played" and "played and scored nothing" are the same value.
- There is no season identifier, so previous-season rows and current-season rows live in one table and are differenced against each other.
- Four downstream defects fall directly out of the two problems above.

This increment fixes trust. Prediction models, cohorts, simulators and planners are deliberately excluded; they are worth nothing built on data whose meaning is ambiguous.

## Scope

In scope: master goal sections 2, 4, 5, 6, 7, the Movers correction from section 11, the recommender guardrails from section 20, the ranked-coverage accounting from section 9.1, and the schema-baseline correction from section 56.

Out of scope, each requiring its own spec: predictions and ensembles, elite cohorts, Monte Carlo rank simulation, multi-gameweek and robust planning, chips, live gameweek, captaincy optimiser, AI analyst, watchlists and alerts, decision journal, backtesting, counterfactuals, shadow teams.

## Confirmed defects and their mechanisms

| ID | Defect | Mechanism |
| --- | --- | --- |
| D1 | Personal FPL data is publicly readable | `Settings.auth_enabled` is `bool(app_username and app_password)`. Absent credentials disable authentication instead of denying access. |
| D2 | Unavailable values render as zero | Derived snapshot columns are `Float, nullable=False, default=0.0`. `reliability_factor`, `expected_minutes`, `start_rate` and `project_next_fixtures` return `0.0` when their inputs are absent. |
| D3 | Cross-season mixing | No `season` column exists. `import_history_directory` writes prior-season CSV rows into `player_snapshots`. `GameweekHistory` is unique on `(player_id, gameweek)`, so two seasons collide. |
| D4 | Movers lists unchanged players as both risers and fallers | `movers_data` retains rows whose delta is not `None`, including exact zeros, then sorts the same list ascending and descending. |
| D5 | Recommender returns a minimal-cost squad with unspent budget | With every projection zero, `team_objective` produces equal first and second sort keys for all states, so `max` resolves on `-spent`, selecting the cheapest legal squad. |
| D6 | Ranked coverage is unexplained | `assign_global_ranks` drops rows whose metric is not greater than zero and records no reason. The dashboard shows the resulting counts without a breakdown. |
| D7 | First schema observation is reported as hundreds of additions | `_update_schema` treats an empty `schema_fields` table as "everything is new" and emits an `Added` change per field. |

D2, D5 and D6 share one root cause: a non-nullable zero default erasing the difference between a measured zero and an absent measurement.

## Design

### A. Access control and privacy

Replace the derived `auth_enabled` property with an explicit, fail-closed `ACCESS_MODE` setting taking one of three values:

- `demo` (default) — analytics routes are public and serve real market data, which is not personal. Personal and mutation routes require authentication.
- `private` — every route except `PUBLIC` requires authentication. Intended for Tailscale and LAN deployments.
- `local` — no authentication. Refuses to start unless the database URL is SQLite, so it cannot be selected by accident in production.

Routes carry a protection class rather than relying on a single global gate:

| Class | Members | Rule |
| --- | --- | --- |
| `PUBLIC` | `/health`, `/login`, `/static` | Always reachable. |
| `ANALYTICS` | player market pages, exports of market data | Public in `demo`, authenticated otherwise. |
| `PERSONAL` | `/my-team`, the dashboard team card, entry-scoped data | Authenticated in every mode. |
| `MUTATION` | `/admin/refresh` and future sync, import and AI endpoints | Authenticated and CSRF-checked in every mode. |

Privacy is enforced by exclusion rather than masking. `linked_team_data` is not called for an unauthenticated request, so manager name, team name and entry identifier never enter a template context that an anonymous visitor can receive. Masking values already present in the render tree depends on every template applying a filter correctly; excluding them at the source does not.

If `ACCESS_MODE` requires credentials and none are configured, the application still starts and still serves its public class, but every `PERSONAL` and `MUTATION` route is refused and the login page states that no credentials are configured. Starting is preferable to crashing, because a crash loop on Railway would take the public deployment down; refusing the protected classes preserves the security property either way.

Additional controls: `HttpOnly`, `Secure` when not on SQLite, `SameSite=Lax` session cookies; absolute session expiry; failed-login throttling per client address; and an `audit_events` table recording login success, login failure, logout and every mutation with actor, action, path, client address and timestamp. Secrets are never logged.

### B. Season identity

Add `season` to `player_snapshots` and `gameweek_history`, typed as the FPL season label (for example `2026/27`). Change the gameweek uniqueness constraint to `(player_id, season, gameweek)`. Add an index on `(season, captured_at)` for snapshot queries.

`import_history_directory` gains a required `season` parameter; it will not guess. Snapshot queries become season-scoped, defaulting to the current season from the season-state service. Any comparison whose two operands carry different seasons raises `CrossSeasonError` rather than returning a number.

Backfill assigns the configured current season to existing rows, because every existing row in a deployed database was written by the current-season refresh pipeline. Imported rows, which are the ones that could be prior-season, are identifiable by their `ImportRecord` provenance and are left for the operator to re-import with an explicit season; the migration records how many such rows exist.

### C. Missing, zero and invalid values

Derived columns become nullable and store `NULL` when their inputs are insufficient. The affected columns are `value`, `reliability_factor`, `reliable_value`, `start_rate`, `points_per_90`, `points_per_start`, `points_per_team_match`, `points_per_minute`, `minutes_per_team_match`, `average_minutes_per_start`, `value_per_90`, `expected_minutes`, `projected_points_5`, `forward_value` and `average_fixture_difficulty`. Observation columns such as `minutes`, `starts`, `total_points`, `price` and `ownership` remain non-nullable, because the API reports them as genuine observations.

Each snapshot gains a `metric_status` JSON column mapping metric name to one of `real_zero`, `missing`, `not_yet_available`, `not_applicable`, `not_calculated`, `failed`, `stale`, `invalid` or `suppressed_low_confidence`, together with a human-readable reason.

A `MetricValue` presentation type carries value, status, reason, unit and precision. A Jinja filter renders it: a number when the status is a value, and an explanatory phrase such as `Not available — no matches played yet` otherwise. Exports emit an empty cell rather than `0` for a non-value status, and record the reason in a companion column.

Metric contracts are declared in one module: name, formula, input fields, input seasons, target season, unit, valid range, minimum sample, null behaviour, metric version and calculation timestamp. The refresh pipeline consults the contract to decide whether a metric is computable, which removes the ad-hoc guards currently scattered through `metrics.py`.

### D. Season state and readiness

Bootstrap `events` are currently parsed for schema detection and then discarded. Persist them in a `gameweeks` table with season, number, deadline, finished, data-checked and current flags.

`app/services/season_state.py` exposes a pure function over gameweeks, fixtures, latest snapshot time and the current instant, returning the season state, current gameweek, next deadline and time remaining. States are those enumerated in the master goal, from `uninitialized` through `historical_season`.

A companion readiness function returns one of `ready`, `degraded`, `fallback`, `not_ready`, `stale` or `error`, together with missing inputs, stale inputs, invalid inputs, the active fallback, the last successful calculation, the condition that would activate the feature, and an explanation intended for display. Features declare their required inputs, optional inputs, supported states, minimum sample and freshness window in a registry, so readiness is computed in one place rather than inferred per page.

### E. The four repairs

**Movers.** A riser satisfies `delta > positive_threshold`; a faller satisfies `delta < negative_threshold`. Thresholds are configurable per metric and default to a value just above rounding noise. Zero and unchanged values are excluded from both lists, making them mutually exclusive by construction. Each list reports window start, window end, observation count and threshold, and renders an honest empty state when nobody qualifies.

**Recommender guardrails.** Before optimising, validate the player pool size, per-position pool sizes, the count of players with a non-null projection, and the variation in the objective. If the standard deviation of the objective across the pool is not meaningfully above zero, the inputs cannot distinguish squads, and the recommender raises a structured `NotReadyError` carrying the failed checks and the activation condition instead of returning a squad. The tie-break order becomes expected starting-XI points, expected minutes, start probability, then **budget utilisation ascending in remaining funds** — the inverse of today's behaviour — followed by captaincy ceiling and bench coverage. The response reports budget used, budget remaining, why funds remain, the best excluded candidate and its marginal gain.

**Ranked coverage.** `assign_global_ranks` records an exclusion reason per excluded row rather than filtering silently. The dashboard shows total players, ranked players and a breakdown by reason.

**Schema baseline.** When `schema_fields` is empty, the observation is a baseline. Fields are recorded with `change_type="Baseline"` and the refresh run reports a baseline rather than a change count.

### F. Validation

Unit tests cover season-state transitions; readiness verdicts; the distinction between a real zero and each unavailable status; rejection of deliberately season-mixed comparisons; movers threshold behaviour and riser/faller mutual exclusivity; recommender readiness refusal, budget utilisation and tie-break order; exclusion accounting; and schema baseline semantics.

Integration tests assert that an anonymous request cannot read personal data on any route, cannot export personal data, and cannot trigger a refresh; that the migration applies and rolls back; and that a preseason database renders every page without presenting a fabricated zero.

Evidence required before this increment is considered complete: the full test suite passing, the migration applying to a fresh database, the application starting and serving `/health` and every route, and a Docker Compose start reaching a healthy container.

## Known technical debt accepted in this increment

`player_snapshots` carries sixty columns and serves as both raw observation store and derived metric store. Separating observations from derived metrics is the correct long-term structure and would make metric versioning and recomputation far cheaper. It is not attempted here because the migration cost would dominate the increment. The season column and metric-status column are added to the existing table, and the split is recorded as the first candidate for the next structural increment.
