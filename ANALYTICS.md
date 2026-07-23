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

Set `COLLECT_GAMEWEEK_HISTORY=true` to fetch the public per-player
`element-summary` histories during refresh. This adds many API requests, so it
is opt-in and should be enabled only when the extra historical detail is
needed.
