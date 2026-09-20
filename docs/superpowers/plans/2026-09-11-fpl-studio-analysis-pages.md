# FPL Studio Fixture and Performance Pages Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add fast, data-grounded Fixtures and Performance pages using the existing stored teams and fixtures while preserving FPL Studio’s visual language.

**Architecture:** A focused `team_analysis` service will transform one bounded query of teams and fixtures into presentation-ready fixture and form records. Routes will render those records through two Jinja templates; no per-team API requests, new frontend framework, or new database table is needed.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy, stored FPL `Team`/`Fixture` rows, and existing CSS.

**Spec:** `docs/superpowers/specs/2026-09-11-fpl-studio-freshness-fixtures-performance-design.md`

## Global Constraints

- Fixtures use official FPL difficulty values from 1 through 5.
- Lower average next-ten difficulty ranks better.
- Form uses the latest ten finished fixtures with validated FPL scores.
- Missing scores are incomplete data, never zero-score losses.
- The existing My Team fixture design and approved Performance visual direction remain intact.
- The canonical suite is `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest`.

---

### Task 1: Define fixture and form service contracts with failing tests

**Files:**
- Create: `app/services/team_analysis.py`
- Create: `tests/test_team_analysis.py`

**Interfaces:**
- `fixture_analysis(db: Session, *, limit: int = 10) -> list[dict[str, Any]]` returns team records sorted by `average_difficulty`, then team name.
- `team_performance(db: Session, *, limit: int = 10) -> list[dict[str, Any]]` returns team records sorted by `points_per_game`, total points, then team name.
- Each fixture record contains `event`, `kickoff_time`, `opponent`, `opponent_id`, `is_home`, `difficulty`, and `badge_url`.
- Each form record contains `results`, `wins`, `draws`, `losses`, `points`, `points_per_game`, `goals_for`, and `goals_against`.

- [ ] **Step 1: Write varied fixture-analysis tests**

Seed two teams with twelve future fixtures and assert only ten are returned, the lower mean FDR ranks first, home/away difficulty is read from the correct FPL field, and badge URLs are derived from the opponent/team identifier. Add a missing-difficulty case and assert it is marked incomplete rather than scored as zero.

- [ ] **Step 2: Write varied team-form tests**

Seed finished fixtures with wins, draws, losses, and one finished fixture with missing score fields. Assert the latest ten scored matches are used, results are displayed chronologically, W/D/L counts and points are correct, and the missing-score fixture is excluded from the totals.

- [ ] **Step 3: Run the tests and verify the expected failure**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_team_analysis.py -q`

Expected: failure because `team_analysis.py` and both service functions do not exist.

- [ ] **Step 4: Implement one-query bounded projections**

Load `Team` rows and only the needed unfinished/finished `Fixture` rows once each. For fixture analysis, append each fixture to both participating teams with the correct difficulty field, sort by `(event is None, event, kickoff_time, id)`, keep ten, compute the mean over numeric difficulties, and set `complete=False` when any selected difficulty is missing. For form, read `team_h_score` and `team_a_score` from `Fixture.raw`, discard non-numeric score pairs, compute W/D/L and points, and retain the newest ten before reversing for chronological display.

- [ ] **Step 5: Run the service tests**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_team_analysis.py -q`

Expected: all fixture and form service tests pass.

- [ ] **Step 6: Commit the analysis service**

```bash
git add app/services/team_analysis.py tests/test_team_analysis.py
git commit -m "Add team fixture and form analysis"
```

### Task 2: Add the two routes and templates

**Files:**
- Modify: `app/web/routes.py`
- Create: `app/templates/fixtures.html`
- Create: `app/templates/performance.html`
- Modify: `tests/test_web.py`
- Modify: `tests/test_in_season_rendering.py`

**Interfaces:**
- `GET /fixtures` calls `fixture_analysis(db, limit=10)` and renders `fixtures.html`.
- `GET /performance` calls `team_performance(db, limit=10)` and renders `performance.html`.

- [ ] **Step 1: Add route-rendering tests**

Extend the common page matrix with `/fixtures` and `/performance`. Add assertions that populated fixture data renders “Next 10 fixtures”, “Difficulty”, and a team name; populated form data renders “Last 10 matches”, “Points per game”, and W/D/L labels. Add empty-state assertions for a database with no fixture rows.

- [ ] **Step 2: Run the route tests and verify they fail**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_web.py tests/test_in_season_rendering.py -q`

Expected: 404 or template-loading failures for both new paths.

- [ ] **Step 3: Implement the route handlers**

Import the service functions and add the two GET handlers beside the existing analytics pages. Pass the current season only to shared data functions that require it; fixture/form queries use the current persisted fixture set. Return an explicit empty-state context rather than raising when no data exists.

- [ ] **Step 4: Implement the Fixtures template**

Use the existing page-heading/panel/table classes. Render each ranked team as a row with team badge, name, average difficulty out of five, available fixture count, and ten compact cells. Each cell includes opponent badge/abbreviation, `GWn` or “Not assigned”, `(H)`/`(A)`, and numeric difficulty. Add `aria-label` text describing each cell so the gradient is not the only signal.

- [ ] **Step 5: Implement the Performance template**

Render a ranked team table with badge/name, W-D-L, points, PPG, goals for/against, and a horizontal last-ten result strip. Each result cell includes opponent and score and uses W/D/L text plus color. Render the existing empty/error language when no scored fixtures are available.

- [ ] **Step 6: Run route tests and commit**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_web.py tests/test_in_season_rendering.py tests/test_team_analysis.py -q`

Expected: all new routes render with populated, partial, and empty data.

```bash
git add app/web/routes.py app/templates/fixtures.html app/templates/performance.html tests/test_web.py tests/test_in_season_rendering.py
git commit -m "Add fixtures and performance pages"
```

## Plan Verification

Run the full suite, compileall, and diff check. Confirm that a double gameweek is represented by two fixture cells where applicable, no missing score becomes a loss, no route makes one external request per team, and the new pages remain readable at the existing mobile breakpoint.
