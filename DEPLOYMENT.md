# Deployment

The web service runs `bash scripts/start-web.sh`, which applies `alembic upgrade
head` and starts Uvicorn on Railway's `PORT`. The cron service runs
`bash scripts/run-refresh.sh --scheduled` and exits after one refresh.

Create a Railway project with PostgreSQL, then deploy this repository twice:
the web service uses `railway.json`; the cron service uses the refresh command.
Provide `DATABASE_URL`, `SESSION_SECRET`, and (for a private deployment)
`APP_USERNAME` and `APP_PASSWORD`. Configure cron as `0 14,15 * * *`; the CLI
checks `APP_TIMEZONE` and `REFRESH_HOUR` to avoid duplicate daylight-saving
refreshes.

Verify `/health`, then run `python -m app.cli refresh --force` once. Railway
deployment cannot be claimed from this checkout without authenticated Railway
access and a verified public endpoint.
