# Recommender audit, 2026-08-04

The live deployment recommended Crystal Palace's third-choice goalkeeper as the
best player in the game, selected him, captained him, and labelled the squad
"Safe Starters". This records what was wrong, how it was proven, and what
changed.

## What was observed

```
GKP Benitez    £4.5  proj_out=35.0  ppg=7.0  pts=7    mins=90    starts=1
FWD Haaland    £15.5 proj_out=34.0  ppg=6.8  pts=239  mins=2953  starts=34
```

Benítez outranked every player in the game on 7 points from one 90-minute
appearance. Crystal Palace's actual first-choice keeper was rated far below him:

```
Henderson  £5.0  ppg=3.5  mins=3330  starts=37
Benitez    £4.5  ppg=7.0  mins=90    starts=1
Matthews   £4.0  ppg=0.0  mins=0     starts=0
```

Three of the top thirty players by projected output were goalkeepers.

## The mechanism

Reproduced against the live FPL API: `max team_matches` was 0 — no match had
been played this season — yet `points_per_game > 0` for 400 of 568 players.
Those are **last season's** figures, the carry-over hazard already documented by
`CarryOverPreseasonClient` in `tests/fakes.py`.

1. `projected_points_5` was null for all 568 players, correctly: no fixture had
   been played, so there was no basis for a projection.
2. `_projected_output` fell back to `points_per_game * 5`.
3. `points_per_game` is a **rate with no sample-size guard**. One lucky
   appearance produces a higher rate than a full season of elite output.
4. The minutes discount that might have caught it defaulted to the best case:
   `min(expected_minutes / 450, 1.0) if expected_minutes else 1.0`. In preseason
   `expected_minutes` is null, so every player was treated as playing full
   minutes.

## Everything that followed from it

| Symptom | Cause |
| --- | --- |
| A backup keeper ranked first | Carry-over rate with no minutes or sample floor |
| He was captained | `max(starting, key=_output_or_zero)` with no position guard |
| Labelled "Safe Starters" | `safe` weights `reliable_value`, `forward_value` and `rotation_risk`, all null in preseason. `safe` and `best_team` returned 10 of 11 identical players |
| The not-ready gate never fired | `validate_pool` measured the fallback, so it saw a spread of 6.619 against a 0.05 threshold |
| The dashboard disagreed with the page | The dashboard reads `projected_points_5`; the recommender read the fallback. Two definitions of "ready" |
| Every player's justification identical | `decorate()` reads only null-in-preseason fields, so all fifteen fell through to the literal string `"best available fit"` |

## Root cause

The trust layer worked. `expected_minutes()` returns `None` when
`team_matches <= 0`, and `project_next_fixtures()` returns `None` without it.
The analytics layer had already refused to use this data.

The recommender reached around that layer to raw snapshot fields and
re-derived a projection from the data the analytics layer had rejected. The
guard existed; the caller bypassed it.

## What changed

- **The `points_per_game` fallback is removed.** No projection means no
  projected output, `validate_pool` fails honestly, and `/recommendation` and
  `/templates` show their not-ready panel until a match has been played.
- **Unknown expected minutes returns `None`, not full credit.** Belt and braces
  — `projected_points_5` is already null without expected minutes — but the old
  default was a fail-open in an application whose premise is that missing is
  not zero.
- **Captaincy excludes goalkeepers on position**, via `_captaincy_pair()`, not
  on projection. A keeper cannot attack; their ceiling is a clean sheet plus
  save points. This must not depend on the numbers happening to rank someone
  else first.
- **`"best available fit"` is gone.** When no threshold fires, the reason now
  states the number the selection was actually made on, or says plainly that
  there is no measured basis.

## Regression tests

`tests/test_recommender.py`:

- `test_last_season_points_per_game_is_not_a_projection`
- `test_recommender_refuses_while_only_carry_over_data_exists`
- `test_the_captain_is_never_a_goalkeeper` (across four strategies)
- `test_a_selected_player_is_never_justified_by_an_empty_default`

`_carry_over_pool()` reproduces preseason as the FPL API actually serves it,
including the fringe player whose small-sample rate leads the pool.

## Note on test stubs

Two existing stubs set `projected_points_5` without `expected_minutes` — a
state the refresh pipeline cannot produce, since `project_next_fixtures()`
returns `None` without it. They were corrected rather than the guard weakened.
Hand-built stubs carry whatever fields the code asks for, which is why they did
not catch any of this; the reproduction that did was run against the live API.
