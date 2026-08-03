# Season state and feature readiness

Season state used to be inferred wherever a page needed it, which meant pages
could disagree about whether the season had started. `app/services/season_state.py`
is now the single answer. It is a pure function of the stored gameweek calendar,
the latest snapshot time and the current instant, so it is tested without a
database.

The calendar itself is new. The bootstrap `events` payload was previously parsed
only to detect schema changes and then discarded, leaving the application unable
to name the current gameweek or the next deadline. It is now persisted in the
`gameweeks` table.

## States

| State | Condition |
| --- | --- |
| `uninitialized` | No gameweeks collected. Run a refresh. |
| `fpl_unavailable` | A calendar exists but no player snapshot has been captured. |
| `pre_launch` | Gameweeks exist but no deadline is set. |
| `preseason` | No gameweek has finished and the first deadline is more than 24 hours away. Metrics derived from match data are unavailable. |
| `pre_deadline` | The next deadline is within 24 hours. |
| `deadline_passed` | The current gameweek's deadline has passed and it is not finished. |
| `live` | Reserved for live scoring, which is not implemented. |
| `provisional` | The current gameweek has finished but bonus and final data are not confirmed. |
| `finalized` | Reserved; `provisional` resolves into `gameweek_open` once data is checked. |
| `international_break` | The next deadline is 10 or more days away. |
| `gameweek_open` | Between deadlines, with football scheduled. |
| `postseason` | Every gameweek is finished. |
| `historical_season` | Reserved for browsing a completed past season. |

`live`, `finalized` and `historical_season` are declared but not yet reachable;
they need live scoring and a season browser, neither of which is implemented.

The result also carries `data_is_stale`, set when the newest snapshot is more
than 48 hours old, so a valid state can still warn that its inputs are old.

## Readiness

Every feature declares its requirements in the `FEATURES` registry rather than
inferring them at the call site:

- required and optional inputs
- supported season states
- minimum sample per input
- freshness window
- the fallback in use, if any
- the condition that would activate the feature

`readiness_for(feature, inputs, state, age_hours)` returns one of:

| Verdict | Meaning |
| --- | --- |
| `ready` | All required inputs present and current. |
| `degraded` | Running with reduced inputs. |
| `fallback` | Running on a declared substitute method. |
| `not_ready` | Cannot run. The response names the missing inputs and the activation condition. |
| `stale` | Runnable, but the data is beyond the freshness window. |
| `error` | Inputs are invalid, or the feature is not registered. |

Registered features today: `projections`, `expected_minutes`, `movers`,
`recommendations`, `differentials`.

## What a page should do with each verdict

- `ready` — show the result.
- `stale` — show the result with a refresh prompt.
- `degraded` / `fallback` — show the result, labelled with the method in use.
- `not_ready` — show the refusal panel (`_readiness.html`), listing the failed
  checks and the activation condition. Do not show a partial result. The point
  of the refusal is that a result built from these inputs would look like an
  answer without being one.
- `error` — show the invalid inputs; this is a bug or a source problem.

## Worked example: preseason

In `preseason` with no matches played:

- `expected_minutes` → `not_ready`; team matches is 0 and 1 is required.
- `projections` → `not_ready`; projections depend on expected minutes.
- `recommendations` → `not_ready`; the team builder refuses and names the
  failing checks rather than returning a squad. This is the fix for the
  minimal-cost squad with a full bank.
- `differentials` → `not_ready`; low ownership alone is not evidence, so no
  score is calculated.
- `movers` → `not_ready` until a second snapshot exists.

No fallback is currently declared for any of them. A preseason projection model
built on previous-season data would be the fallback for `projections` and
`expected_minutes`; it is not implemented, and the registry says so rather than
implying coverage that does not exist.
