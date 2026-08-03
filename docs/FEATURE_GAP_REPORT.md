# FPL Value Studio Feature-Gap Report

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
