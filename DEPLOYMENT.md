# Deployment

The web service runs `bash scripts/start-web.sh`, which applies `alembic upgrade
head` and starts Uvicorn on `PORT`. The refresh job runs
`bash scripts/run-refresh.sh --scheduled` and exits after one refresh.

## Current Railway target (not yet production-ready)

The active Railway project and web service are retained as the next release
target. A successful container deployment is not evidence that the application
has durable data or completed a refresh; the target must first have PostgreSQL,
the scheduled refresh service, and the variables listed in
[`docs/PRODUCTION_CHECKLIST.md`](docs/PRODUCTION_CHECKLIST.md).

| Resource | Value |
| --- | --- |
| Project | `fpl-value-studio` (`e54df4c5-d15f-48cc-a164-2fedafa9c7cc`) |
| Environment | `production` (`ca46ed57-89c4-4861-9970-2b85770b285d`) |
| Web service | `fpl-value-studio` (`c69cfd06-a450-4620-9e8d-af62130028d1`) |
| Domain | `https://fpl-value-studio-production.up.railway.app` |
| Web start | `bash scripts/start-web.sh` |
| Refresh start | `bash scripts/run-refresh.sh --scheduled` |

Railway production must use PostgreSQL. The local SQLite fallback is for
development and tests only; it is not durable production storage. Never treat
HTTP 200, an uploaded image, or a Railway deployment status alone as proof of
data readiness. Record the exact deployment ID, terminal status, database
dialect, refresh result, and public route checks in the production checklist.

**Production runs on `linux-leif`**, in Docker, behind the host Caddy instance,
at <https://fpl-studio.leif.media>. Railway hosted this application until
2026-09-02 and is retained unchanged as a fallback; see [Railway (previous
host)](#railway-previous-host).

## Self-hosted on linux-leif (current)

`deploy/fpl-studio/` holds the production stack: a `postgres:18-alpine`
database, the application, and a profiled one-shot `refresh` service that host
cron invokes. The full runbook — first deploy, redeploy, rollback, backup,
troubleshooting — is in [`deploy/fpl-studio/README.md`](deploy/fpl-studio/README.md).

| | |
| --- | --- |
| Host | `linux-leif`, tailnet `media` / `100.84.43.115`, as user `leif` |
| Checkout | `/home/leif/fpl-value-studio` |
| Compose file | `/home/leif/fpl-value-studio/deploy/fpl-studio/docker-compose.yml` |
| Loopback port | `127.0.0.1:8788` |
| Public name | `fpl-studio.leif.media`, routed by the host Caddy |
| Refresh | host cron, hourly at `5 * * * *` |

Caddy binds the Tailscale address only, so the site answers **on the tailnet and
nowhere else**. This is the material difference from Railway, which served it on
the public internet.

`ACCESS_MODE` is set to `local` accordingly: there is no sign-in, and tailnet
membership is the whole access control. That pairing is deliberate and asserted,
not inferred — `TRUSTED_NETWORK=true` is what permits `local` alongside a
networked database, and it is a claim about where the deployment sits. **If this
host is ever published beyond the tailnet, change `ACCESS_MODE` back to
`private` and `TRUSTED_NETWORK` to `false` in the same edit**, or the site is
open to everyone who can reach it.

TLS needs no per-host work. Caddy already holds a `*.leif.media` wildcard
certificate issued by ACME DNS-01 through Porkbun, and `*.leif.media` resolves
to the Tailscale address, so a new subdomain is a Caddyfile entry and a reload.

## Required variables

Set in `deploy/fpl-studio/.env` (mode `600`, never committed; see
`.env.example`). `PORT` and `DATABASE_URL` are set by `docker-compose.yml` and
must not be duplicated there.

```bash
POSTGRES_PASSWORD=<long random value>
SESSION_SECRET=<long random value; the default is a known string>
ACCESS_MODE=local           # or private, or demo
TRUSTED_NETWORK=true        # required by `local` on a networked database
APP_USERNAME=<username>     # inert under `local`; retained for a switch back
APP_PASSWORD=<long random password>
CURRENT_SEASON=2026/27
```

`SESSION_SECRET` is required even with no sign-in: it signs the session cookie
that carries the CSRF token.

`ACCESS_MODE` decides who can read what, and defaults to `demo`: market
analytics are public, while the linked team, exports of personal data, and every
mutation require sign-in. `private` requires sign-in for everything. `local`
requires it for nothing, and is what this deployment runs. See
`docs/SECURITY.md` for the full matrix and for Tailscale setup.

Credentials are not optional in effect. If they are absent under `demo` or
`private`, personal and mutation routes stay closed rather than opening — the
old behaviour, where missing credentials disabled authentication entirely, is
fixed. `local` is the one mode that opens them, which is why it must be asked
for by name and, on a networked database, seconded by `TRUSTED_NETWORK`.

`CURRENT_SEASON` labels every snapshot written by the refresh pipeline and scopes
every query. Set it before the season rolls over; leaving it stale will file new
snapshots under the previous season.

Host cron runs the refresh hourly at `5 * * * *` — five past, because FPL price
changes and gameweek rollovers land on the hour. The compose `refresh` service
sets `REFRESH_HOURLY=true`, which makes every scheduled invocation refresh.
Without it the CLI refreshes only in `REFRESH_HOUR` (in `APP_TIMEZONE`) and any
other invocation is a no-op, which is how the previous daily schedule worked.

Hourly refreshes do not grow storage. Each refresh stores about 1.4 MB of
snapshots, so an unpruned hourly cadence would add roughly 12 GB a year. Every
successful refresh therefore collapses days older than
`SNAPSHOT_HOURLY_KEEP_HOURS` (default 48) to their final snapshot, for the
current season only. Day, week and month movement read the latest snapshot at or
before a target time, so they are unaffected.

## Local verification

`docker compose up --build` was last verified on 2026-08-04: the image builds at
420MB, all eight migrations apply cleanly from empty against real PostgreSQL,
`/health` returns 200, and `torch` is absent from the image.

The compose stack sets `ACCESS_MODE=private`, so an anonymous request to `/`
returning **303** is the correct result and not a failure. Sign-in additionally
requires the CSRF token rendered into the login form; posting valid credentials
without it returns 401 by design. A scripted check must fetch `/login` first,
carry the session cookie, and submit the `csrf_token` field with the
credentials.

This run was the first time migrations `0005`–`0008` were applied to PostgreSQL
rather than SQLite. They applied without error. They remain unapplied in
production, deliberately.

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
4. **`onnxruntime` is actually installed in the image.** It is deliberately not
   a declared dependency, because nothing imports it while the heuristic is the
   only projection source and it would otherwise add roughly fifty megabytes to
   every deploy for no purpose. Confirmed absent from the image built on
   2026-08-04. Add it to `dependencies` in `pyproject.toml` in the same change
   that ships an artefact, never separately: the serving path at
   `app/models/artefact.py` imports it lazily, so a missing dependency surfaces
   as a request-time failure rather than a failed boot.

Deploy with the artefact directory present in the image. **`models/` is in
`.gitignore`**, so the deploy path in
[`deploy/fpl-studio/README.md`](deploy/fpl-studio/README.md), which ships a
`git archive` of the tracked tree, will not carry an artefact. Copy it across
explicitly in the same step:

```bash
# from the repository root, on the workstation
scp -r models/<artefact> leif@100.84.43.115:/home/leif/fpl-value-studio/models/
ssh leif@100.84.43.115 \
  'cd /home/leif/fpl-value-studio/deploy/fpl-studio && docker compose up -d --build app'
```

`scripts/start-web.sh` runs `alembic upgrade head`, so the migrations apply on
boot. Watch `docker compose logs -f app` for `Running upgrade 0007 -> 0008`
before assuming the predictions table exists.

Migration `0012` creates `player_season_aggregates` empty. Populate it once per
environment that has the archive imported, or "Show past seasons" reports that
nothing has been built:

```bash
python -m app.cli build-season-aggregates
```

**Rolling back a model** does not require a redeploy of the application: point
at a previous artefact directory. Each carries its own manifest recording model
version, feature version, training seasons, seed and held-out scores.

Verify `/health`, then run the first refresh inside the deployed container:

```bash
ssh leif@100.84.43.115 \
  'cd /home/leif/fpl-value-studio/deploy/fpl-studio && docker compose exec app python -m app.cli refresh --force'
```

Run it in the container, not on the workstation. Unlike the Railway database,
which was reachable through a public TCP proxy, this Postgres is published to no
host port at all and answers only on the `fpl-studio_fpl` Docker network. There
is no workstation-side connection to make.

That also changes how history is imported. The importer needs the actual history
files, so copy them to the host and mount them into a one-off container:

```bash
scp -r "C:\Users\you\Documents\fpl_exports\history" leif@100.84.43.115:/tmp/history
ssh leif@100.84.43.115 'cd /home/leif/fpl-value-studio/deploy/fpl-studio && \
  docker compose run --rm -v /tmp/history:/history app \
    python -m app.cli import-history --directory /history --season 2025/26'
```

`--season` is required and is never inferred. These files usually hold
previous-season data; an untagged import would land in the same table as the
current season, where a value delta across the boundary reads as player
movement rather than a season rollover.

## Railway (previous arrangement and historical cutover)

Railway previously served this application publicly, with `ACCESS_MODE=demo`,
until the self-hosted cutover on 2026-09-02. The prior Railway database and
deployment arrangement are historical; the current project and service IDs
above are the explicit release target and must not be conflated with the
self-hosted production database.

Its cron service was already failing before the migration — `refresh_runs` 49
and 50 both recorded `failed`, and the service showed `Crashed`. That is a fault
the move resolved, not one it introduced.

The only surviving copy of the Railway database is
`/home/leif/fpl-migrate/fpl.dump` on `linux-leif`, taken at cutover. The
self-hosted database has been the authoritative copy since, so that dump is of
historical interest only.

`scripts/start-web.sh` honours `RUN_REFRESH_ONLY=true` so a second Railway
service can share one image and exit after refreshing. The self-hosted stack
uses a dedicated `refresh` service instead; that is separate deployment
history, not evidence that the Railway scheduled service is configured.
