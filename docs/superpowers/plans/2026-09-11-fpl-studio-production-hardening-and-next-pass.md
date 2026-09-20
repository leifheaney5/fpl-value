# FPL Studio Production Hardening and Next Pass Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (- [ ]) syntax for tracking.

**Goal:** Make FPL Studio durable in production, visibly trustworthy about data freshness, demonstrably strategy-sensitive, and faster to diagnose and operate.

**Architecture:** Keep FastAPI, Jinja, SQLAlchemy, and server-rendered pages. Use Railway PostgreSQL as the production source of truth, a separate scheduled refresh service for FPL ingestion, and small application services for status, strategy comparison, and durable team snapshots. Keep external FPL calls bounded and stale-safe; do not introduce a SPA, opaque model, or broad cache without measured need.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, PostgreSQL/SQLite local fallback, httpx, pytest, Docker, Railway CLI, Chart.js.

**Spec:** docs/superpowers/specs/2026-09-11-fpl-studio-freshness-fixtures-performance-design.md, DEPLOYMENT.md, and this plan.

## Global Constraints

- No dependency changes without explicit approval.
- Preserve missing-versus-zero semantics.
- The newest available FPL entry snapshot wins regardless of event flags.
- Failed refreshes retain the last valid snapshot, mark it stale, expose the selected event, and retain a safe error.
- FPL requests remain bounded by the existing timeout/retry policy.
- Personal team data remains behind existing access control.
- Secrets stay in Railway variables or an untracked environment file.
- Production migrations require local PostgreSQL verification and a backup/rollback decision first.
- FPL team row IDs and official badge codes remain separate.
- The deterministic optimizer is labelled Explainable Team Builder, not an opaque AI model.
- Railway production must use PostgreSQL, not SQLite.
- Push, merge, and deploy only after explicit release authorization. Poll the exact deployment ID to SUCCESS and verify the public URL.
- Commit messages contain no Co-Authored-By trailer.

## Current Baseline

The repository already includes freshness caching, newest-available My Team selection, manual refresh, strategy templates, Fixtures and Performance pages, timing telemetry, and responsive navigation. The current checkout still has final fixes in the working tree and three approved planning documents; preserve them.

Current Railway resources:

| Resource | Value |
| --- | --- |
| Project | fpl-value-studio, e54df4c5-d15f-48cc-a164-2fedafa9c7cc |
| Environment | production, ca46ed57-89c4-4861-9970-2b85770b285d |
| Web service | fpl-value-studio, c69cfd06-a450-4620-9e8d-af62130028d1 |
| Domain | https://fpl-value-studio-production.up.railway.app |
| Last deployment | bef3b9c9-917c-4cf8-b568-d1ee53607faa, SUCCESS |

The service currently starts and responds, but it has no PostgreSQL source, no scheduled refresh service, and no user-specific FPL configuration. HTTP 200 proves container startup, not data readiness.

## File Map

| Area | Files | Responsibility |
| --- | --- | --- |
| Operations | DEPLOYMENT.md, README.md, docs/PRODUCTION_CHECKLIST.md | Document and verify production. |
| Team identity | app/db/models.py, alembic/versions/0010_store_official_team_code.py, app/services/refresh.py, app/services/team_analysis.py | Persist and consume official club codes. |
| Data status | app/services/data_status.py, app/services/queries.py, app/web/routes.py, app/templates/dashboard.html, app/templates/my_team.html | Show refresh and personal freshness state. |
| Recommender comparison | app/services/team_recommender.py, app/web/routes.py, app/templates/recommendation.html | Compare strategies without duplicating optimizer rules. |
| Durable team snapshots | app/db/models.py, alembic/versions/0011_persist_linked_team_snapshot.py, app/services/my_team.py, app/services/freshness.py | Survive process restarts and coordinate replicas. |
| Verification | tests/test_team_analysis.py, tests/test_data_status.py, tests/test_recommender.py, tests/test_my_team.py, tests/test_refresh.py, tests/test_web.py, tests/test_observability.py, tests/test_migration.py | Prove behavior, privacy, and performance contracts. |

---

### Task 1: Establish the production contract

**Files:**
- Modify: DEPLOYMENT.md
- Modify: README.md
- Create: docs/PRODUCTION_CHECKLIST.md
- Test: command-line diff verification.

**Interfaces:**
- Consumes: current Railway IDs and existing Docker/CLI behavior.
- Produces: a runbook that distinguishes local SQLite, Railway PostgreSQL, self-hosted history, and live smoke evidence.

- [ ] Record state without changing production:

~~~powershell
git status --short
git branch --show-current
git log -1 --oneline
railway status --json
~~~

Expected: the working-tree changes and Railway context are recorded without printing secrets.

- [ ] Create docs/PRODUCTION_CHECKLIST.md with gates for tests, compilation, diff hygiene, PostgreSQL DATABASE_URL, required variables, /health, scheduled refresh, exact deployment SUCCESS, public route status, completed refresh, newest My Team event, and anonymous privacy.

- [ ] Update DEPLOYMENT.md so the active Railway target and scheduled service are explicit while retired Railway history remains historical. State that local SQLite is not durable production storage.

- [ ] Verify:

~~~powershell
git diff --check
~~~

Expected: exit 0.

- [ ] When release integration is authorized, commit only the documentation:

~~~powershell
git add DEPLOYMENT.md README.md docs/PRODUCTION_CHECKLIST.md
git commit -m "Document FPL Studio production contract"
~~~

Do not push or deploy in this task unless separately authorized.

### Task 2: Configure durable Railway production

**Files:**
- Modify: Railway project e54df4c5-d15f-48cc-a164-2fedafa9c7cc
- Modify: Railway environment ca46ed57-89c4-4861-9970-2b85770b285d
- Modify: Railway service c69cfd06-a450-4620-9e8d-af62130028d1
- Create: one PostgreSQL service in the same project/environment.
- Test: production checklist and live smoke commands.

**Interfaces:**
- Consumes: Dockerfile, scripts/start-web.sh, Alembic migrations, and current Railway resources.
- Produces: PostgreSQL-backed web service with /health monitoring and correct injected-port routing.

- [ ] Snapshot service, variable-name, and domain state without retrieving secret values:

~~~powershell
$env:RAILWAY_CALLER='skill:use-railway@1.4.0'
$env:RAILWAY_AGENT_SESSION='railway-skill-fpl-studio-20260911'
railway service list --project e54df4c5-d15f-48cc-a164-2fedafa9c7cc --environment ca46ed57-89c4-4861-9970-2b85770b285d --json
railway variable list --project e54df4c5-d15f-48cc-a164-2fedafa9c7cc --service c69cfd06-a450-4620-9e8d-af62130028d1 --environment ca46ed57-89c4-4861-9970-2b85770b285d --json
railway domain list --project e54df4c5-d15f-48cc-a164-2fedafa9c7cc --service c69cfd06-a450-4620-9e8d-af62130028d1 --environment ca46ed57-89c4-4861-9970-2b85770b285d --json
~~~

Expected: service, variable-name, and domain state are captured without secret values.

- [ ] Provision one PostgreSQL service in the existing project. Record its returned name and ID; do not create another project.

- [ ] Connect web DATABASE_URL to the database service reference. Configure:

~~~text
ACCESS_MODE=demo
CURRENT_SEASON=2026/27
APP_TIMEZONE=America/New_York
REFRESH_HOUR=10
COLLECT_GAMEWEEK_HISTORY=false
~~~

Set FPL_ENTRY_ID to the owner-supplied entry ID. Set SESSION_SECRET, APP_USERNAME, and APP_PASSWORD through secret variables without printing their values.

- [ ] Configure the web service health check as /health. Keep the generated domain target aligned with the injected PORT; scripts/start-web.sh must bind 0.0.0.0.

- [ ] Deploy only after release authorization. Poll the exact deployment ID and inspect startup logs for PostgreSQL rather than SQLiteImpl. Verify /health returns HTTP 200 and {"status":"ok"}.

- [ ] Decide separately whether to import historical self-hosted data. If approved, back up first and verify season-scoped row counts. Otherwise label this as a fresh production baseline.

### Task 3: Add scheduled FPL refresh

**Files:**
- Modify: Railway project e54df4c5-d15f-48cc-a164-2fedafa9c7cc
- Create: Railway refresh service using the same repository/image.
- Modify: DEPLOYMENT.md
- Test: tests/test_refresh.py and CLI coverage.

**Interfaces:**
- Consumes: scripts/run-refresh.sh, scripts/start-web.sh, app.cli.command_refresh, and web variables.
- Produces: a scheduled one-shot refresh process that exits without running Uvicorn.

- [ ] Verify local refresh behavior:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_refresh.py tests/test_cli.py
~~~

Create tests/test_cli.py if it is absent; cover the scheduled wrong-hour skip and delegation to refresh_data before changing behavior.

- [ ] Create a second service in the existing project/environment using the same Dockerfile. Set RUN_REFRESH_ONLY=true so scripts/start-web.sh dispatches to scripts/run-refresh.sh --scheduled.

- [ ] Copy database, season, timezone, FPL entry, and secret-variable configuration from the web service. Configure a UTC schedule around the 10:00 America/New_York refresh hour; retain the CLI local-hour guard.

- [ ] Run one controlled refresh and verify bootstrap/fixture counts, completed RefreshRun, and PostgreSQL dialect.

- [ ] Verify a wrong-hour invocation is a no-op and does not create a duplicate refresh run.

### Task 4: Persist official team codes

**Files:**
- Modify: app/db/models.py
- Create: alembic/versions/0010_store_official_team_code.py
- Modify: app/services/refresh.py
- Modify: app/services/team_analysis.py
- Modify: app/templates/fixtures.html
- Modify: app/templates/performance.html
- Test: tests/test_team_analysis.py, tests/test_refresh.py, tests/test_migration.py.

**Interfaces:**
- Consumes: FPL bootstrap teams[].code.
- Produces: Team.code: int | None and _badge_url(team_id, team_name=None, team_code=None) -> str, preferring persisted official code, then a legacy-name fallback, then team ID.

- [ ] Add failing tests:

~~~python
def test_refresh_persists_official_team_code(tmp_path):
    # Existing fake refresh contains team code 3.
    # Assert db.get(Team, 1).code == 3.

def test_badge_url_prefers_persisted_official_code():
    assert _badge_url(1, "Imported Arsenal", team_code=3).endswith("/t3.png")

def test_badge_url_falls_back_for_unknown_team():
    assert _badge_url(99, "Imported Club", team_code=None).endswith("/t99.png")
~~~

- [ ] Add nullable indexed Team.code with a reversible Alembic migration:

~~~python
op.add_column("teams", sa.Column("code", sa.Integer(), nullable=True))
op.create_index("ix_teams_code", "teams", ["code"], unique=False)
~~~

- [ ] Validate and persist item.get("code") during refresh. Update fixture/form service records and templates to use service-produced badge URLs; templates must not reconstruct URLs from team row IDs.

- [ ] Run:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_team_analysis.py tests/test_refresh.py tests/test_migration.py
python -m compileall -q app tests
~~~

Expected: all focused tests pass and compilation exits 0.

### Task 5: Add data-status and My Team freshness panels

**Files:**
- Create: app/services/data_status.py
- Modify: app/services/queries.py
- Modify: app/web/routes.py
- Modify: app/templates/dashboard.html
- Modify: app/templates/my_team.html
- Test: tests/test_data_status.py, tests/test_web.py, tests/test_access.py.

**Interfaces:**
- Consumes: RefreshRun, current-season snapshot timestamps, season_state, and My Team _freshness.
- Produces: data_status(db: Session, season: str, *, now: datetime | None = None) -> dict[str, Any] with last_success_at, last_run_status, last_run_error, snapshot_age_seconds, season, current_event, and state.

- [ ] Add tests for never_refreshed, fresh completed, stale, and failed states:

~~~python
def test_status_reports_never_refreshed_database():
    assert data_status(db, "2026/27", now=now)["state"] == "never_refreshed"

def test_status_reports_fresh_completed_run():
    result = data_status(db, "2026/27", now=now)
    assert result["state"] == "fresh"
    assert result["last_run_status"] == "completed"

def test_status_reports_failed_run_without_calling_it_current():
    result = data_status(db, "2026/27", now=now)
    assert result["state"] in {"stale", "failed"}
    assert result["last_run_error"]
~~~

- [ ] Implement the service as a read-only database projection. It must not issue an FPL request and must use injected now for deterministic tests.

- [ ] Render season, current event, last success, latest run state, snapshot age, safe error text, and authorized refresh link. Use explicit “No refresh has completed yet” and “Data may be stale” states.

- [ ] On authenticated My Team, show selected picks gameweek, fetched time, cache age/expiry, and stale/error state. Keep personal fields out of anonymous contexts.

- [ ] Run:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_data_status.py tests/test_web.py tests/test_access.py
~~~

Expected: status text renders and anonymous responses contain no entry ID, manager, squad, or personal freshness.

### Task 6: Add strategy comparison

**Files:**
- Modify: app/services/team_recommender.py
- Modify: app/web/routes.py
- Modify: app/templates/recommendation.html
- Test: tests/test_recommender.py, tests/test_web.py.

**Interfaces:**
- Consumes: STRATEGIES and recommend_team_cached(rows, budget, strategy).
- Produces: compare_recommendations(rows: list[dict[str, Any]], budget: float, strategies: Mapping[str, Mapping[str, Any]] = STRATEGIES) -> list[dict[str, Any]] with strategy, label, state, spent, remaining, formation, projected_total, starting_ids, bench_ids, captain_id, and changed_from_previous.

- [ ] Build varied test data with positive projections, every required position, multiple clubs, and differences in raw, reliable, forward, form, and ownership values.

- [ ] Add failing tests:

~~~python
def test_compare_recommendations_preserves_order_and_reports_differences(varied_rows):
    result = compare_recommendations(varied_rows, 100.0)
    assert [item["strategy"] for item in result] == list(STRATEGIES)
    assert len({tuple(item["starting_ids"]) for item in result if item["state"] == "ready"}) > 1
    assert any(item["changed_from_previous"] for item in result[1:])

def test_compare_recommendations_reports_not_ready_without_fabricating_squads(zero_projection_rows):
    result = compare_recommendations(zero_projection_rows, 100.0)
    assert all(item["state"] == "not_ready" for item in result)
    assert all(item["starting_ids"] == [] for item in result)
~~~

- [ ] Implement the adapter by calling the existing cached recommender once per strategy. Convert NotReadyError to a not_ready row with checks and activation text; do not duplicate optimizer rules.

- [ ] Render label, state, formation, projected total, budget used, captain, and starting-XI changes. If all ready strategies are identical, state that current inputs do not materially distinguish them.

- [ ] Run:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_recommender.py tests/test_web.py
~~~

Expected: varied fixtures prove strategy sensitivity and insufficient data remains an honest not-ready state.

### Task 7: Persist linked-team snapshots across restarts

**Files:**
- Modify: app/db/models.py
- Create: alembic/versions/0011_persist_linked_team_snapshot.py
- Modify: app/services/my_team.py
- Modify: app/services/freshness.py
- Modify: app/web/routes.py
- Modify: app/templates/settings.html
- Test: tests/test_my_team.py, tests/test_access.py, tests/test_migration.py.

**Interfaces:**
- Consumes: FPL_ENTRY_ID, FreshnessCache, and authenticated routes.
- Produces: protected LinkedTeamSnapshot storage keyed by entry ID with payload, selected_event, fetched_at, expires_at, stale, and last_error. linked_team_data retains its existing shape and _freshness metadata.

- [ ] Add tests for successful write, new-cache fallback, and protected access:

~~~python
def test_successful_team_load_writes_a_snapshot(db, team_client, settings):
    result = linked_team_data(db, team_client, settings)
    stored = db.scalar(select(LinkedTeamSnapshot).where(LinkedTeamSnapshot.entry_id == 123))
    assert stored.selected_event == result["event"]
    assert stored.stale is False

def test_new_cache_reads_last_valid_snapshot(db, team_client, settings):
    linked_team_data(db, team_client, settings)
    my_team_service._REMOTE_CACHE = FreshnessCache()
    result = linked_team_data(db, OfflineTeamClient(), settings)
    assert result["_freshness"]["stale"] is True
    assert result["event"] == 4

def test_snapshot_route_remains_protected(client):
    assert client.get("/my-team", follow_redirects=False).status_code == 303
~~~

- [ ] Add an additive migration with unique entry_id, JSON payload, selected event, timezone-aware fetched/expiry timestamps, stale flag, safe error, and updated timestamp. Store no credentials or session data.

- [ ] Write/replace snapshots after successful loads. On failed revalidation, retain payload, set stale, and record the safe error. Manual invalidation never deletes the last-valid payload.

- [ ] Retain FreshnessCache for per-process single-flight. Use a short PostgreSQL row lock or compare-and-swap claim so replicas do not refresh one entry simultaneously after expiry.

- [ ] Show configured entry ID, selected event, last snapshot time, and manual refresh link only to authenticated users. Do not add arbitrary entry-ID mutation in this task.

- [ ] Run:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_my_team.py tests/test_access.py tests/test_migration.py
~~~

Expected: a new cache can serve the last valid snapshot as stale, transient failures cannot downgrade the event, and anonymous responses remain free of personal data.

### Task 8: Measure response time and add operator diagnostics

**Files:**
- Modify: app/services/queries.py
- Modify: app/services/freshness.py
- Modify: app/api/fpl_client.py
- Modify: app/web/routes.py
- Modify: app/main.py
- Modify: app/templates/diagnostics.html
- Test: tests/test_queries.py, tests/test_observability.py, tests/test_web.py.

**Interfaces:**
- Consumes: request timing, FPL timing, freshness timing, and query-count instrumentation.
- Produces: route, status, duration, cache state, selected event, and query count telemetry without response bodies, cookies, query values, or secrets.

- [ ] Add tests proving one dashboard latest-row query, no second FPL request within My Team TTL, and timing logs that exclude cookies/query values.

- [ ] Remove only measured duplicate work. Keep dashboard rows loaded once; keep fixture/form projections bounded to ten records per team; keep comparison-option queries bounded.

- [ ] Add a 1000 ms slow-request diagnostic containing route, status, duration, cache state, and query count. Never log request bodies, authorization headers, entry payloads, or cookie values.

- [ ] Show protected /diagnostics aggregates: p50/p95 duration, slow-request count, latest refresh duration, and FPL error count. With no samples, render “No timing samples yet” rather than a zero-valued claim.

- [ ] Run:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q tests/test_queries.py tests/test_observability.py tests/test_web.py
python -m pytest -q
~~~

### Task 9: Add live smoke tests and release evidence

**Files:**
- Create: tests/test_production_smoke.py
- Modify: docs/PRODUCTION_CHECKLIST.md
- Modify: DEPLOYMENT.md
- Test: local suite and exact Railway commands.

**Interfaces:**
- Consumes: the Railway domain, service IDs, checklist, and public/protected route contract.
- Produces: repeatable evidence that a successful image is responding and configured.

- [ ] Create an opt-in, bounded helper accepting an explicit base URL and checking /health, /, /fixtures, /performance, and /recommendation. Skip unless FPL_STUDIO_SMOKE_URL is explicitly supplied so ordinary pytest never calls production. Report only status code, elapsed milliseconds, and a short marker; never print bodies or cookies.

- [ ] Cover the protected flow by fetching /login, preserving CSRF and cookies in memory, and checking /my-team and /diagnostics when credentials are intentionally supplied. Without credentials, assert the documented redirect. Never put credentials in source or output.

- [ ] Poll the exact submitted deployment ID:

~~~powershell
$env:RAILWAY_CALLER='skill:use-railway@1.4.0'
$env:RAILWAY_AGENT_SESSION='railway-skill-fpl-studio-20260911'
railway deployment list --project e54df4c5-d15f-48cc-a164-2fedafa9c7cc --environment ca46ed57-89c4-4861-9970-2b85770b285d --service c69cfd06-a450-4620-9e8d-af62130028d1 --json
~~~

- [ ] Run:

~~~powershell
$base='https://fpl-value-studio-production.up.railway.app'
foreach ($path in @('/health','/','/fixtures','/performance','/recommendation')) {
  curl.exe -sS --max-time 30 -D - -o NUL "$base$path"
}
~~~

Expected: documented status codes, /health HTTP 200 with {"status":"ok"}, and expected headings.

- [ ] Record deployment ID, image digest, domain status, HTTP results, refresh result, database dialect, and missing owner-supplied variables in the checklist.

### Task 10: Final integration

**Files:**
- Modify: all files intentionally changed by Tasks 1–9.
- Test: complete verification and exact deployment polling.

- [ ] Review:

~~~powershell
git status --short
git diff --stat
git diff --check
git diff --name-only
~~~

Confirm no secret, database file, generated export, or unrelated reformat appears.

- [ ] Run canonical verification:

~~~powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'
python -m pytest -q
python -m compileall -q app tests
git diff --check
~~~

- [ ] When release integration is authorized, commit without a Co-Authored-By trailer:

~~~powershell
git add app tests alembic README.md DEPLOYMENT.md docs
git commit -m "Harden FPL Studio production freshness and diagnostics"
~~~

- [ ] When push and deploy are authorized:

~~~powershell
git push origin main
$env:RAILWAY_CALLER='skill:use-railway@1.4.0'
$env:RAILWAY_AGENT_SESSION='railway-skill-fpl-studio-20260911'
railway up --project e54df4c5-d15f-48cc-a164-2fedafa9c7cc --environment ca46ed57-89c4-4861-9970-2b85770b285d --service c69cfd06-a450-4620-9e8d-af62130028d1 --detach --json -m 'Harden FPL Studio production freshness and diagnostics'
~~~

Capture the returned deployment ID and poll that exact ID to SUCCESS.

## Delivery Order

1. Tasks 1–3: PostgreSQL, required environment, and scheduled refresh. Release blockers.
2. Task 4: persisted official badge codes.
3. Task 5: visible data-status and freshness.
4. Task 6: strategy comparison.
5. Task 7: restart/replica-safe My Team snapshots.
6. Task 8: measured performance and diagnostics.
7. Tasks 9–10: smoke evidence, integration, and release.

## Explicit Non-Goals

- No React migration, SPA router, or new frontend dependency.
- No trained prediction model or opaque AI scoring layer.
- No arbitrary public FPL entry lookup UI.
- No destructive deletion of team snapshots or historical data.
- No Redis or broad distributed cache until PostgreSQL-backed evidence shows the simpler row/lock approach misses response budgets.
- No automatic import of retired self-hosted data without a separately approved backup and migration procedure.
