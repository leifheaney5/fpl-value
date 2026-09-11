# FPL Studio Freshness, Fixtures, Performance, and Response-Time Design

## Scope

This increment improves the reliability and responsiveness of FPL Studio while
adding two decision surfaces:

- a Fixtures page that ranks teams by the difficulty of their next ten
  fixtures; and
- a Performance page that shows each team's current form across its last ten
  completed matches.

The highest priority is data correctness. The application must not silently
present a previous gameweek as current, and a failed refresh must not replace a
newer valid result with an older response.

The existing My Team fixture presentation and the approved Performance visual
direction are retained. This design does not introduce a frontend framework or
rewrite the server-rendered FastAPI/Jinja application.

## Current evidence

The current My Team service fetches an entry, bootstrap calendar, and
gameweek-specific picks through separate synchronous FPL requests. Its cache
has a single TTL and does not expose fetch metadata. Event selection previously
gave special priority to `is_next` and `is_current`, which can select the wrong
snapshot when FPL flags lag or when a newer event is otherwise available.

The response path also contains avoidable database work. `latest_rows()` loads
historical comparisons for every player, diagnostics then calls
`player_history()` once per player, and comparison options request the full
latest-row structure even though they only need current player metadata.
Dashboard and recommendation flows can load the same latest rows more than
once. Cold personal requests additionally wait on sequential external FPL
requests.

## Data freshness architecture

Add a small central freshness policy and metadata model used by external-data
services. Each cached value records:

- `fetched_at`;
- `expires_at`;
- the dataset key;
- the selected gameweek where applicable;
- whether the returned value was a cache hit; and
- whether the value is stale because a revalidation failed.

Initial policies are:

| Dataset | Policy | Failure behavior |
| --- | --- | --- |
| My Team entry and picks | 60-second cache; manual refresh bypasses it | Keep the last valid snapshot, mark it stale, and log the error |
| Current event and live/current fixtures | 5-minute cache | Keep the last valid value with stale metadata |
| Player market/bootstrap metadata | 10-minute cache | Use the last valid value when available |
| Historical player/team results | 24-hour cache or persisted database rows | Prefer persisted history; never label it as current |

My Team chooses the highest-numbered event whose picks endpoint returns a valid
non-empty picks list. It does not rely on `is_current`, `is_next`, or
`entry.current_event` for ordering; those fields remain useful metadata only.
The entry event is included as a fallback when it is absent from bootstrap.

Cache refreshes use per-dataset single-flight coordination so concurrent
requests do not issue duplicate fetches. A fetch may update a cache entry only
if it belongs to the current generation of that dataset; an older in-flight
response cannot overwrite a newer refresh. Manual My Team refresh increments
the entry's generation and bypasses the normal TTL.

When no valid value has ever been fetched, the route returns the existing
unavailable state. When a previous valid value exists but revalidation fails,
the value remains visible with a small stale indicator and internal metadata.
The UI never describes stale data as current.

FPL request instrumentation records endpoint, duration, status, retry count, and
cache outcome through the application logger. A request-timing middleware logs
the route, total duration, and response status. These are development and
operations signals, not a user-facing analytics system.

## Fixtures page

Add `GET /fixtures` and a navigation link. The service layer reads the current
team and unfinished fixture records already stored by the refresh pipeline.
For each team it selects the next ten fixtures ordered by event, kickoff, and
fixture ID. Each fixture view contains:

- team and opponent names;
- home/away direction;
- gameweek or `Not assigned` when FPL has not assigned one;
- official FPL difficulty from 1 through 5; and
- a badge URL derived from the FPL team identifier, with an accessible team
  abbreviation fallback.

Teams are ranked by mean difficulty across the available next-ten set, with
fewer available fixtures and missing difficulty shown as incomplete rather than
converted to an artificial zero. Lower mean difficulty is better. The page
shows the average difficulty out of five and a ten-cell fixture strip. Difficulty
cells use a continuous green-to-red visual scale: 1 is easiest, 3 is neutral,
and 5 is toughest. The page remains useful with fewer than ten fixtures and
states the available count explicitly.

## Performance page

Add `GET /performance` and a navigation link. The service derives team results
from finished stored fixtures and their FPL score fields. For every team it
keeps the latest ten completed matches, displays them in chronological order,
and calculates:

- wins, draws, and losses;
- total result points;
- points per game;
- goals for; and
- goals against.

The default ranking is points per game, followed by total points and team name
for deterministic ties. Each result cell shows the opponent, score, and W/D/L
state with the existing green/yellow/red visual language. Fixtures without
validated scores are excluded from form calculations and marked as incomplete;
they are never treated as scoreless losses.

## Response-time improvements

Make the following targeted changes without adding a broad application cache or
weakening freshness:

1. Replace diagnostics' per-player history query loop with one grouped query.
2. Add a lightweight current-player-options query for comparison screens.
3. Pass already-loaded latest rows into My Team and recommendation consumers
   where the same request already computed them.
4. Reuse a request-scoped FPL client and parallelize independent entry,
   bootstrap, history, and picks work after its event candidates are known.
5. Keep fixture/form services to bounded projections over teams and fixtures,
   rather than calling the FPL API once per team.
6. Add query-count and timing probes in tests so regressions are measurable
   without relying on a fragile wall-clock threshold in CI.

The first request after process start may still pay external API latency. It
must remain bounded by the client timeout/retry policy and should return the
last valid dataset when possible. Server-rendered pages will keep their current
immediate HTML response model; no whole-page spinner or speculative stale
replacement is introduced.

## Navigation and responsive behavior

Change the header to a three-column layout: flexible left branding, intrinsic
center navigation, and flexible right controls. This keeps the navigation's
visual center independent of unequal brand/account widths. On tablet and mobile
the navigation collapses according to the existing responsive behavior without
overlapping the controls or changing the established visual language.

## Error handling and provenance

All external-data failures are logged with dataset, endpoint, event, retry, and
duration context. Routes distinguish among:

- fresh data;
- cached-but-valid data;
- stale last-valid data; and
- no data available.

Existing database refresh and manual-refresh behavior remains authoritative for
persisted market data. Page-level current-team revalidation does not mutate the
database. No credentials or private FPL session data are introduced by this
increment; an entry ID can only retrieve what the configured FPL endpoints make
available.

## Validation

Tests will cover:

- newest available My Team event selection, including unflagged newer events;
- cache hit, expiry, manual bypass, stale-on-error, and generation ordering;
- fixture ranking, next-ten truncation, FDR display, badge fallback, and
  missing-data behavior;
- last-ten form calculations, tie ordering, and score-missing behavior;
- route rendering for empty, partial, and populated datasets;
- centered navigation at desktop and narrow viewport widths; and
- query-count regressions for diagnostics, comparison options, dashboard, and
  recommendation paths.

The canonical test suite remains `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m
pytest`. Verification also includes `python -m compileall -q app tests`,
`git diff --check`, and a production health smoke test after deployment.

## Non-goals

- No React, SPA router, or new frontend dependency.
- No destructive database migration or replacement of the existing source of
  truth.
- No private FPL login/session automation.
- No invented results when FPL scores or fixture difficulty are absent.
- No redesign of the approved My Team fixtures or Performance visual language.
