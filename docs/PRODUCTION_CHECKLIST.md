# FPL Studio production checklist

This checklist is evidence-based. Mark a gate complete only with the command
output or artifact named beside it. Do not print secret values, credentials,
cookies, response bodies containing personal data, or database URLs.

## Release identity

- [ ] Working tree, branch, last commit, and intended changed paths recorded.
- [ ] Railway project `e54df4c5-d15f-48cc-a164-2fedafa9c7cc`, environment
      `ca46ed57-89c4-4861-9970-2b85770b285d`, and web service
      `c69cfd06-a450-4620-9e8d-af62130028d1` confirmed with read-only commands.
- [ ] Exact submitted deployment ID recorded and polled to terminal `SUCCESS`.
- [ ] Image digest and public domain status recorded separately from deployment
      status.

## Code and schema gates

- [ ] Focused tests pass for the changed task.
- [ ] Canonical verification passes:
      `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 python -m pytest -q`.
- [ ] `python -m compileall -q app tests` passes.
- [ ] `git diff --check` passes.
- [ ] Diff contains no `.env`, secret, database file, generated export, or
      unrelated reformat.
- [ ] Any migration was verified against local PostgreSQL before production;
      backup and rollback decisions are recorded.

## Railway configuration gates

- [ ] A PostgreSQL service exists in the same project/environment.
- [ ] The web service receives a PostgreSQL `DATABASE_URL`; SQLite is not used
      for production storage.
- [ ] Required non-secret variables are configured: `ACCESS_MODE`,
      `CURRENT_SEASON`, `APP_TIMEZONE`, `REFRESH_HOUR`, and
      `COLLECT_GAMEWEEK_HISTORY`.
- [ ] Owner-supplied `FPL_ENTRY_ID` is configured when My Team is enabled.
- [ ] `SESSION_SECRET`, `APP_USERNAME`, and `APP_PASSWORD` are configured as
      secret variables without their values appearing in logs or this file.
- [ ] Web healthcheck is `/health`, and the process binds `0.0.0.0` on the
      injected `PORT`.
- [ ] The scheduled refresh service uses the same image/revision,
      `RUN_REFRESH_ONLY=true`, the shared variables, and a UTC schedule that
      brackets the configured New York refresh hour.
- [ ] A controlled refresh records bootstrap/fixture counts, a completed
      `RefreshRun`, and PostgreSQL dialect.
- [ ] A wrong-hour scheduled invocation is a no-op and creates no duplicate
      refresh run.

## Live behavior gates

- [ ] `GET /health` returns HTTP 200 and `{"status":"ok"}`.
- [ ] `/`, `/fixtures`, `/performance`, and `/recommendation` return their
      documented status codes and headings.
- [ ] At least one completed refresh is visible in the data-status panel; if
      none has completed, the UI says “No refresh has completed yet”.
- [ ] Newest available My Team event is recorded from the selected picks
      snapshot, regardless of FPL event flags.
- [ ] Failed refreshes retain the last valid snapshot and show a stale/error
      state rather than presenting an older event as current.
- [ ] Anonymous responses contain no entry ID, manager, squad, or personal
      freshness metadata.
- [ ] Authenticated `/my-team` and `/diagnostics` behavior is checked only with
      intentionally supplied credentials; credentials remain in memory and are
      never printed.

## Historical and deployment boundaries

The self-hosted `linux-leif` stack and its PostgreSQL database are separate
deployment history. Do not claim that a Railway success updated the
self-hosted host, or that self-hosted history proves the Railway database is
ready. Importing retired self-hosted data into Railway requires a separate
backup, row-count, and rollback decision; otherwise record Railway as a fresh
production baseline.

No push, merge, commit, migration against shared production, or deployment is
authorized by this checklist alone. Release integration requires an explicit
authorization in the task conversation.
