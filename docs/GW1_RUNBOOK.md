# GW1 runbook — 21 August 2026

The deadline is **21 August, 17:30 UTC**; the first fixtures finish around
**19:00 UTC**. That is the moment the application has never experienced with
real data: one finished fixture switches on six behaviours simultaneously.

`tests/test_season_transition.py` rehearses this against fake clients and the
flip is correct there. This runbook covers what fakes cannot: whether the real
FPL API behaves the way the fakes assume.

Base URL: `https://web-production-f5979.up.railway.app`

---

## 1. Refresh manually once fixtures finish

**The cron will not do this for you in time.** It runs at 14:00 and 15:00 UTC,
before kick-off. The first in-season data needs a manual refresh:

```bash
railway ssh --service web -- python -m app.cli refresh --force
```

Expect `Refresh success: ~570 players, 0 schema changes`. A non-zero schema
change count is worth reading — FPL sometimes adds fields at season start, and
`/schema` lists them.

Do **not** use `railway run`: it executes locally with Railway variables
injected, not inside the container.

## 2. Confirm a fixture actually registered

Everything below depends on `team_matches > 0`. Check `/diagnostics`.

If it is still zero after a refresh, the fixtures endpoint has not marked the
match finished yet. Wait and refresh again rather than changing anything.

## 3. Confirm the carry-over labels have gone

Load `/spreadsheet`. The string `2025/26` must not appear — no banner, no column
tags.

If it persists, the carry-over test is `team_matches == 0 and (total_points > 0
or minutes > 0)` in `app/web/routes.py:spreadsheet` and the stored
`metric_status` in `app/services/refresh.py`. Both key off `team_matches`, so a
label surviving means step 2 did not really succeed.

## 4. Confirm the numbers are plausible — the important one

**After one match, a season total should be single digits.** Check any
well-known player on `/spreadsheet`:

| Column | Expected after GW1 |
| --- | --- |
| Points | roughly 0–15 |
| Minutes | 0–90 |
| PPG | roughly 0–15 |
| Value | roughly 0–3 |
| P/90 | blank for most — the 270-minute floor is not met after one match |

**A player showing 200 points means last season's data is being reported as this
season's.** That is the defect that ranked a one-appearance keeper above
Haaland, in its most visible form. If you see it, stop and read
`docs/RECOMMENDER_AUDIT.md` before trusting anything else on the page.

Note that P/90 being blank for nearly everyone is correct, not a fault:
`P90_MIN_MINUTES = 270` and one match gives at most 90.

## 5. Confirm the dormant features woke up

| Page | Expected |
| --- | --- |
| `/captaincy` | A ranked shortlist, **not** the not-ready panel |
| `/recommendation` | A 15-player squad, not the blocked-checks panel |
| `/templates` | Template squads |
| `/differentials` | Scored players |

Two specific checks on `/captaincy`:

- **The top pick must not be a goalkeeper.** Guarded on position in
  `_captaincy_pair`, so a keeper appearing means that guard has regressed.
- The reasoning should name the opponent and expected minutes, not a generic
  phrase.

One on `/recommendation`:

- **No selected player should be justified by "no measured basis".** That string
  is the honest fallback when a projection is absent; seeing it fifteen times
  means projections did not activate.

## 6. Sanity-check against reality

This is the check tests cannot perform. Take the three highest-scoring players
in the actual gameweek — from the official FPL site — and confirm the sheet
agrees on their points.

Every automated check above can pass while the numbers describe the wrong
players, the wrong season, or the wrong gameweek. This is the only step that
catches that, and it is the reason it is on the list.

## 7. Watch the second refresh

The cron runs at 14:00/15:00 UTC on 22 August. Confirm on `/diagnostics` that a
scheduled refresh succeeded without a manual trigger — that is the first
evidence the cron service works on in-season data.

Both services must be on the same revision; check with:

```bash
railway status
```

## If something is wrong

Snapshots are append-only, so no data is lost and a bad refresh is not
destructive. Roll back the application with:

```bash
git checkout <previous-good-commit>
railway up --service web --detach
railway up --service cron --detach
```

Then refresh again. The previous snapshots remain intact and the next refresh
recomputes from the current code.

Do not edit data directly to make a page look right. Every number on the sheet
is derived at refresh time from the API payload plus the stored history; a
hand-edited snapshot will be silently overwritten and will have hidden whatever
the real defect was.
