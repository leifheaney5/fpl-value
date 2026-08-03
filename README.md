# FPL Value Studio

A modular, Railway-ready web application for Fantasy Premier League value
analysis. It replaces the single-file spreadsheet exporter with a persistent
database, a web dashboard, a scheduled refresh service, and on-demand
CSV/Excel exports.

## Before you deploy

Set `ACCESS_MODE`, `APP_USERNAME`, `APP_PASSWORD` and `SESSION_SECRET`. Access
control fails closed: absent credentials keep personal pages and mutations shut
rather than opening them. `ACCESS_MODE` defaults to `demo`, which serves the
impersonal market analytics publicly and requires sign-in for anything about
your own team. See [docs/SECURITY.md](docs/SECURITY.md).

## Reading the numbers

A blank or "Not available" reading is not a zero. Metrics whose inputs do not
exist yet — everything derived from match data, before a match has been played —
have no value and say so, rather than displaying `0.00`. See
[docs/METRICS.md](docs/METRICS.md) for each metric's formula and null behaviour,
and [docs/SEASON_STATE.md](docs/SEASON_STATE.md) for why a feature may decline
to produce a result.

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
- Differential Finder with transparent ownership-aware scores, categories, confidence, and freshness labels
- Transfer Market dashboard with current inbound/outbound/net activity and locally-derived trend status where history permits
- Valid strategy templates and same-position affordable price-slot alternatives, reusing the existing squad-rule recommender
- Model Diagnostics page that remains explicit when insufficient history exists
- Searchable and filterable player explorer
- API schema-change history
- Optional per-player gameweek history collection through the public element-summary endpoint
- CSV and styled Excel exports
- Fail-closed access control with demo, private and local modes
- Audit log of sign-ins and mutations
- Explicit season identity on every snapshot, with cross-season comparison rejected
- Missing-versus-zero metric semantics with per-metric status and reason
- Centralised season state and per-feature readiness
- Ten seasons of historical per-gameweek data for model training
- Point-in-time feature builder with a proven no-leakage guarantee
- Walk-forward backtesting against six baselines
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

Run the first production refresh from the dashboard, or execute inside the
deployed web container:

```bash
railway ssh --service web -- python -m app.cli refresh --force
```

`railway run` only injects Railway variables into a command running on your
local machine; it does not run inside the deployed container.

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
ACCESS_MODE=demo
APP_USERNAME
APP_PASSWORD
CURRENT_SEASON=2026/27
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

Access is controlled by `ACCESS_MODE`, which defaults to `demo`:

| Mode | Analytics | Personal | Mutations |
| --- | --- | --- | --- |
| `demo` | Public | Sign-in | Sign-in |
| `private` | Sign-in | Sign-in | Sign-in |
| `local` | Open | Open | Open (SQLite only) |

Leaving `APP_USERNAME` and `APP_PASSWORD` unset does **not** make the
application public. It leaves personal pages and mutations closed with no way to
open them. This is deliberate: the previous behaviour, where absent credentials
disabled the authentication check entirely, published the linked manager's name
and entry ID and left the refresh endpoint open to anyone.

Full details, including Tailscale setup, are in
[docs/SECURITY.md](docs/SECURITY.md).

## Model training data

Historical per-gameweek data comes from the MIT-licensed
[vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League)
archive, covering 2016-17 to 2025-26:

```bash
python -m app.cli import-archive                 # all seasons
python -m app.cli import-archive --season 2024-25
```

Evaluate the baselines with walk-forward backtesting:

```bash
python -m app.cli evaluate --output docs/evaluation-baseline.json
```

See [docs/MODEL_EVALUATION.md](docs/MODEL_EVALUATION.md) for the recorded
results and the bar a trained model has to clear.

Train a model offline and export it for serving:

```bash
pip install -e ".[train]"
python scripts/train.py --output models/preseason_v1 --state preseason
```

Training refuses to write an artefact that has not beaten the deployed
heuristic on the held-out season. That is deliberate: a model that loses to
what it replaces would make the application worse. Pass `--force` to override,
and the override is recorded in the artefact's manifest.

Training uses PyTorch and runs on a development machine. Production loads the
exported ONNX graph through `onnxruntime` and never imports torch, which keeps
roughly 800MB of training stack out of the deployed image.

**Rolling back** is pointing at a previous artefact directory: each one carries
its own manifest recording the model version, feature version, training seasons,
seed and held-out scores.

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

### Decision surfaces

\`/differentials\` ranks low-owned players using the existing forward value,
expected minutes, form, availability, and rotation-risk inputs. It exposes the
score components and marks each record as calculated from the latest official
FPL snapshot. \`/transfer-market\` displays the current transfer-event totals
from that same snapshot; a change-versus-prior-snapshot is only shown when
local history exists. \`/templates\` produces valid 15-player squads using the
existing feasibility-first recommender and presents affordable alternatives for
each selected player's position/price slot.

These screens do not claim intraday transfer feeds, elite-manager ownership,
external injury news, or trained prediction-model outputs. Those require
additional source-specific collection and validation.

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
python -m app.cli import-history --directory "C:\\Users\\you\\Documents\\fpl_exports\\history"

Replace the example path with the folder that actually contains the legacy
CSV files. The importer reports a missing directory instead of silently doing
nothing.
```

Imports match rows by Player ID, preserve timestamps, retain the original row
payload, record skipped rows, and are idempotent. Fields unavailable in the
legacy file cannot be reconstructed and remain at safe defaults.
