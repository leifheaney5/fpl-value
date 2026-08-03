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
| `app/services/archive_schema.py` | Column mapping for the FPL archive's four schema eras. Pure. |
| `app/services/archive_import.py` | Archive ingestion, keyed on stable player code and fixture. |
| `app/models/features.py` | Point-in-time feature builder with an availability mask. Pure. |
| `app/models/baselines.py` | Baselines a trained model must beat before shipping. |
| `app/models/metrics.py` | Accuracy, rank correlation and calibration. |
| `app/models/evaluation.py` | Walk-forward backtesting. |
| `app/models/dataset.py` | Materialise features into aligned arrays once per state. |
| `app/models/candidates.py` | Trainable candidates and the `TrainableModel` protocol. |
| `app/models/network.py` | Two-stage minutes-then-events model. **Training only — never imported in production.** |
| `app/models/artefact.py` | Manifest, ONNX export, and serving through onnxruntime. |
| `app/services/predictions.py` | Produce and store predictions, or report why it cannot. |
| `scripts/train.py` | Offline training. Refuses to write an artefact that fails the gate. |

## Tables

Ingestion: `teams`, `players`, `fixtures`, `gameweeks`, `player_snapshots`,
`gameweek_history`.

`gameweek_history` holds both live API collection and the imported historical
archive, distinguished by `source`. It is keyed on
`(player_code, season, gameweek, fixture_id)`: the code because element ids move
between seasons, and the fixture because a double gameweek gives a player two
fixtures in one gameweek.

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
6. **Player identity is the code, not the element id.** FPL re-assigns element
   ids every season, so anything spanning seasons joins on `players.code`.
7. **No feature may see the future.** `app/models/features.py` discards rows at
   or after the prediction time before computing anything, and is tested with
   deliberately poisoned future rows.
8. **A model ships only if it beats the baselines.** `app/models/evaluation.py`
   scores the deployed heuristic alongside any candidate on held-out seasons,
   and `scripts/train.py` refuses to write an artefact that loses.
9. **Training and serving are separate.** PyTorch trains offline; production
   loads an ONNX graph through onnxruntime and never imports torch. A
   subprocess test enforces it.

## Model lifecycle

```
import-archive ─► gameweek_history ─► dataset ─► train (offline, PyTorch)
                                                    │
                                          gate: beat the heuristic?
                                                    │ yes
                                        artefact (ONNX + manifest)
                                                    │
                                   predictions table ─► readiness ─► UI
```

A model that fails the gate stops at the gate. The transparent
`projected_points_5` heuristic stays in use, and the readiness registry reports
it as the active fallback rather than implying a model is running.

## Known technical debt

`player_snapshots` has around sixty columns and serves as both the raw
observation store and the derived metric store. Separating them would make
metric versioning and recomputation much cheaper, and is the first candidate for
the next structural increment. It was not attempted here because the migration
cost would have dominated.
