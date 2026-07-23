# FPL Value Studio

A modular, Railway-ready web application for Fantasy Premier League value
analysis. It replaces the single-file spreadsheet exporter with a persistent
database, a web dashboard, a scheduled refresh service, and on-demand
CSV/Excel exports.

## What is included

- FastAPI web application
- PostgreSQL production storage with SQLite local fallback
- Daily player and fixture snapshots
- Raw points-per-price value
- Reliability-adjusted value
- Position-relative value ranks and percentiles
- Fixture-based forward value
- Rotation-risk scoring
- Points per game, minute, start, team match, and 90
- Player history charts
- One-day, seven-day, and 30-day value, price, ownership, and rank movement
- Color-coded movers dashboard and page
- Player comparison
- Transfer Finder with budget, minutes, risk, percentile, ownership, and availability constraints
- Model Diagnostics page that remains explicit when insufficient history exists
- Searchable and filterable player explorer
- API schema-change history
- Optional per-player gameweek history collection through the public element-summary endpoint
- CSV and styled Excel exports
- Optional username/password protection
- Railway Docker and cron configuration
- Automated tests

## Architecture

```text
Railway Project
├── Web service
│   └── FastAPI + Jinja + Chart.js
├── PostgreSQL
│   └── Players, fixtures, snapshots, refresh runs, schema history
└── Cron service
    └── Same Docker image, terminating refresh command
```

The web and cron services share the same PostgreSQL database. The scheduled
service performs one refresh and exits; it does not run a permanent worker.

## Repository structure

```text
app/
├── api/          FPL HTTP client
├── analytics/    Value, reliability, projection, and rotation formulas
├── db/           SQLAlchemy models and sessions
├── services/     Refresh pipeline, queries, and exports
├── templates/    Server-rendered application screens
├── static/       Application styles
├── web/          Authentication and routes
├── cli.py        Database and refresh commands
└── main.py       FastAPI entrypoint
```

## Run locally with Docker

```bash
docker compose up --build
```

Open:

```text
http://localhost:8000
```

The local credentials in `docker-compose.yml` are:

```text
admin / admin
```

Run the first refresh from the dashboard.

## Run locally without Docker

Create a Python 3.12 environment and install:

```bash
pip install -e ".[dev]"
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload
```

Run a refresh:

```bash
python -m app.cli refresh --force
```

Run tests:

```bash
pytest
```

## Deploy to Railway

### 1. Push the project to GitHub

Create a repository and commit the entire package.

### 2. Create the web service

In Railway:

1. Create a new project.
2. Deploy the GitHub repository.
3. Add a PostgreSQL service to the project.
4. Give the application service access to the PostgreSQL `DATABASE_URL`.
5. Set the application variables below.
6. Generate a public domain for the web service.

Required variables:

```text
DATABASE_URL
SESSION_SECRET
APP_USERNAME
APP_PASSWORD
APP_TIMEZONE=America/New_York
REFRESH_HOUR=10
COLLECT_GAMEWEEK_HISTORY=false
```

Generate a strong session secret, for example:

```bash
python -c "import secrets; print(secrets.token_urlsafe(48))"
```

The Dockerfile is detected automatically. The default service command is:

```bash
bash scripts/start-web.sh
```

The health check endpoint is:

```text
/health
```

### 3. Create the daily cron service

Create a second Railway service from the same GitHub repository.

Set its start command to:

```bash
bash scripts/run-refresh.sh --scheduled
```

Configure it as a Railway Cron Job with:

```cron
0 14,15 * * *
```

Railway cron schedules use UTC. Running at both 14:00 and 15:00 UTC allows the
application to preserve a 10:00 AM `America/New_York` refresh across daylight
saving changes. The CLI checks local time and exits without refreshing when
the local hour is not 10.

Give the cron service the same `DATABASE_URL`, `APP_TIMEZONE`,
`REFRESH_HOUR`, and other model settings as the web service.

The cron process is designed to complete and exit.

### 4. Populate the database

After deployment, sign into the web application and click **Refresh FPL
data**. Future refreshes are handled by the cron service.

## Authentication

Authentication is enabled when both variables are present:

```text
APP_USERNAME
APP_PASSWORD
```

For a public read-only application, leave both unset. A private deployment is
strongly recommended while the application is single-user.

## Data model

The database stores:

- Current player identity and team metadata
- Fixtures
- Immutable player snapshots
- Refresh-run status and errors
- Schema fields and detected additions/removals

Spreadsheet exports are generated from the current database state rather than
used as the source of truth.

## Formula notes

### Raw value

```text
Total points / current price
```

### Reliable value

Raw value multiplied by a reliability factor derived from sample size, start
share, and minute share.

### Rotation risk

A 0–100 heuristic using season start share and minute share, plus a recent
seven-day comparison once sufficient history exists.

### Forward value

Projected points over the configured future-fixture window divided by current
price. Projection inputs include form, points per game, points per 90,
expected minutes, availability, fixture difficulty, and home advantage.

These are transparent analytical heuristics, not official FPL predictions.

## Production notes

- Use PostgreSQL on Railway rather than SQLite.
- Do not attach a volume merely to store database history; PostgreSQL is the
  durable source of truth.
- Export files are generated on demand and streamed to the browser.
- Keep the web and cron services on the same code revision.
- Review `/schema` after FPL launches a new season or changes its API fields.


## Migrating from the local v7/v8 exporter

The Railway application uses PostgreSQL snapshots as its source of truth.
Import supported CSV snapshots with:

```bash
python -m app.cli import-history --directory "/path/to/fpl_exports/history"
```

Imports match rows by Player ID, preserve timestamps, retain the original row
payload, record skipped rows, and are idempotent. Fields unavailable in the
legacy file cannot be reconstructed and remain at safe defaults.
