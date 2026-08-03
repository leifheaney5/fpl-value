# Analytics

Raw Value is total points divided by current price in millions. Reliable Value
multiplies Raw Value by a transparent minutes/start/sample-size factor. Forward
Value is a heuristic projection across the next configured fixtures, adjusted
for form, points per game, points per 90, expected minutes, availability,
difficulty, and home advantage. Rotation Risk is a 0-100 heuristic where a
higher score means less secure starts and minutes. None of these metrics is an
official FPL forecast or a guarantee.

Ranks are calculated globally and separately within GKP, DEF, MID, and FWD.
Historical comparisons select the latest snapshot at or before each 1D, 7D,
and 30D target.

Forward model weights and fixture assumptions are configurable through the
`FORWARD_*`, `FIXTURE_DIFFICULTY_WEIGHT`, and `HOME_ADVANTAGE_FACTOR`
environment variables; defaults sum to the documented heuristic blend.

Set `COLLECT_GAMEWEEK_HISTORY=true` to fetch the public per-player
`element-summary` histories during refresh. This adds many API requests, so it
is opt-in and should be enabled only when the extra historical detail is
needed.

## Decision-layer methodology

The differential score is a calculated, ownership-aware heuristic: low
ownership (35%), forward value (up to 30 points), expected minutes (up to 20),
form (up to 10), availability (up to 10), and rotation safety (up to 5).
It is accompanied by a category and confidence indicator; it is not a
guarantee of upside.

Transfer-market totals are observed values from the latest official FPL
bootstrap snapshot. When a prior local snapshot contains comparable totals,
the application calculates net-transfer velocity and labels it Stable, Rising,
Declining, or Spiking. Otherwise it displays \`Not available\`, rather than
inventing a trend. Snapshot age over 48 hours is marked stale.

Template teams call the existing full-squad optimizer, which enforces the
15-player composition, budget, formation, and maximum-three-per-club rules.
Price-slot alternatives are same-position, at-or-below-price candidates sorted
by projected five-fixture points and forward value.

## Missing values, seasons and readiness

Three rules govern every number this application shows. They are documented in
full in `docs/METRICS.md` and `docs/SEASON_STATE.md`.

1. **A blank is not a zero.** Derived metrics are nullable. When the inputs for
   a metric do not exist — which is the case for everything derived from match
   data before a match has been played — the metric has no value and the
   interface says so. `0.00` means the calculation ran and the answer was zero.

2. **Seasons do not mix.** Every snapshot records the season it describes, every
   query filters on it, and differencing two seasons raises rather than
   returning a number. A value delta across a season rollover looks like player
   movement but measures a reset.

3. **Features refuse rather than mislead.** Each feature declares its required
   inputs, supported season states and minimum sample. When they are not met the
   feature reports `not_ready`, names the failing checks and states what would
   activate it, instead of producing a result its inputs cannot support.

The clearest example of rule 3 is the team builder. With no projections it
previously returned a legal squad costing about £40m with the rest of the budget
unspent — a real-looking recommendation with nothing behind it. It now refuses
and explains why.
