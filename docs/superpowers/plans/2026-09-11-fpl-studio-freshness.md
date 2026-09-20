# FPL Studio Data Freshness Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make My Team and current FPL data select the newest available snapshot, expose freshness provenance internally, and prevent stale or out-of-order refreshes from winning.

**Architecture:** Add a small typed in-process freshness cache with dataset-specific policies, generation-aware invalidation, stale-if-error behavior, and single-flight loading. Keep the existing FPL entry/picks API contract and server-rendered routes; current team revalidation remains page-level and does not mutate the database.

**Tech Stack:** Python 3.12, FastAPI, synchronous `httpx`, SQLAlchemy, pytest, standard-library `dataclasses`, `threading`, and `logging`.

**Spec:** `docs/superpowers/specs/2026-09-11-fpl-studio-freshness-fixtures-performance-design.md`

## Global Constraints

- My Team selects the highest-numbered event whose picks endpoint returns a valid non-empty picks list.
- Current picks use a 60-second cache and manual refresh bypasses the normal TTL.
- Failed revalidation keeps the last valid value visible but marks it stale.
- No private FPL login/session automation is introduced.
- No credentials or `.env` files are read or committed.
- The canonical suite is `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest`.

---

### Task 1: Add the typed freshness cache

**Files:**
- Create: `app/services/freshness.py`
- Create: `tests/test_freshness.py`

**Interfaces:**
- Produces `FreshnessPolicy`, `FreshnessRecord`, and `FreshnessCache` for the FPL services.
- Defines named policies for My Team (60 seconds), current event/fixtures (5 minutes),
  player market/bootstrap metadata (10 minutes), and historical data (24 hours).
- `FreshnessCache.get(key: str, loader: Callable[[], T], policy: FreshnessPolicy, *, force: bool = False) -> FreshnessRecord[T]` returns a fresh record, a cached record, or a stale last-valid record.
- `FreshnessCache.invalidate(key: str | None = None) -> None` increments generations and removes cached values.

- [ ] **Step 1: Write the failing tests**

```python
def test_cache_returns_metadata_and_reuses_fresh_value():
    calls = []
    cache = FreshnessCache(clock=lambda: 100.0)
    policy = FreshnessPolicy("team", ttl_seconds=60)

    first = cache.get("entry:7", lambda: calls.append(1) or {"event": 4}, policy)
    second = cache.get("entry:7", lambda: calls.append(1) or {"event": 5}, policy)

    assert first.value == {"event": 4}
    assert first.cache_hit is False
    assert second.cache_hit is True
    assert second.expires_at == 160.0
    assert calls == [1]


def test_failed_revalidation_returns_last_valid_value_as_stale():
    now = [100.0]
    cache = FreshnessCache(clock=lambda: now[0])
    policy = FreshnessPolicy("team", ttl_seconds=60, stale_if_error=True)
    cache.get("entry:7", lambda: {"event": 4}, policy)
    now[0] = 161.0

    stale = cache.get("entry:7", lambda: (_ for _ in ()).throw(RuntimeError("timeout")), policy)

    assert stale.value == {"event": 4}
    assert stale.stale is True
    assert stale.last_error == "timeout"
    assert stale.fetched_at == 100.0


def test_force_invalidation_prevents_older_generation_from_winning():
    cache = FreshnessCache(clock=lambda: 100.0)
    policy = FreshnessPolicy("team", ttl_seconds=60)
    cache.get("entry:7", lambda: {"event": 4}, policy)
    cache.invalidate("entry:7")

    refreshed = cache.get("entry:7", lambda: {"event": 5}, policy)

    assert refreshed.value == {"event": 5}
    assert refreshed.generation == 1
```


The in-flight test should use two `threading.Event` objects: block an expired
loader after it starts, call `invalidate("entry:7")`, release the blocked loader,
then load the new generation and assert that the cache contains only the newer
value. The old loader's completion must not overwrite the post-invalidation
value.

- [ ] **Step 2: Run the tests and verify the expected failure**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_freshness.py -q`

Expected: collection or assertion failure because `app.services.freshness` and its cache interfaces do not yet exist.

- [ ] **Step 3: Implement the minimal cache**

Implement `FreshnessPolicy` and `FreshnessRecord` as dataclasses. Store records and per-key generation counters behind one `threading.RLock`; use a per-key condition map so only one loader runs for a missing/expired key while concurrent callers wait for the result. On loader failure, return the previous record with `stale=True` only when `stale_if_error` is enabled; otherwise re-raise. Set `cache_hit` to `False` for a newly loaded value and `True` for a valid cached value.

- [ ] **Step 4: Run the focused tests**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_freshness.py -q`

Expected: all freshness-cache tests pass.

- [ ] **Step 5: Commit the cache unit**

```bash
git add app/services/freshness.py tests/test_freshness.py
git commit -m "Add generation-aware freshness cache"
```

### Task 2: Instrument and reuse FPL HTTP requests

**Files:**
- Modify: `app/api/fpl_client.py`
- Modify: `tests/test_web.py` or create `tests/test_fpl_client.py`

**Interfaces:**
- `FPLClient(settings: Settings, http_client: httpx.Client | None = None)` accepts an optional request-scoped client.
- `_get_json(url: str, *, dataset: str | None = None) -> Any` logs duration, status, and retry count without logging response bodies or credentials.
- `FPLClient.close()` closes a client created by the instance but never closes an injected client.

- [ ] **Step 1: Write the failing request instrumentation tests**

Use `httpx.MockTransport` and `caplog` to assert one successful request logs the dataset, elapsed duration, status code, and `retries=0`; a two-failure-then-success response logs `retries=2` and returns the successful JSON.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_fpl_client.py -q`

Expected: failure because the constructor cannot accept an injected client and no timing fields are emitted.

- [ ] **Step 3: Implement request-scoped client reuse and bounded logging**

Create one `httpx.Client` per `FPLClient` when no client is injected, preserve the existing timeout and retry behavior, measure with `time.perf_counter()`, and log only endpoint path/dataset, status, duration, and retries. Add context-manager support so routes can close internally-created clients after rendering. Use the named policies from Task 1 at the dataset boundaries; persisted historical rows remain the preferred 24-hour source.

- [ ] **Step 4: Run focused and API tests**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_fpl_client.py tests/test_my_team.py -q`

Expected: all focused tests pass.

- [ ] **Step 5: Commit the HTTP client unit**

```bash
git add app/api/fpl_client.py tests/test_fpl_client.py
git commit -m "Instrument and reuse FPL HTTP clients"
```

### Task 3: Rebuild My Team freshness and newest-event selection

**Files:**
- Modify: `app/services/my_team.py`
- Modify: `app/web/routes.py`
- Modify: `app/templates/my_team.html`
- Modify: `tests/test_my_team.py`
- Modify: `tests/test_access.py`

**Interfaces:**
- `REMOTE_CACHE_POLICY = FreshnessPolicy("my_team", ttl_seconds=60)` is the sole policy for linked-team data.
- `_remote_team_data(client: FPLClient, entry_id: int, *, force: bool = False) -> dict[str, Any]` returns the existing team payload plus an internal `_freshness` record.
- `clear_remote_team_cache(entry_id: int | None = None) -> None` invalidates the entry generation.

- [ ] **Step 1: Add failing regression tests**

Add tests that (a) an unflagged event 5 beats flagged event 4 when both picks payloads are valid, (b) a forced refresh bypasses a valid cached event 4 and returns event 5, (c) an FPL error keeps event 4 with `stale=True`, and (d) two event candidates cannot let an older completion overwrite a newer generation.

- [ ] **Step 2: Run My Team tests and verify the new cases fail**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_my_team.py -q`

Expected: the existing event-ordering test may pass, but the new force/stale/generation assertions fail against the single-TTL dictionary cache.

- [ ] **Step 3: Implement newest-available selection, bounded parallelism, and freshness metadata**

Build candidates from every valid bootstrap event ID plus `entry.current_event`, sort descending, call `entry_picks` until a valid non-empty picks list is returned, and record the selected event. Fetch the entry and bootstrap calendar concurrently with a bounded two-worker executor; keep event-specific picks in descending order and fetch history only after the selected picks snapshot is known. Wrap this loader in `FreshnessCache`; manual refresh invalidates the entry before redirecting. Preserve cached valid data on transient errors and add `_freshness = {"dataset", "fetched_at", "expires_at", "event", "cache_hit", "stale", "last_error"}` for development logging and template context. The in-flight regression test must prove an old generation cannot publish after manual invalidation.

- [ ] **Step 4: Update the page copy without exposing noisy diagnostics**

Render a compact stale/error notice only when `_freshness.stale` is true, label the picks section with the returned event number, and leave the existing My Team fixture layout unchanged. Do not render timestamps or endpoint details in normal fresh state.

- [ ] **Step 5: Run focused tests and compile checks**

Run: `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest tests/test_my_team.py tests/test_access.py -q` and `python -m compileall -q app tests`

Expected: all focused tests pass and compileall exits zero.

- [ ] **Step 6: Commit the My Team freshness unit**

```bash
git add app/services/my_team.py app/web/routes.py app/templates/my_team.html tests/test_my_team.py tests/test_access.py
git commit -m "Make My Team freshness generation-aware"
```

## Plan Verification

After all tasks, run `$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD='1'; python -m pytest`, `python -m compileall -q app tests`, and `git diff --check`. Verify that no `.env` file is staged and that the deployed smoke test reports the selected event metadata without exposing response bodies or secrets.
