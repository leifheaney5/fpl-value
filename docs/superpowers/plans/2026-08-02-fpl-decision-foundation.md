# FPL Decision Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver source-aware differential, transfer-market, and template-team decision tools on the existing FPL snapshot and recommender architecture.

**Architecture:** Add a pure player-intelligence service that consumes existing query rows, then expose it through FastAPI routes and Jinja templates. Reuse `team_recommender` as the only squad optimizer and retain the current refresh pipeline/database schema unless a proven storage need emerges.

**Tech Stack:** Python 3.12, FastAPI, Jinja2, SQLAlchemy 2, Alembic, pytest, Docker Compose.

## Global Constraints

- Reuse existing FPL API client, snapshot model, refresh pipeline, and feasibility-first team recommender.
- Every calculated output declares source status, captured timestamp, freshness, and confidence.
- Do not present unavailable intraday, elite-cohort, injury-provider, or model-training data as observed facts.
- Keep Docker local and Railway cron compatibility; do not commit secrets.

---

### Task 1: Player intelligence derivation

**Files:**
- Create: `app/services/player_intelligence.py`
- Test: `tests/test_player_intelligence.py`

**Interfaces:**
- Consumes: `list[dict]` from `app.services.queries.latest_rows`.
- Produces: `build_player_intelligence(rows, now=None) -> list[dict]` and `differential_score(snapshot) -> dict`.

- [ ] Write failing tests for lower ownership increasing otherwise-equal differential score, unavailable history producing `Not available`, and stale snapshots being marked.
- [ ] Run `python -m pytest tests/test_player_intelligence.py -q` and observe missing-module failure.
- [ ] Implement pure, deterministic score, category, provenance, freshness, and transfer-trend calculations.
- [ ] Re-run the focused tests and then the full suite.

### Task 2: Differential and transfer-market surfaces

**Files:**
- Modify: `app/web/routes.py`
- Create: `app/templates/differentials.html`
- Create: `app/templates/transfer_market.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_web.py`

**Interfaces:**
- Consumes: `build_player_intelligence(rows, now=None)`.
- Produces: GET `/differentials` and GET `/transfer-market` HTML responses.

- [ ] Write failing route tests asserting both pages return HTTP 200 after fixture data is refreshed.
- [ ] Run the focused route tests and observe missing-route 404 responses.
- [ ] Add routes with ownership/position/price controls and tables that include provenance, confidence, and data freshness.
- [ ] Re-run focused and complete tests.

### Task 3: Template and price-slot adapter

**Files:**
- Create: `app/services/template_teams.py`
- Modify: `app/web/routes.py`
- Create: `app/templates/templates.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_template_teams.py`

**Interfaces:**
- Consumes: latest rows, `recommend_team_cached(rows, budget, strategy)`, and player-intelligence rows.
- Produces: `template_summaries(rows, budget) -> list[dict]` and `price_slot_suggestions(selected_row, intelligence_rows, limit=3) -> list[dict]`.

- [ ] Write failing tests that assert each returned template is a legal 15-player squad and each slot has up to three affordable, same-position suggestions ordered deterministically.
- [ ] Run focused tests and observe missing-module failure.
- [ ] Implement the adapter using only existing recommender strategies and constraints.
- [ ] Add the Templates route/UI and re-run focused/full tests.

### Task 4: Documentation and runtime evidence

**Files:**
- Modify: `README.md`
- Modify: `ANALYTICS.md`
- Modify: `docs/FEATURE_GAP_REPORT.md`

- [ ] Document methodology, limitations, and URLs for each surface.
- [ ] Run `python -m pytest -q`, initialise a temporary SQLite database, start the application, and request `/health`, `/differentials`, `/transfer-market`, and `/templates`.
- [ ] Capture actual output and report deferred capability groups separately from delivered work.
