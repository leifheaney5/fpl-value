# Season Readiness and Captaincy Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the application demonstrably correct in the season states it will actually occupy from 21 August, then ship captaincy — the highest-leverage weekly decision — on top of the heuristic that already won the evaluation.

**Architecture:** Two halves. The first adds end-to-end coverage for the four in-season states that no test has ever rendered, using fake FPL clients that generate deadlines relative to `now` rather than freezing time. The second derives a next-gameweek projection from the `upcoming_fixtures` JSON already stored on each snapshot — a pure read-side feature requiring no migration — and ranks captain candidates from it, honouring the existing readiness and missing-versus-zero contracts.

**Tech Stack:** FastAPI, Jinja2, SQLAlchemy 2, pytest. No new runtime dependencies.

## Context: where the project actually is

Read this before starting. It is the state as of 2026-08-04.

**Live and healthy** at `https://web-production-f5979.up.railway.app/`. Trust Foundation
is deployed: fail-closed access control, season identity, missing-versus-zero
semantics, per-feature readiness. Anonymous requests get 303 on `/my-team` and
`POST /admin/refresh`; no personal data appears in anonymous HTML.

**The model line is closed.** `docs/MODEL_EVALUATION.md` records the decision: no
model ships, the `projected_points_5` heuristic remains the sole projection
source. The heuristic wins on ranking (Spearman 0.6899 versus 0.6716 for the best
model variant across nine folds). A model beats it on MAE by 8–18% if a more
accurate *displayed* number is ever wanted, but that is a separate decision and
not this plan's business. **Do not revisit this.** Migrations `0005`–`0008` are
written, tested, and deliberately unapplied in production.

**Docker is validated as of 2026-08-04.** `docker compose up --build` was run on
the Windows host: image builds (420MB), all eight migrations apply cleanly from
empty on real PostgreSQL, `/health` returns 200, anonymous requests return 303
under `ACCESS_MODE=private`, CSRF is enforced (correct credentials without a
token are rejected), and the login round-trip reaches 200 on `/`, `/my-team` and
`/schema`. `torch` is confirmed absent from the image. The `linux-leif` item is
closed; do not repeat it.

**The finding that motivates Task 1.** `tests/test_preseason_rendering.py` has a
test named `test_every_page_renders_with_in_season_data`. It does not render an
in-season state. `FakeClient.bootstrap()` returns `events: [{"id": 1, "finished":
True}]` — a single finished gameweek with no unfinished successor — which
`season_state()` classifies as `postseason` ("Every gameweek is complete. The
season is over."). This was verified by direct probe, not inferred.

The consequence: end-to-end page coverage jumps from `preseason` straight to
`postseason`. The four states the application will occupy every week from 21
August — `gameweek_open`, `pre_deadline`, `deadline_passed`, `provisional` — have
**never had a page rendered in them**. The state machine itself is well covered
(`tests/test_season_state.py`, 16 tests over 9 states); the gap is specifically
that no route, query, or template has ever executed against those states.

`SeasonState.LIVE`, `SeasonState.FINALIZED` and `SeasonState.HISTORICAL_SEASON`
are declared and labelled but are never returned by `season_state()`. They are
aspirational placeholders. Task 1 does not add them; Task 6 records them.

**17 days to the GW1 deadline** (21 August 2026, 17:30 UTC).

## Global Constraints

- **No new production dependencies.** `pyproject.toml` production `dependencies`
  stays as it is. Training extras (`numpy`, `scikit-learn`) are training-only.
- **Missing is not zero.** A metric with no basis returns `None` and carries a
  `metric_status` reason. Never emit `0.0` for an unmeasured quantity. See
  `app/analytics/contracts.py`.
- **A feature that cannot run says why.** Register it in
  `app/services/season_state.py:FEATURES` and render its `explanation`. Never
  show an empty panel.
- **Season-scoped queries.** Every query filters on `season`; cross-season
  comparison is rejected.
- **Timezone discipline.** Normalise both sides to UTC before comparing
  datetimes. SQLite returns naive datetimes; this has caused two production
  crashes already. Use the `_as_utc` pattern in
  `app/services/season_state.py:64`.
- **Test names must describe what is actually exercised.** The bug in Task 1
  survived because a test named `..._with_in_season_data` rendered postseason.
- **Frequent commits.** One commit per task minimum.
- **Full suite green before each commit:** `pytest` (253 tests currently pass).

---

## File Structure

**Modify:**
- `tests/fakes.py` — add four in-season fake clients and a relative-deadline
  helper. Currently holds `FakeClient`, `PreseasonClient`,
  `CarryOverPreseasonClient`.
- `tests/test_preseason_rendering.py` — rename the misleading test, add
  state-parametrised rendering.
- `app/services/season_state.py` — register the `captaincy` feature requirement.
- `app/services/queries.py:506-521` — add `captaincy` to the readiness dict.
- `app/web/routes.py` — add the `/captaincy` route.
- `app/web/auth.py:20` — add `/captaincy` to `PROTECTION_MAP` as `PUBLIC`.
- `app/templates/base.html` — add the nav link.
- `DEPLOYMENT.md` — record the Docker validation and the `onnxruntime` gap.

**Create:**
- `tests/test_in_season_rendering.py` — the four in-season states, rendered.
- `app/services/captaincy.py` — next-gameweek projection and candidate ranking.
  One responsibility: turn stored snapshots into a ranked captain shortlist.
- `tests/test_captaincy.py` — unit tests for the service.
- `app/templates/captaincy.html` — the page.

---

## Task 1: Render the four in-season states

**Files:**
- Modify: `tests/fakes.py`
- Test: `tests/test_in_season_rendering.py` (create)

**Interfaces:**
- Produces: `LiveClient`, `PreDeadlineClient`, `DeadlinePassedClient`,
  `ProvisionalClient` in `tests/fakes.py`, each a `FakeClient` subclass with
  `bootstrap()` and `fixtures()`. Task 2 consumes all four.

- [ ] **Step 1: Write the failing test**

Create `tests/test_in_season_rendering.py`:

```python
"""Every page must render in the states the season will actually occupy.

Coverage previously jumped from preseason straight to postseason: the test
named ``test_every_page_renders_with_in_season_data`` used a client whose only
gameweek was finished, which ``season_state()`` classifies as ``postseason``.
The four states below are the ones the application is in every week between
21 August and May, and no page had ever been rendered in any of them.
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.session import get_db
from app.main import app
from app.services.refresh import refresh_data
from app.services.season_state import SeasonState

from fakes import (
    DeadlinePassedClient,
    LiveClient,
    PreDeadlineClient,
    ProvisionalClient,
)

PAGES = [
    "/", "/spreadsheet", "/spreadsheet?view=forward", "/spreadsheet?view=movers",
    "/forward", "/rotation", "/transfers", "/movers", "/compare",
    "/diagnostics", "/schema", "/settings", "/differentials",
    "/transfer-market", "/templates", "/recommendation",
]

STATES = [
    (LiveClient, SeasonState.GAMEWEEK_OPEN),
    (PreDeadlineClient, SeasonState.PRE_DEADLINE),
    (DeadlinePassedClient, SeasonState.DEADLINE_PASSED),
    (ProvisionalClient, SeasonState.PROVISIONAL),
]


def _seeded(tmp_path, client_class, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=url, current_season="2026/27")
    with Session() as db:
        refresh_data(db, settings, client_class())

    def override_db():
        with Session() as db:
            yield db

    app.dependency_overrides[get_db] = override_db
    return TestClient(app)


@pytest.mark.parametrize("client_class,expected_state", STATES)
def test_client_produces_the_state_it_claims(tmp_path, client_class, expected_state):
    """The fixture must actually reach the state, or the coverage is fictional."""
    client = _seeded(tmp_path, client_class, f"{expected_state}.db")
    try:
        body = client.get("/diagnostics").text
        assert expected_state in body, (
            f"{client_class.__name__} did not produce {expected_state}"
        )
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize("client_class,expected_state", STATES)
@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders(tmp_path, path, client_class, expected_state):
    client = _seeded(tmp_path, client_class, f"{expected_state}-pages.db")
    try:
        response = client.get(path)
        assert response.status_code == 200, (
            f"{path} returned {response.status_code} in {expected_state}"
        )
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run it to confirm it fails**

Run: `pytest tests/test_in_season_rendering.py -x`
Expected: `ImportError: cannot import name 'DeadlinePassedClient' from 'fakes'`.

- [ ] **Step 3: Add the fake clients**

Append to `tests/fakes.py`:

```python
from datetime import datetime, timedelta, timezone


def _relative(hours: float) -> str:
    """An ISO deadline offset from now.

    The routes call ``datetime.now(timezone.utc)`` themselves, so a fixed
    timestamp would drift into a different season state as the calendar moves.
    Generating relative to now keeps each client in its intended window
    permanently, without freezing time.
    """
    moment = datetime.now(timezone.utc) + timedelta(hours=hours)
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


class LiveClient(FakeClient):
    """Gameweek 1 played and confirmed, gameweek 2 open for changes.

    This is ``gameweek_open``: the state the application spends most of the
    season in, and the one no page had ever been rendered in.
    """

    NEXT_DEADLINE_HOURS = 72.0
    NEXT_FINISHED = False
    NEXT_IS_CURRENT = False
    NEXT_IS_NEXT = True
    FIRST_DATA_CHECKED = True
    FIRST_IS_CURRENT = True

    def bootstrap(self):
        payload = super().bootstrap()
        payload["events"] = [
            {
                "id": 1,
                "name": "Gameweek 1",
                "deadline_time": _relative(-168),
                "finished": True,
                "data_checked": self.FIRST_DATA_CHECKED,
                "is_current": self.FIRST_IS_CURRENT,
                "is_next": False,
            },
            {
                "id": 2,
                "name": "Gameweek 2",
                "deadline_time": _relative(self.NEXT_DEADLINE_HOURS),
                "finished": self.NEXT_FINISHED,
                "data_checked": False,
                "is_current": self.NEXT_IS_CURRENT,
                "is_next": self.NEXT_IS_NEXT,
            },
        ]
        return payload

    def fixtures(self):
        first, second = super().fixtures()
        return [
            dict(first, finished=True, kickoff_time=_relative(-166)),
            dict(second, finished=False, kickoff_time=_relative(
                self.NEXT_DEADLINE_HOURS + 2)),
        ]


class PreDeadlineClient(LiveClient):
    """The next deadline is within the 24-hour window."""

    NEXT_DEADLINE_HOURS = 6.0


class DeadlinePassedClient(LiveClient):
    """Gameweek 2's deadline has gone and its results are not in."""

    NEXT_DEADLINE_HOURS = -2.0
    NEXT_IS_CURRENT = True
    NEXT_IS_NEXT = False
    FIRST_IS_CURRENT = False


class ProvisionalClient(LiveClient):
    """Gameweek 1 has finished but bonus points are not confirmed."""

    FIRST_DATA_CHECKED = False
```

- [ ] **Step 4: Run the state assertions only**

Run: `pytest tests/test_in_season_rendering.py::test_client_produces_the_state_it_claims -v`
Expected: 4 passed.

If any client lands in the wrong state, trace it through
`app/services/season_state.py:97-245` rather than adjusting the assertion. The
order of checks there is: postseason, deadline-passed-on-current,
provisional, pre-deadline, international-break, gameweek-open.

- [ ] **Step 5: Run the full rendering matrix**

Run: `pytest tests/test_in_season_rendering.py -v`
Expected: 68 tests. Some **may fail** — that is the purpose of the task. These
pages have never executed in these states. Read each failure; fix the
application code, not the test. Likely candidates, given past incidents in this
codebase: a naive-versus-aware datetime comparison, and a template assuming
`current_gameweek` is not `None`.

- [ ] **Step 6: Run the whole suite**

Run: `pytest`
Expected: 253 existing + 68 new, all passing.

- [ ] **Step 7: Commit**

```bash
git add tests/fakes.py tests/test_in_season_rendering.py
git commit -m "Render every page in the four states the season will occupy"
```

---

## Task 2: Correct the misleading test name

**Files:**
- Modify: `tests/test_preseason_rendering.py:76-81`

The existing test is not wrong — postseason is a real state worth covering. Its
name is wrong, and the name is why the gap survived.

- [ ] **Step 1: Rename it**

Replace lines 76–81 of `tests/test_preseason_rendering.py`:

```python
@pytest.mark.parametrize("path", PAGES)
def test_every_page_renders_with_postseason_data(tmp_path, path):
    """``FakeClient``'s only gameweek is finished, which is postseason.

    This was called ``..._with_in_season_data`` and was read as covering the
    live season for months. It does not: see tests/test_in_season_rendering.py.
    """
    client, _ = _seeded_client(tmp_path, FakeClient, "post.db")
    try:
        assert client.get(path).status_code == 200
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Amend the `FakeClient` docstring**

In `tests/fakes.py`, replace the module docstring's second sentence:

```python
"""Stub FPL clients shared across tests.

``FakeClient`` has one finished gameweek and no unfinished successor, which
``season_state()`` classifies as **postseason** — not a season in progress. For
a live season use ``LiveClient`` and its subclasses below.
``PreseasonClient`` represents the state the live deployment is in today:
fixtures scheduled, none played, every counting stat still zero.
"""
```

- [ ] **Step 3: Run and commit**

Run: `pytest tests/test_preseason_rendering.py -v`
Expected: all pass.

```bash
git add tests/fakes.py tests/test_preseason_rendering.py
git commit -m "Name the postseason rendering test for the state it exercises"
```

---

## Task 3: Next-gameweek projection

**Files:**
- Create: `app/services/captaincy.py`
- Test: `tests/test_captaincy.py` (create)

**Interfaces:**
- Consumes: `app.analytics.metrics.project_next_fixtures` (existing, at
  `app/analytics/metrics.py:186`), and the `upcoming_fixtures` JSON column on
  `PlayerSnapshot`, whose entries carry `event`, `opponent`, `difficulty`,
  `is_home` (written at `app/services/refresh.py:350-368`).
- Produces: `next_gameweek_projection(snapshot, gameweek) -> float | None` and
  `fixtures_in_gameweek(snapshot, gameweek) -> list[dict]`.

**Why this is not `projected_points_5 / 5`:** `upcoming` appends one entry per
fixture, so a team playing twice in a gameweek contributes two entries. The
number of fixtures inside the five-fixture window varies per player, so dividing
by five understates a double gameweek and overstates a blank. Captaincy is
exactly where that error is most costly.

- [ ] **Step 1: Write the failing test**

Create `tests/test_captaincy.py`:

```python
"""Captaincy is judged on the next gameweek alone, and on every fixture in it."""

from types import SimpleNamespace

import pytest

from app.services.captaincy import fixtures_in_gameweek, next_gameweek_projection


def _snapshot(**overrides):
    base = dict(
        form=5.0,
        points_per_game=5.0,
        points_per_90=6.0,
        expected_minutes=85.0,
        availability=1.0,
        upcoming_fixtures=[
            {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
            {"event": 3, "opponent": "TST", "difficulty": 4, "is_home": False},
        ],
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def test_only_fixtures_in_the_target_gameweek_count():
    assert len(fixtures_in_gameweek(_snapshot(), 2)) == 1
    assert fixtures_in_gameweek(_snapshot(), 2)[0]["opponent"] == "OTH"


def test_a_double_gameweek_counts_both_fixtures():
    """Two fixtures in one gameweek is the case captaincy exists to catch."""
    double = _snapshot(upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
        {"event": 2, "opponent": "TST", "difficulty": 3, "is_home": False},
    ])
    single = _snapshot(upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
    ])
    assert len(fixtures_in_gameweek(double, 2)) == 2
    assert next_gameweek_projection(double, 2) > next_gameweek_projection(single, 2)


def test_a_blank_gameweek_projects_zero_not_none():
    """No fixture is a real, measured zero: the player cannot score."""
    blank = _snapshot(upcoming_fixtures=[
        {"event": 3, "opponent": "OTH", "difficulty": 2, "is_home": True},
    ])
    assert next_gameweek_projection(blank, 2) == 0.0


def test_no_expected_minutes_projects_none_not_zero():
    """Preseason. Unknown is not zero; that distinction is load-bearing here."""
    assert next_gameweek_projection(_snapshot(expected_minutes=None), 2) is None


def test_no_target_gameweek_projects_none():
    assert next_gameweek_projection(_snapshot(), None) is None
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_captaincy.py -x`
Expected: `ModuleNotFoundError: No module named 'app.services.captaincy'`.

- [ ] **Step 3: Write the implementation**

Create `app/services/captaincy.py`:

```python
"""Who to captain, and how confident that answer is.

Captaincy doubles one player's score, so it is judged on the next gameweek
alone -- never on the five-fixture forward window the rest of the application
uses. The distinction matters most in a double gameweek, where a player has two
fixtures and the five-fixture average silently halves the thing that makes them
worth captaining.

Everything here is derived from data already stored on the snapshot, so this
module needs no migration and no change to the refresh pipeline.
"""

from __future__ import annotations

from typing import Any

from app.analytics.metrics import project_next_fixtures


def fixtures_in_gameweek(snapshot: Any, gameweek: int | None) -> list[dict[str, Any]]:
    """Every fixture the player's team plays in that gameweek.

    A list, not a single fixture: a double gameweek has two, and collapsing
    them to one is the specific error this function exists to avoid.
    """
    if gameweek is None:
        return []
    fixtures = getattr(snapshot, "upcoming_fixtures", None) or []
    return [
        fixture
        for fixture in fixtures
        if fixture.get("event") == gameweek
    ]


def next_gameweek_projection(snapshot: Any, gameweek: int | None) -> float | None:
    """Projected points for that gameweek, or None when there is no basis.

    Returns 0.0 -- a real zero -- for a blank gameweek, because a player with
    no fixture genuinely cannot score. Returns None when expected minutes are
    unknown, because that is an absence of information rather than a forecast
    of a blank. The two must not collapse into each other.
    """
    if gameweek is None:
        return None

    expected = getattr(snapshot, "expected_minutes", None)
    availability = getattr(snapshot, "availability", None)
    if expected is None or availability is None:
        return None

    fixtures = fixtures_in_gameweek(snapshot, gameweek)
    if not fixtures:
        return 0.0

    return project_next_fixtures(
        form=float(getattr(snapshot, "form", 0.0) or 0.0),
        points_per_game=float(getattr(snapshot, "points_per_game", 0.0) or 0.0),
        points_per_90=getattr(snapshot, "points_per_90", None),
        expected_minutes_value=expected,
        availability=availability,
        fixtures=fixtures,
    )
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_captaincy.py -v`
Expected: 5 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/captaincy.py tests/test_captaincy.py
git commit -m "Project the next gameweek alone, counting every fixture in it"
```

---

## Task 4: Rank captain candidates

**Files:**
- Modify: `app/services/captaincy.py`
- Test: `tests/test_captaincy.py`

**Interfaces:**
- Consumes: `next_gameweek_projection` from Task 3.
- Produces: `captain_candidates(snapshots, gameweek, limit=8) -> list[dict]`,
  each with keys `player`, `projection`, `captain_points`, `fixture_count`,
  `opponents`, `confidence`, `reasoning`. Task 5 renders these keys.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_captaincy.py`:

```python
from app.services.captaincy import captain_candidates


def _player(name, **overrides):
    snapshot = _snapshot(**overrides)
    snapshot.player = SimpleNamespace(web_name=name, position_short="MID")
    return snapshot


def test_candidates_are_ordered_by_projection():
    rows = [
        _player("Low", form=1.0, points_per_game=1.0, points_per_90=1.0),
        _player("High", form=9.0, points_per_game=9.0, points_per_90=9.0),
    ]
    result = captain_candidates(rows, 2)
    assert [row["player"].web_name for row in result] == ["High", "Low"]


def test_captain_points_are_double_the_projection():
    result = captain_candidates([_player("Ada")], 2)
    assert result[0]["captain_points"] == pytest.approx(
        result[0]["projection"] * 2
    )


def test_players_with_no_basis_are_excluded_not_ranked_last():
    """A None projection is unknown. Ranking it last would assert it is worst."""
    rows = [_player("Known"), _player("Unknown", expected_minutes=None)]
    result = captain_candidates(rows, 2)
    assert [row["player"].web_name for row in result] == ["Known"]


def test_a_double_gameweek_is_stated_in_the_reasoning():
    double = _player("Ada", upcoming_fixtures=[
        {"event": 2, "opponent": "OTH", "difficulty": 2, "is_home": True},
        {"event": 2, "opponent": "TST", "difficulty": 3, "is_home": False},
    ])
    result = captain_candidates([double], 2)
    assert result[0]["fixture_count"] == 2
    assert "two fixtures" in result[0]["reasoning"]


def test_confidence_is_low_when_expected_minutes_are_low():
    rotated = _player("Fringe", expected_minutes=30.0)
    assert captain_candidates([rotated], 2)[0]["confidence"] == "Low"


def test_no_gameweek_yields_no_candidates():
    assert captain_candidates([_player("Ada")], None) == []
```

- [ ] **Step 2: Run it to verify it fails**

Run: `pytest tests/test_captaincy.py -x -k candidates`
Expected: `ImportError: cannot import name 'captain_candidates'`.

- [ ] **Step 3: Write the implementation**

Append to `app/services/captaincy.py`:

```python
# Below this many expected minutes a captaincy recommendation is not worth
# making: the doubled downside of a benching outweighs the upside.
MINUTES_CONFIDENT = 75.0
MINUTES_TENTATIVE = 60.0


def _confidence(expected: float | None, fixture_count: int) -> str:
    if expected is None:
        return "Low"
    if expected >= MINUTES_CONFIDENT and fixture_count >= 1:
        return "High"
    if expected >= MINUTES_TENTATIVE:
        return "Medium"
    return "Low"


def _reasoning(fixtures: list[dict[str, Any]], expected: float | None) -> str:
    if not fixtures:
        return "No fixture in this gameweek, so no captaincy case."

    opponents = ", ".join(
        f"{fixture.get('opponent', '?')} "
        f"({'H' if fixture.get('is_home') else 'A'})"
        for fixture in fixtures
    )
    parts = []
    if len(fixtures) == 2:
        parts.append(f"Plays two fixtures this gameweek: {opponents}")
    elif len(fixtures) > 2:
        parts.append(f"Plays {len(fixtures)} fixtures this gameweek: {opponents}")
    else:
        parts.append(f"Faces {opponents}")

    if expected is not None:
        parts.append(f"expected around {expected:.0f} minutes")

    easiest = min(
        (fixture.get("difficulty", 3) for fixture in fixtures), default=3
    )
    if easiest <= 2:
        parts.append("against a low-difficulty opponent")
    elif easiest >= 4:
        parts.append("against a high-difficulty opponent")

    return ". ".join(parts) + "."


def captain_candidates(
    snapshots: list[Any], gameweek: int | None, limit: int = 8
) -> list[dict[str, Any]]:
    """Rank captaincy options for one gameweek.

    Players whose projection has no basis are excluded rather than ranked last.
    Ordering them at the bottom would assert they are the worst options, which
    is a claim the data does not support; their absence is the honest answer.
    """
    if gameweek is None:
        return []

    candidates = []
    for snapshot in snapshots:
        projection = next_gameweek_projection(snapshot, gameweek)
        if projection is None:
            continue
        fixtures = fixtures_in_gameweek(snapshot, gameweek)
        expected = getattr(snapshot, "expected_minutes", None)
        candidates.append(
            {
                "player": getattr(snapshot, "player", None),
                "projection": projection,
                "captain_points": round(projection * 2, 2),
                "fixture_count": len(fixtures),
                "opponents": fixtures,
                "expected_minutes": expected,
                "confidence": _confidence(expected, len(fixtures)),
                "reasoning": _reasoning(fixtures, expected),
            }
        )

    candidates.sort(key=lambda row: row["projection"], reverse=True)
    return candidates[:limit]
```

- [ ] **Step 4: Run the tests**

Run: `pytest tests/test_captaincy.py -v`
Expected: 11 passed.

- [ ] **Step 5: Commit**

```bash
git add app/services/captaincy.py tests/test_captaincy.py
git commit -m "Rank captain candidates, excluding those with no basis"
```

---

## Task 5: The captaincy page

**Files:**
- Modify: `app/services/season_state.py` (FEATURES registry)
- Modify: `app/services/queries.py:506-521`
- Modify: `app/web/auth.py:20` (PROTECTION_MAP)
- Modify: `app/web/routes.py`
- Create: `app/templates/captaincy.html`
- Modify: `app/templates/base.html`
- Test: `tests/test_in_season_rendering.py`, `tests/test_preseason_rendering.py`

**Interfaces:**
- Consumes: `captain_candidates` from Task 4; `readiness_for` from
  `app/services/season_state.py`.

- [ ] **Step 1: Write the failing tests**

Add `"/captaincy"` to the `PAGES` list in **both**
`tests/test_in_season_rendering.py` and `tests/test_preseason_rendering.py`.

Then append to `tests/test_in_season_rendering.py`:

```python
def test_captaincy_names_the_gameweek_it_is_advising_on(tmp_path):
    """A captaincy pick without a gameweek attached is not actionable."""
    client = _seeded(tmp_path, LiveClient, "cap.db")
    try:
        body = client.get("/captaincy").text
        assert "Gameweek 2" in body
    finally:
        app.dependency_overrides.clear()
```

And append to `tests/test_preseason_rendering.py`:

```python
def test_captaincy_declines_to_advise_in_preseason(tmp_path):
    """No match played means no expected minutes means no captaincy case."""
    client, _ = _seeded_client(tmp_path, PreseasonClient, "cap-pre.db")
    try:
        body = client.get("/captaincy").text
        assert "activates once" in body
        assert "Not available" in body or "not available" in body
    finally:
        app.dependency_overrides.clear()
```

- [ ] **Step 2: Run to verify failure**

Run: `pytest tests/test_preseason_rendering.py -k captaincy -v`
Expected: FAIL, 404 rather than 200.

- [ ] **Step 3: Register the feature requirement**

In `app/services/season_state.py`, add to the `FEATURES` dict after
`"differentials"`:

```python
    "captaincy": FeatureRequirement(
        required_inputs=("projections_available", "next_gameweek"),
        supported_states=IN_SEASON_STATES,
        minimum_sample={"projections_available": 15, "next_gameweek": 1},
        activates_when=(
            "Captaincy activates once expected minutes exist and a next "
            "gameweek is scheduled. Doubling a projection that has no minutes "
            "estimate behind it doubles the guess, not the information."
        ),
        fallback=(
            "None. Ranking by last season's points would be a recommendation "
            "about a season that has ended."
        ),
    ),
```

- [ ] **Step 4: Supply the input and readiness entry**

In `app/services/queries.py`, inside the readiness dict at line 506, add
`"next_gameweek"` to the inputs mapping:

```python
                "next_gameweek": state.get("next_gameweek"),
```

and extend the feature tuple on line 520:

```python
        for name in (
            "projections", "expected_minutes", "movers",
            "recommendations", "captaincy",
        )
```

- [ ] **Step 5: Add the route**

In `app/web/auth.py`, add to `PROTECTION_MAP` beside the other analytics
routes:

```python
    ("/captaincy", "PUBLIC"),
```

In `app/web/routes.py`, add:

```python
@router.get("/captaincy", response_class=HTMLResponse)
def captaincy_page(
    request: Request,
    db: Annotated[Session, Depends(get_db)],
    settings: Annotated[Settings, Depends(get_settings)],
):
    """Who to captain in the next gameweek, and how confident that is."""
    data = dashboard_data(db, settings.current_season)
    readiness = data["readiness"]["captaincy"]
    gameweek = data["season_state"].get("next_gameweek")

    candidates = []
    if readiness["state"] in (Readiness.READY, Readiness.STALE):
        candidates = captain_candidates(
            [row["snapshot"] for row in data["rows"]], gameweek
        )

    return templates.TemplateResponse(
        request=request,
        name="captaincy.html",
        context={
            "candidates": candidates,
            "gameweek": gameweek,
            "readiness": readiness,
            "season_state": data["season_state"],
            "season": data["season"],
        },
    )
```

Add the imports at the top of `routes.py`:

```python
from app.services.captaincy import captain_candidates
from app.services.season_state import Readiness
```

**Note for the implementer:** confirm the key holding the ORM snapshot in each
element of `data["rows"]`. Read `app/services/queries.py:dashboard_data` and use
the actual key; `row["snapshot"]` above is the expected name, not a verified one.
If rows are flat dicts rather than nested, pass the row objects directly —
`captain_candidates` uses `getattr`, so it needs objects, and a dict row will
need `SimpleNamespace(**row)` or a small accessor.

- [ ] **Step 6: Create the template**

Create `app/templates/captaincy.html`, following the existing structure of
`app/templates/differentials.html` for the readiness block and card markup:

```html
{% extends "base.html" %}
{% block title %}Captaincy{% endblock %}
{% block content %}
<h1>Captaincy{% if gameweek %} — Gameweek {{ gameweek }}{% endif %}</h1>

{% if not candidates %}
  <section class="notice">
    <h2>Not available</h2>
    <p>{{ readiness.explanation }}</p>
    <p class="muted">{{ readiness.activates_when }}</p>
    {% if readiness.fallback %}<p class="muted">{{ readiness.fallback }}</p>{% endif %}
  </section>
{% else %}
  <p class="muted">
    Projected for gameweek {{ gameweek }} only, counting every fixture in it.
    The captain figure is the projection doubled.
  </p>
  <ol class="captain-list">
    {% for row in candidates %}
      <li class="captain-card">
        <div class="captain-head">
          <span class="captain-name">{{ row.player.web_name }}</span>
          <span class="captain-position">{{ row.player.position_short }}</span>
          <span class="captain-points">{{ "%.1f"|format(row.captain_points) }}</span>
        </div>
        <p class="captain-reasoning">{{ row.reasoning }}</p>
        <p class="muted">
          Projection {{ "%.2f"|format(row.projection) }} ·
          Confidence {{ row.confidence }} ·
          {{ row.fixture_count }} fixture{{ "s" if row.fixture_count != 1 }}
        </p>
      </li>
    {% endfor %}
  </ol>
{% endif %}
{% endblock %}
```

Add the nav link in `app/templates/base.html` beside `/differentials`:

```html
<a href="/captaincy">Captaincy</a>
```

- [ ] **Step 7: Run the captaincy tests**

Run: `pytest tests/ -k captaincy -v`
Expected: all pass, in both preseason and the four in-season states.

- [ ] **Step 8: Run the whole suite**

Run: `pytest`
Expected: everything green.

- [ ] **Step 9: Commit**

```bash
git add app/services/captaincy.py app/services/season_state.py \
        app/services/queries.py app/web/routes.py app/web/auth.py \
        app/templates/captaincy.html app/templates/base.html \
        tests/test_captaincy.py tests/test_in_season_rendering.py \
        tests/test_preseason_rendering.py
git commit -m "Add captaincy for the next gameweek, silent when it has no basis"
```

---

## Task 6: Record what deployment now knows

**Files:**
- Modify: `DEPLOYMENT.md`
- Modify: `README.md` (feature list)

Two things learned on 2026-08-04 that are not written down anywhere.

- [ ] **Step 1: Record the `onnxruntime` gap**

`onnxruntime` is **not** a declared dependency — not in production
`dependencies`, not in the `train` extra (which holds only `numpy` and
`scikit-learn`). It was confirmed absent from the built image. Nothing is broken
today because no model is served, but the ONNX serving path at
`app/models/artefact.py:338` would fail on import the moment one is.

Add a fourth item to the checklist under "Deploying a trained model" in
`DEPLOYMENT.md`:

```markdown
4. **`onnxruntime` is actually installed in the image.** It is deliberately not
   a declared dependency, because nothing imports it while the heuristic is the
   only projection source and it would otherwise add roughly fifty megabytes to
   every deploy for no purpose. Confirmed absent from the image built on
   2026-08-04. Add it to `dependencies` in `pyproject.toml` in the same change
   that ships an artefact, never separately: the serving path at
   `app/models/artefact.py:338` imports it lazily, so a missing dependency
   surfaces as a request-time failure rather than a failed boot.
```

- [ ] **Step 2: Record the Docker validation**

Add to `DEPLOYMENT.md` under a new `## Local verification` heading:

```markdown
## Local verification

`docker compose up --build` was last verified on 2026-08-04: the image builds at
420MB, all eight migrations apply cleanly from empty against real PostgreSQL,
`/health` returns 200, and `torch` is absent from the image.

The compose stack sets `ACCESS_MODE=private`, so an anonymous request to `/`
returning **303** is the correct result and not a failure. Sign-in additionally
requires the CSRF token rendered into the login form; posting valid credentials
without it returns 401 by design. A scripted check must fetch `/login` first,
carry the session cookie, and submit the `csrf_token` field with the
credentials.

This run was the first time migrations `0005`–`0008` were applied to PostgreSQL
rather than SQLite. They applied without error. They remain unapplied in
production, deliberately.
```

- [ ] **Step 3: Update the README feature list**

Add to the bulleted list in `README.md`:

```markdown
- Next-gameweek captaincy ranking that counts double gameweeks and stays silent without expected minutes
```

- [ ] **Step 4: Commit**

```bash
git add DEPLOYMENT.md README.md
git commit -m "Record the Docker validation and the onnxruntime serving gap"
```

---

## Roadmap beyond this plan

Ordered by value per day of work, given a season that starts 21 August.

**1. Watch the real GW1 transition (21–23 August).** No amount of fake-client
coverage substitutes for the first real refresh with actual match data. Budget a
day around GW1 for observation. The specific things to check: `ranked_count`
becomes non-zero, `expected_minutes` becomes non-null, readiness flips to
`ready`, and the `CarryOverPreseasonClient` problem — the FPL API serving last
season's totals in preseason — has genuinely cleared.

**2. Transfer planning (multi-week).** The natural successor to captaincy: it
reuses `next_gameweek_projection` per gameweek across a horizon. Wait until
in-season data exists, because a planner built on preseason nulls cannot be
tested against anything real.

**3. Live gameweek tracking.** This is the one that needs
`SeasonState.LIVE`, which is declared at `app/services/season_state.py:24`,
labelled, and included in `IN_SEASON_STATES` but **never returned by
`season_state()`**. So does `FINALIZED`, and `HISTORICAL_SEASON`. Implementing
live tracking means first making `LIVE` reachable: it needs a kickoff-aware
check between `deadline_passed` and `provisional`. Also needs a polling cadence
faster than the daily cron, which is a genuine infrastructure change.

**4. Chip strategy.** Depends on transfer planning; low value before it exists.

**5. Elite cohort ownership.** Deferred deliberately: it needs collection from
sources outside the official API, which carries data-acquisition and
terms-of-service questions that none of the other items do. Do not start this
without deciding those questions first.

**6. AI analyst.** Deferred. It would narrate numbers the rest of the
application already shows; it adds trust surface without adding information.
Revisit only once the underlying numbers have a season of real validation.

**Explicitly not on this roadmap: revisiting the model.** That decision is
recorded in `docs/MODEL_EVALUATION.md` and the evidence is nine-fold. The one
circumstance that would reopen it is a decision that the *displayed projection
number* should minimise error rather than preserve rank order — a product
choice, not a modelling one.

---

## Self-Review

**Spec coverage.** This plan covers the season-readiness gap found on 2026-08-04
and one new decision surface. It does not attempt the remaining master-spec
areas; those are enumerated in the roadmap above with reasons for their ordering.
That is deliberate — attempting five surfaces in 17 days would produce five
half-built ones.

**Placeholder scan.** One item is knowingly under-specified: Task 5 Step 5 flags
that `row["snapshot"]` is an expected key rather than a verified one, and tells
the implementer to read `dashboard_data` and use the real accessor. That is
called out inline rather than hidden. Task 1 Step 5 states that some tests may
fail and that the failures are the deliverable — that is the nature of adding
coverage to untested paths, not a gap in the plan.

**Type consistency.** `captain_candidates` returns dicts with keys `player`,
`projection`, `captain_points`, `fixture_count`, `opponents`,
`expected_minutes`, `confidence`, `reasoning`. The template in Task 5 reads
`player.web_name`, `player.position_short`, `captain_points`, `reasoning`,
`projection`, `confidence`, `fixture_count` — all defined in Task 4.
`next_gameweek_projection` and `fixtures_in_gameweek` are defined in Task 3 and
consumed in Task 4 with matching signatures. The `readiness` dict keys used in
the template (`explanation`, `activates_when`, `fallback`, `state`) match the
return shape of `readiness_for` at `app/services/season_state.py:348`.
