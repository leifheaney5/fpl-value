# FPL Studio Response-Time and Navigation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce redundant database/API work, add measurable request timing, and center primary navigation without changing the established visual language.

**Architecture:** Optimize at existing service boundaries: replace known N+1 queries with grouped SQL, add lightweight read models for comparison options, pass already-loaded rows across consumers, and instrument FastAPI requests. Use a CSS grid header for true visual centering and keep the current responsive collapse behavior.

**Tech Stack:** Python 3.12, FastAPI middleware, SQLAlchemy, SQLite/PostgreSQL-compatible SQL, pytest, Jinja2, and CSS.

**Spec:** `docs/superpowers/specs/2026-09-11-fpl-studio-freshness-fixtures-performance-design.md`

## Global Constraints

- Measure before optimizing and retain query-count evidence in tests.
- Do not add an unbounded cache or serve stale data as current.
- Independent requests may be parallelized only when their data dependencies are explicit.
- Navigation must remain centered when left and right header content have different widths.
- No new frontend dependency.
- The canonical suite is `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest`.

---

### Task 1: Remove database N+1 and duplicate latest-row work

**Files:**
- Modify: `app/services/queries.py`
- Modify: `app/web/routes.py`
- Modify: `app/services/my_team.py`
- Create or modify: `tests/test_queries.py`
- Modify: `tests/test_web.py`

**Interfaces:**
- `latest_player_options(db: Session, season: str, *, exclude_player_id: int | None = None) -> list[dict[str, Any]]` returns only current player/team/latest snapshot fields needed by comparison options.
- `history_player_count(db: Session, season: str) -> int` returns the number of current players with at least two snapshots.
- `linked_team_data(..., rows: list[dict[str, Any]] | None = None)` reuses caller rows when supplied.

- [ ] **Step 1: Add query-count regression tests**

Attach a SQLAlchemy `before_cursor_execute` listener to a seeded test engine. Assert `history_player_count()` uses one grouped query, `latest_player_options()` returns the same visible options as the full-row path, and dashboard/recommendation pass their existing rows into My Team instead of issuing another latest-row query.

- [ ] **Step 2: Run the tests and verify they fail**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_queries.py tests/test_web.py -q`

Expected: missing helper failures and a query-count assertion showing diagnostics still performs one history query per player.

- [ ] **Step 3: Implement grouped history counting**

Replace the `for row in rows: player_history(...)` loop in `diagnostics_data()` with a grouped `select(PlayerSnapshot.player_id).where(PlayerSnapshot.season == season).group_by(PlayerSnapshot.player_id).having(func.count(PlayerSnapshot.id) >= 2)` query and `len(...)`. Keep the existing `sufficient` threshold and message unchanged.

- [ ] **Step 4: Implement lightweight comparison options**

Use the latest snapshot timestamp for the season, join `PlayerSnapshot` to `Player` and `Team`, select only the option fields, exclude the detail player, and preserve the existing dictionary keys consumed by `player_detail.html`.

- [ ] **Step 5: Reuse rows across routes**

In `dashboard()`, pass `data["rows"]` to `linked_team_data()`. In `recommendation_page()`, assign the recommender input to a local `rows` variable and pass those rows to `linked_team_data()`. In `my_team.py`, use the supplied rows for `rows_by_id` and call `latest_rows()` only when no rows were supplied.

- [ ] **Step 6: Run query and route tests**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_queries.py tests/test_web.py tests/test_in_season_rendering.py -q`

Expected: tests pass and query counts no longer scale with player count for diagnostics or comparison options.

- [ ] **Step 7: Commit the database optimization**

```bash
git add app/services/queries.py app/web/routes.py app/services/my_team.py tests/test_queries.py tests/test_web.py
git commit -m "Remove redundant FPL view queries"
```

### Task 2: Add request timing and cache observability

**Files:**
- Modify: `app/main.py`
- Modify: `app/services/freshness.py`
- Modify: `app/api/fpl_client.py`
- Create or modify: `tests/test_observability.py`

**Interfaces:**
- Middleware logs `request_complete method=<method> path=<path> status=<status> duration_ms=<number>`.
- Freshness records expose `fetched_at`, `expires_at`, `cache_hit`, `stale`, and dataset key to development logs.
- FPL client logs endpoint path, status, duration, and retry count without payloads.

- [ ] **Step 1: Write failing observability tests**

Use `caplog` and a TestClient request to assert one request-complete log includes the route and non-negative duration. Use a freshness test to assert cache-hit and stale fields are logged at the service boundary and that no response body or secret-like setting value appears in log records.

- [ ] **Step 2: Run the tests and verify failure**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_observability.py -q`

Expected: missing middleware/log record failure.

- [ ] **Step 3: Add bounded middleware logging**

Register a small HTTP middleware in `app/main.py`, measure with `time.perf_counter()`, call the downstream app exactly once, and log in a `finally` block so error responses are timed too. Do not log headers, cookies, query values, or response bodies.

- [ ] **Step 4: Add freshness log fields**

Emit one structured log record on cache load/hit/stale fallback with dataset key, cache state, selected event, age milliseconds, and remaining TTL. Keep the existing user-facing response unchanged except for the intentional stale indicator from the freshness plan.

- [ ] **Step 5: Run focused observability tests**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_observability.py tests/test_fpl_client.py tests/test_freshness.py -q`

Expected: all observability tests pass with no secret/body fields in captured logs.

- [ ] **Step 6: Commit instrumentation**

```bash
git add app/main.py app/services/freshness.py app/api/fpl_client.py tests/test_observability.py
git commit -m "Add request and freshness timing telemetry"
```

### Task 3: Center navigation responsively

**Files:**
- Modify: `app/templates/base.html`
- Modify: `app/static/app.css`
- Modify: `tests/test_web.py`

**Interfaces:**
- Header markup has `.site-header__brand`, `.site-header__nav`, and `.site-header__actions` regions.
- Desktop CSS uses `grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr)` with navigation in the intrinsic center column.

- [ ] **Step 1: Add markup/CSS regression assertions**

Assert the base response contains the three named header regions and the new `/fixtures` and `/performance` links. Keep a narrow viewport smoke check at 600px by rendering the same HTML and asserting the navigation retains the responsive class rather than overflowing.

- [ ] **Step 2: Implement the three-column header**

Move brand, nav, and account/settings controls into the three regions. Right-align the action region and left-align branding while leaving the nav centered by grid placement. At the existing 1000px breakpoint, hide or collapse only the nav according to the current behavior; preserve settings and authentication controls.

- [ ] **Step 3: Run rendering checks**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_web.py tests/test_preseason_rendering.py tests/test_in_season_rendering.py -q`

Expected: all pages render and the header assertions pass.

- [ ] **Step 4: Commit the navigation unit**

```bash
git add app/templates/base.html app/static/app.css tests/test_web.py
git commit -m "Center responsive primary navigation"
```

## Plan Verification

Run `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest`, `python -m compileall -q app tests`, and `git diff --check`. Capture route timings and SQL query counts before and after the changes, then perform the production health smoke test. Report data-freshness, latency, and visual-verification results separately.
