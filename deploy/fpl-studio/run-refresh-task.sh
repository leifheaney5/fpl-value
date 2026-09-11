#!/usr/bin/env bash
# Daily refresh for the self-hosted stack, invoked by host cron.
#
# This replaces the Railway cron service. Railway scheduled it at `0 14,15 UTC`
# — two entries, because a UTC schedule drifts an hour against the New York
# refresh hour across daylight saving. linux-leif's own clock is
# America/New_York, the same zone as APP_TIMEZONE, so one entry is both
# sufficient and correct:
#
#   0 10 * * * /home/leif/fpl-value-studio/deploy/fpl-studio/run-refresh-task.sh >> /home/leif/fpl-studio-refresh.log 2>&1
#
# `refresh --scheduled` still checks APP_TIMEZONE and REFRESH_HOUR itself, so a
# stray second invocation on the same day is a no-op rather than a duplicate.
set -euo pipefail

cd "$(dirname "$0")"

DOCKER=/usr/bin/docker

echo "=== refresh started $(date --iso-8601=seconds) ==="
"$DOCKER" compose --profile tasks run --rm refresh
echo "=== refresh finished $(date --iso-8601=seconds) ==="
