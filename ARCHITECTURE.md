# Architecture

FastAPI serves Jinja pages and exports. `app/api` owns FPL HTTP access,
`app/analytics` contains deterministic calculations and metric contracts,
`app/services` owns refresh, queries, season state, exports, and legacy imports,
and `app/db` owns SQLAlchemy models and sessions. PostgreSQL is the production
source of truth; SQLite is used for local development and tests. Alembic is the
production schema evolution mechanism.

The refresh pipeline is shared by the web manual-refresh action, CLI, and
Railway cron service. Each successful run stores immutable player snapshots.

## Modules

| Module | Responsibility |
| --- | --- |
| `app/config.py` | Settings, including the fail-closed `ACCESS_MODE` and the current season. |
| `app/web/auth.py` | Route protection classes, session gate, login throttling. |
| `app/web/audit.py` | Append-only audit log; never raises, never stores secrets. |
| `app/analytics/contracts.py` | Metric contracts, statuses, and the `MetricValue` display type. |
| `app/analytics/metrics.py` | Deterministic calculations. Returns `None` when inputs do not exist. |
| `app/services/season_state.py` | Season state and per-feature readiness. Pure; no database access. |
| `app/services/queries.py` | Season-scoped reads, movement classification, dashboard assembly. |
| `app/services/refresh.py` | Ingestion, metric calculation, schema baseline, gameweek calendar. |
| `app/services/team_recommender.py` | The only squad optimiser and constraint engine. |

## Tables

Ingestion: `teams`, `players`, `fixtures`, `gameweeks`, `player_snapshots`,
`gameweek_history`.

Operations: `refresh_runs`, `schema_fields`, `schema_changes`, `import_records`,
`audit_events`.

`player_snapshots` carries `season` and a `metric_status` JSON map alongside its
metric columns.

## Design rules

1. **Zero is a measurement.** Derived metric columns are nullable; a null means
   there is no number, and `metric_status` says why. See `docs/METRICS.md`.
2. **Season is explicit.** Every snapshot names its season, every query filters
   on it, and comparisons across seasons raise rather than return a number.
3. **Access fails closed.** Absent credentials deny access rather than disabling
   the check. See `docs/SECURITY.md`.
4. **Features refuse rather than mislead.** A feature whose inputs cannot support
   a result reports `not_ready` with the failing checks. See
   `docs/SEASON_STATE.md`.
5. **One optimiser.** `team_recommender` owns squad rules; templates and price
   slots call it rather than reimplementing constraints.

## Known technical debt

`player_snapshots` has around sixty columns and serves as both the raw
observation store and the derived metric store. Separating them would make
metric versioning and recomputation much cheaper, and is the first candidate for
the next structural increment. It was not attempted here because the migration
cost would have dominated.
