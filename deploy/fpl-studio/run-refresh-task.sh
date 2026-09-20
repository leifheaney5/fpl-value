#!/usr/bin/env bash
# Hourly refresh for the self-hosted stack, invoked by host cron.
#
# This replaces the Railway cron service. Railway scheduled it at `0 14,15 UTC`
# — two entries, because a UTC schedule drifts an hour against the New York
# refresh hour across daylight saving. linux-leif's own clock is
# America/New_York, the same zone as APP_TIMEZONE, so one entry is both
# sufficient and correct:
#
#   5 * * * * /home/leif/fpl-value-studio/deploy/fpl-studio/run-refresh-task.sh >> /home/leif/fpl-studio-refresh.log 2>&1
#
# Five past the hour, not on it: FPL price changes and gameweek rollovers land
# on the hour, and a request at :00 can catch the API mid-update.
#
# The compose `refresh` service sets REFRESH_HOURLY=true, so every invocation
# refreshes. Storage does not grow with the cadence: each refresh collapses days
# older than SNAPSHOT_HOURLY_KEEP_HOURS to their final snapshot. Without
# REFRESH_HOURLY, `refresh --scheduled` runs only in REFRESH_HOUR and every
# other invocation is a no-op.
set -euo pipefail

cd "$(dirname "$0")"

DOCKER=/usr/bin/docker

echo "=== refresh started $(date --iso-8601=seconds) ==="
"$DOCKER" compose --profile tasks run --rm refresh
echo "=== refresh finished $(date --iso-8601=seconds) ==="
