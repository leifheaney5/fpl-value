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

```
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
