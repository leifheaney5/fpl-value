# Deployment

The web service runs `bash scripts/start-web.sh`, which applies `alembic upgrade
head` and starts Uvicorn on Railway's `PORT`. The cron service runs
`bash scripts/run-refresh.sh --scheduled` and exits after one refresh.

Create a Railway project with PostgreSQL, then deploy this repository twice:
the web service uses `railway.json`; the cron service uses the refresh command.
For the cron service, set `RUN_REFRESH_ONLY=true` and configure its Railway
service cron schedule to `0 14,15 * * *`. The shared entrypoint also detects
this variable, so an uploaded cron service terminates after its refresh instead
of starting Uvicorn.

## Required variables

```bash
DATABASE_URL=<provided by the Railway PostgreSQL plugin>
SESSION_SECRET=<long random value; the default is a known string>
ACCESS_MODE=demo            # or private
APP_USERNAME=<username>
APP_PASSWORD=<long random password>
CURRENT_SEASON=2026/27
```

`ACCESS_MODE` decides who can read what, and defaults to `demo`: market
analytics are public, while the linked team, exports of personal data, and every
mutation require sign-in. Use `private` to require sign-in for everything. See
`docs/SECURITY.md` for the full matrix and for Tailscale setup.

Credentials are no longer optional in effect. If they are absent, personal and
mutation routes stay closed rather than opening — the previous behaviour, where
missing credentials disabled authentication entirely, is fixed.

`CURRENT_SEASON` labels every snapshot written by the refresh pipeline and scopes
every query. Set it before the season rolls over; leaving it stale will file new
snapshots under the previous season.

Configure cron as `0 14,15 * * *`; the CLI checks `APP_TIMEZONE` and
`REFRESH_HOUR` to avoid duplicate daylight-saving refreshes.

## Deploying a trained model

Model migrations `0005`–`0008` add the archive columns, the stable player code,
the fixture-aware history key and the `predictions` table. They are additive and
safe, but there is no reason to apply them until a model is worth serving.

Before deploying a model, check three things:

1. **It cleared the gate on the nine-fold walk-forward**, not just on a single
   holdout season. `scripts/train.py` checks one holdout, which is enough to
   refuse an obviously bad model and not enough to justify shipping a good one.
   A single favourable holdout has misled repeatedly on this project; see
   `docs/MODEL_EVALUATION.md`.
2. **The artefact's `feature_version` matches the running code.**
   `app/services/predictions.py` refuses to serve a mismatch rather than
   producing numbers that do not mean what they claim.
3. **`holdout_gate_passed` is true and `forced` is false** in the manifest. Note the name: that field records a single-holdout check only, and `walk_forward_confirmed` is what says the nine-fold run agreed.

Deploy with the artefact directory present in the image:

```bash
git push origin <branch>
railway up --service web --detach
```

`scripts/start-web.sh` runs `alembic upgrade head`, so the migrations apply on
boot. Watch the deploy logs for `Running upgrade 0007 -> 0008` before assuming
the predictions table exists.

**Rolling back a model** does not require a redeploy of the application: point
at a previous artefact directory. Each carries its own manifest recording model
version, feature version, training seasons, seed and held-out scores.

Verify `/health`, then run the first refresh inside the deployed container:

```bash
railway ssh --service web -- python -m app.cli refresh --force
```

Do not use `railway run` for this production operation: it runs the command on
the local machine with Railway variables injected and therefore requires local
Python, SQLAlchemy, and `psycopg` dependencies. The importer likewise requires
an actual local history directory, for example:

```bash
python -m app.cli import-history \
  --directory "C:\\Users\\you\\Documents\\fpl_exports\\history" \
  --season 2025/26
```

`--season` is required and is never inferred. These files usually hold
previous-season data; an untagged import would land in the same table as the
current season, where a value delta across the boundary reads as player
movement rather than a season rollover.
