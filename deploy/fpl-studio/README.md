# FPL Value Studio on linux-leif

Production runbook for the self-hosted stack. Deployment policy, variables and
model-artefact handling are in [`../../DEPLOYMENT.md`](../../DEPLOYMENT.md).

| | |
| --- | --- |
| Host | `linux-leif`, tailnet `media` / `100.84.43.115`, user `leif` |
| Checkout | `/home/leif/fpl-value-studio` |
| Stack | `/home/leif/fpl-value-studio/deploy/fpl-studio` |
| URL | <https://fpl-studio.leif.media> (tailnet only) |
| Loopback | `127.0.0.1:8788` |
| Containers | `fpl-studio-app-1`, `fpl-studio-postgres-1` |
| Volume | `fpl-studio_fpl_postgres` |
| Refresh | host cron, hourly at `5 * * * *` → `/home/leif/fpl-studio-refresh.log` |
| Monitoring | host cron, `*/5 * * * *` → ntfy topic `fpl-studio` |
| Access | `ACCESS_MODE=local` — no sign-in; the tailnet is the access control |

## Reachability

Caddy runs on the host network and binds the Tailscale address only, so the site
answers on the tailnet and nowhere else. The app publishes its port on loopback
and Postgres publishes no host port at all.

The route lives in the `*.leif.media` block of
`/home/leif/media-server-stack/config/caddy/Caddyfile`:

```caddyfile
@fplstudio host fpl-studio.leif.media
handle @fplstudio {
	reverse_proxy 127.0.0.1:8788
}
```

`*.leif.media` already resolves to the Tailscale address and Caddy already holds
a wildcard certificate for it, so no DNS or ACME work is needed for this name.
After editing, validate before reloading — a bad Caddyfile takes down every
service on this host, not just this one:

```bash
docker exec caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile
docker exec caddy caddy reload   --config /etc/caddy/Caddyfile --adapter caddyfile
```

## Redeploy

The stack builds from a checkout of the tracked tree, not from a Git remote.
Ship the working tree from the workstation:

```powershell
git archive --worktree-attributes HEAD -o repo.tar
tar -cf deploy.tar deploy/fpl-studio
scp repo.tar deploy.tar leif@100.84.43.115:/tmp/
ssh leif@100.84.43.115 'set -e
  cd /home/leif/fpl-value-studio
  tar -xf /tmp/repo.tar && tar -xf /tmp/deploy.tar
  chmod +x scripts/*.sh deploy/fpl-studio/*.sh
  cd deploy/fpl-studio && docker compose up -d --build app'
```

`--worktree-attributes` is load-bearing. `.gitattributes` pins `*.sh` to LF, and
without that flag `git archive` on a Windows clone with `core.autocrlf=true`
emits CRLF; `set -euo pipefail\r` then fails at boot with
`set: pipefail: invalid option name` and the container restart-loops. Check with
`file scripts/*.sh` after extracting — it must not say `CRLF line terminators`.

`.env` is not in either archive and is never overwritten by a deploy.

## Refresh

Cron runs the refresh every hour:

```cron
5 * * * * /home/leif/fpl-value-studio/deploy/fpl-studio/run-refresh-task.sh >> /home/leif/fpl-studio-refresh.log 2>&1
```

The `refresh` service sets `REFRESH_HOURLY=true`; without it only the
`REFRESH_HOUR` invocation does anything. Storage stays flat because each refresh
thins days older than 48 hours to one snapshot. `monitor.sh` alerts when the
newest successful refresh is more than 3 hours old.

`run-refresh-task.sh` is what cron runs; run it by hand to reproduce cron
exactly rather than approximating it:

```bash
/home/leif/fpl-value-studio/deploy/fpl-studio/run-refresh-task.sh
```

It starts the `refresh` service, which sits behind the `tasks` profile so
`docker compose up` never starts it, and shares the `fpl-studio-app:local` image
so a refresh never triggers a rebuild. Force one outside the scheduled hour with
`docker compose exec app python -m app.cli refresh --force`.

## Health

```bash
docker compose ps
docker compose logs --tail 50 app
curl -s http://127.0.0.1:8788/health          # {"status":"ok"}
curl -so /dev/null -w '%{http_code}\n' https://fpl-studio.leif.media/health
```

`ACCESS_MODE=local`, so an anonymous `GET /` returns **200** and there is no
sign-in anywhere. A **303** to `/login` means the access mode is not what this
runbook assumes — check `ACCESS_MODE` and `TRUSTED_NETWORK` in `.env`.

That mode is safe only because Caddy binds the Tailscale address, so the host is
not publicly reachable. Publishing this host without first setting
`ACCESS_MODE=private` and `TRUSTED_NETWORK=false` exposes the linked team and
every mutation route to anyone who can reach it.

## Database

Postgres is `18-alpine`, matching the Railway server this was migrated from —
`pg_dump` output cannot be restored into an older major. It mounts
`/var/lib/postgresql`, not `/var/lib/postgresql/data`: `postgres:18` relocated
PGDATA, and the old path leaves the volume empty and silently re-initialises the
cluster on every boot.

Reach it through the Docker network; it has no host port:

```bash
docker compose exec postgres psql -U fpl -d fpl
```

Back up and restore:

```bash
docker compose exec -T postgres pg_dump -Fc -U fpl fpl > fpl-$(date +%F).dump
cat fpl-2026-09-02.dump | docker compose exec -T postgres \
  pg_restore --clean --if-exists --no-owner --no-privileges -U fpl -d fpl
```

There is **no automated backup for this stack yet** — see Known gaps.

## Monitoring

`monitor.sh` runs every five minutes from cron and pushes to the local ntfy at
`http://127.0.0.1:8090`, topic **`fpl-studio`** — subscribe to that topic to
receive alerts. It follows the NewLeaf watchdog on this host: alerts fire on
**state change only**, so a sustained outage is one notification rather than one
every five minutes, and recoveries are announced too.

Checks: app `/health`, the public `https://fpl-studio.leif.media/health` (which
catches a broken Caddy while the app itself is fine), both containers running,
freshness of the newest successful `refresh_runs` row, and root disk usage.

The refresh check is the one that earns its keep. This stack has no backups by
design, so the realistic failure is not data loss but silence: cron stops firing
or the FPL API rejects every attempt, the site stays up, and it serves numbers
that are days stale with nothing looking wrong. It alerts when the newest
success is older than `REFRESH_MAX_AGE_HOURS` (default 30, which tolerates one
missed daily run).

Run it by hand at any time — it is idempotent:

```bash
/home/leif/fpl-value-studio/deploy/fpl-studio/monitor.sh
cat /home/leif/.fpl-studio-monitor/*     # current state of each check
```

To rehearse an alert, write `fail` into one of those state files and run it
again; the next run reports a recovery.

## Rollback

`/home/leif/fpl-migrate/fpl.dump` is the pre-cutover Railway database as of
2026-09-02, kept for rollback. The Railway project itself is still running and
untouched; pointing DNS or a browser back at it is the fastest rollback, at the
cost of whatever the self-hosted instance has refreshed since.

## Known gaps

- **No automated backup, by decision.** Unlike the NewLeaf stack on this host,
  nothing backs this database up. Most of the data is re-derivable by
  re-ingesting from the FPL API. The exception is `player_snapshots`: it is a
  daily time series, so anything lost cannot be reconstructed after the fact,
  only re-accumulated going forward. Take an ad-hoc `pg_dump` (see Database)
  before anything risky.
