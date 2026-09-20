#!/bin/bash
# FPL Value Studio watchdog. Runs from cron; alerts through the local ntfy.
#
# Design notes, following the NewLeaf watchdog on this host:
#   * An external uptime service cannot see a Tailscale-only host, so this runs
#     on the box and pushes to ntfy, which already has an iOS app.
#   * It alerts on STATE CHANGE, not on every run, so a sustained outage does
#     not produce a notification every five minutes. Recovery is announced too,
#     because "it went quiet" and "it came back" are both worth knowing.
#   * It checks the things that fail silently. This stack has no backups by
#     design, so the data risk is different from NewLeaf's: what matters is that
#     the daily refresh keeps landing. A refresh that quietly stops leaves the
#     site up and serving numbers that are days stale, which is worse than an
#     outage because nothing looks wrong.
#
# Usage (cron):
#   */5 * * * * /home/leif/fpl-value-studio/deploy/fpl-studio/monitor.sh
set -uo pipefail

STACK_DIR="$(cd "$(dirname "$0")" && pwd)"
NTFY_URL="${NTFY_URL:-http://127.0.0.1:8090}"
NTFY_TOPIC="${NTFY_TOPIC:-fpl-studio}"
APP_URL="${APP_URL:-http://127.0.0.1:8788}"
SITE_URL="${SITE_URL:-https://fpl-studio.leif.media}"
STATE_DIR="${STATE_DIR:-/home/leif/.fpl-studio-monitor}"
# The refresh runs hourly. Allow two missed runs plus slack before
# alerting, so a single transient FPL API failure is not paged at 10:05.
REFRESH_MAX_AGE_HOURS="${REFRESH_MAX_AGE_HOURS:-3}"

mkdir -p "$STATE_DIR"

COMPOSE="docker compose -f $STACK_DIR/docker-compose.yml"

notify() { # notify <priority> <tags> <title> <body>
  curl -s --max-time 15 \
    -H "Priority: $1" -H "Tags: $2" -H "Title: $3" \
    -d "$4" "$NTFY_URL/$NTFY_TOPIC" >/dev/null 2>&1 || true
}

# Fires only when a check flips state, so a long outage stays one notification.
report() { # report <check-name> <ok|fail> <message>
  local name="$1" status="$2" message="$3"
  local file="$STATE_DIR/$name"
  local previous="unknown"
  [ -f "$file" ] && previous="$(cat "$file")"
  printf '%s' "$status" > "$file"
  [ "$status" = "$previous" ] && return 0
  if [ "$status" = "fail" ]; then
    notify urgent "rotating_light" "FPL Studio: $name FAILED" "$message"
  elif [ "$previous" = "fail" ]; then
    notify default "white_check_mark" "FPL Studio: $name recovered" "$message"
  fi
}

# ---------------------------------------------------------------- app health
health="$(curl -s --max-time 20 "$APP_URL/health" 2>/dev/null)"
if printf '%s' "$health" | grep -q '"status":"ok"'; then
  report app ok "The app is responding again."
else
  report app fail "GET /health did not return status ok. Response: ${health:-<none>}"
fi

# Checked separately from the app: Caddy can be down, or its config broken by an
# unrelated service on this host, while the app itself is perfectly healthy.
site_code="$(curl -s -o /dev/null -w '%{http_code}' --max-time 20 "$SITE_URL/health" 2>/dev/null)"
if [ "$site_code" = "200" ]; then
  report site ok "$SITE_URL is reachable again."
else
  report site fail "$SITE_URL/health returned ${site_code:-no response}. Caddy or TLS may be broken."
fi

# ------------------------------------------------------------- containers up
expected="app postgres"
missing=""
for service in $expected; do
  state="$($COMPOSE ps --format '{{.Service}} {{.State}}' 2>/dev/null | awk -v s="$service" '$1==s {print $2}')"
  [ "$state" = "running" ] || missing="$missing $service(${state:-absent})"
done
if [ -z "$missing" ]; then
  report containers ok "All FPL Studio containers are running again."
else
  report containers fail "Containers not running:$missing"
fi

# ---------------------------------------------------------- refresh freshness
# The failure this exists to catch: cron stops firing, or the refresh fails
# every day against the FPL API. The site stays up and stale either way.
age_hours="$($COMPOSE exec -T postgres psql -U fpl -d fpl -Atc \
  "select floor(extract(epoch from (now() - max(started_at))) / 3600)::int
     from refresh_runs where status = 'success'" 2>/dev/null | tr -dc '0-9')"

if [ -z "$age_hours" ]; then
  report refresh fail "Could not read refresh_runs; the database may be unreachable."
elif [ "$age_hours" -gt "$REFRESH_MAX_AGE_HOURS" ]; then
  report refresh fail "The newest successful refresh is ${age_hours}h old (limit ${REFRESH_MAX_AGE_HOURS}h). Data is going stale. Check /home/leif/fpl-studio-refresh.log."
else
  report refresh ok "Refreshes are current again (newest ${age_hours}h old)."
fi

# ------------------------------------------------------------------- disk
usage="$(df --output=pcent / 2>/dev/null | tail -1 | tr -dc '0-9')"
if [ -n "$usage" ] && [ "$usage" -ge 90 ]; then
  report disk fail "Root filesystem is ${usage}% full. Postgres needs headroom."
else
  report disk ok "Disk usage back under 90% (${usage}%)."
fi

exit 0
