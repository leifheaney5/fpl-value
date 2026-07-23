#!/usr/bin/env bash
set -euo pipefail

if [[ "${RUN_REFRESH_ONLY:-false}" == "true" ]]; then
  exec bash scripts/run-refresh.sh --scheduled
fi

alembic upgrade head
exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --proxy-headers \
  --forwarded-allow-ips="*"
