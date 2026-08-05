# FPL Value Studio Feature-Gap Report

> **HISTORICAL — this is a point-in-time audit from 2026-08-02, kept as a
> record of what the codebase looked like before the trust work. Do not read
> it as a description of the current system.** Figures below, including the
> test count of 10, describe the repository on that date; the suite is now
> several hundred tests and most of the gaps named here are closed.
>
> For current state see `README.md`, `docs/METRICS.md`,
> `docs/MODEL_EVALUATION.md` and `docs/RANKING_EVALUATION.md`.
>
> **Update 2026-08-03 — Trust Foundation increment delivered.**
> See the "Trust Foundation delivered" section at the end of this document for
> the seven audit findings and their resolutions, and
> `docs/superpowers/specs/2026-08-02-trust-foundation-design.md` for the design.

**Audit date:** 2026-08-02  
**Evidence:** repository inventory, routes, SQLAlchemy models, refresh pipeline, Docker/Railway configuration, and `python -m pytest -q` (10 passed).

## Architecture discovered

- **Web:** FastAPI with server-rendered Jinja templates and vanilla JavaScript/CSS.
- **Data:** SQLAlchemy 2 with Alembic; PostgreSQL in Railway and SQLite locally.
- **Ingestion:** synchronous FPL public API client; a manual refresh, CLI refresh, and daily Railway cron run all use `app.services.refresh`.
- **History:** immutable per-player snapshots, optional gameweek histories, schema-change history, and idempotent CSV history import.
- **Decision logic:** transparent value/reliability/fixture/rotation metrics and a cached, constraint-aware full-squad recommender.
- **Deployment:** Docker Compose (PostgreSQL + web), Railway Docker deployment plus separate scheduled refresh service, optional password protection.

## Existing features to retain

| Requirement area | Status | Existing implementation |
| --- | --- | --- |
| Player intelligence inputs | Partial | `PlayerSnapshot` has price, ownership, transfer totals, minutes, availability, form, xG/xA, fixtures, rotation, expected minutes, and a five-fixture heuristic. |
| Differentials | Partial | Transfer Finder supports ownership, position, price, availability, minutes, rotation, and forward-value constraints. No differential score/categories/explanations. |
| Transfer activity | Partial | Current FPL transfer totals are stored in raw snapshot payloads; historical ownership/value/price movement exists. No transfer snapshots, velocity, acceleration, or dashboard. |
| Elite cohorts | Does not exist | No rank-cohort collection, storage, or aggregation. |
| Price structures/templates | Partial | `team_recommender` returns valid 15-player squads for a few strategies. No price slots, template comparison/presets, or locks/exclusions. |
| Predictions | Partial | Transparent five-fixture heuristic only; no explicit horizon series, interval, model lifecycle, or evaluation. |
| My Team | Partial | Optional public entry import, current picks, basic performance history, and a one-step comparison to a recommendation. |
| Price tracking | Partial | Snapshot deltas for 1/7/30 days, not confirmed intraday price-change storage/signals. |
| Data health | Partial | Refresh run and schema history are visible; no source/job health dashboard. |
| Security/reliability | Partial | CSRF, optional authentication, FPL request handling, refresh overlap prevention, and schema monitoring exist; no AI endpoints or multi-source reconciliation. |

## Explicitly skipped as already satisfied

- Do not create a second FPL client, refresh system, player snapshot table, squad-rule engine, or team-recommender.
- Do not duplicate the current Player Explorer, Transfer Finder, Movers, comparison, exports, My Team route, diagnostics, or Railway cron mechanism.

## First implementation increment

Build on current snapshots and recommender without fabricating unavailable data:

1. A shared derived player-intelligence object with provenance, freshness, confidence, differential score/category, and transfer-change metrics.
2. Differential Finder route and UI with ownership presets, existing filters, transparent score explanations, and stale-data notice.
3. Transfer Market route and UI with current inbound/outbound/net leaders plus historical velocity/acceleration where local snapshots permit; explicitly label unavailable intraday trends.
4. Valid template-team and price-slot recommendations using the existing feasibility-first recommender, with strategy presets and three recommendations per slot.
5. Tests and documentation for all new deterministic calculations and the new routes.

## Deferred capability groups

Elite cohorts, official team deadline storage, prediction training/evaluation, AI analysis, live scoring, chip planning, alerts, journal/backtesting, and multi-source injury/lineup reconciliation require dedicated data sources, background execution, and/or a user identity model. They remain intentionally unclaimed until their source and lifecycle are implemented.

---

## Trust Foundation delivered (2026-08-03)

Every finding below was verified in code before being repaired, and each has a
regression test named in the table.

| ID | Finding | Mechanism found | Resolution | Test |
| --- | --- | --- | --- | --- |
| D1 | Personal FPL data publicly readable | `auth_enabled` was `bool(username and password)`, so absent credentials disabled authentication rather than denying access. `valid_credentials` returned `True` when nothing was configured. | Explicit fail-closed `ACCESS_MODE`; per-route protection classes; personal data excluded from anonymous contexts rather than masked. | `tests/test_access.py` |
| D2 | Unavailable metrics rendered as `0.00` | Derived columns were `NOT NULL DEFAULT 0.0`, and the calculations returned `0.0` when their inputs did not exist. | Columns nullable; calculations return `None`; `metric_status` records why; pages, sorts, filters and exports show an explained absence. | `tests/test_contracts.py`, `tests/test_preseason_rendering.py` |
| D3 | Cross-season mixing | No `season` column existed. History imports wrote previous-season rows into the same table, and `gameweek_history` was unique on `(player_id, gameweek)`. | `season` on snapshots and gameweek history; unique key includes season; every query scoped; `CrossSeasonError` on mismatched comparison; `--season` required on import. | `tests/test_season_isolation.py` |
| D4 | Movers listed unchanged players as both risers and fallers | Rows were kept when the delta was not null, including exact zeros, then sorted both ways. | `classify_movement` with per-metric thresholds; risers and fallers disjoint by construction; window and unchanged count reported. | `tests/test_movers.py` |
| D5 | Minimal-cost squad with a large unused budget | Two causes. The final tie-break was `-spent`, preferring the cheapest squad when projections tied. More seriously, the beam search spent freely on early positions, reached the last position unable to afford anyone, and silently fell back to `_find_feasible_squad` — the cheapest legal squad. | Budget reservation: a state must leave enough to fill its remaining slots. Tie-break inverted to prefer budget use. Fallback now declares itself via `optimised: false`. Readiness gate refuses rather than guessing. | `tests/test_recommender.py` |
| D6 | Ranked coverage unexplained | `assign_global_ranks` dropped rows with `metric <= 0` and recorded nothing. | Exclusions counted by reason; dashboard shows tracked, ranked, excluded and the breakdown. | `tests/test_refresh.py` |
| D7 | First schema observation shown as hundreds of additions | An empty `schema_fields` table was treated as "everything is new". | First observation recorded as `Baseline`; change count excludes it. | `tests/test_refresh.py` |

### Additional defects found during the work

| Finding | Impact | Resolution |
| --- | --- | --- |
| `0001_initial.py` called `Base.metadata.create_all` against the live models | A fresh database jumped to the current schema, later revisions found their columns already present and did nothing, and downgrades removed columns 0001 had created. Migrations were not reproducible. | Revision 0001 frozen to explicit historical DDL. A test compares the migrated schema against the models, including nullability, so the drift cannot return. |
| Naive/aware datetime subtraction in `player_intelligence` | The differentials and transfer-market pages raised `TypeError` on SQLite, so they were broken for local and Docker deployments. | Timestamps normalised to UTC. |
| Differential score computed from substituted zeros | Preseason, every low-owned player scored as a punt on inputs that did not exist. | Score is not calculated when the projection or expected-minutes input is missing; unscored players are listed separately, never ranked. |

### Deferred, with reasons

These remain unimplemented and are not claimed anywhere in the interface:

- **Preseason projection and expected-minutes models.** The readiness registry
  declares "no fallback" for both rather than implying coverage. This is the
  single highest-value next increment: it is what would make the application
  useful *today*, in the season state it is actually in.
- **Elite cohorts, live gameweek, Monte Carlo rank simulation, multi-gameweek
  and robust planning, chips, captaincy optimiser, AI analyst, watchlists and
  alerts, decision journal, backtesting, counterfactuals, shadow teams.** Each
  needs either a data source that does not exist yet or accumulated history the
  database does not hold. Building them now would produce unvalidated code.
- **Docker container validation.** The Docker daemon was not running in the
  development environment, so the image was not built or run. The Compose file
  was updated for the new variables but not executed.
