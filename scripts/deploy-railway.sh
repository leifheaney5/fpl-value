#!/usr/bin/env bash
set -euo pipefail

if ! command -v railway >/dev/null 2>&1; then
  echo "Railway CLI is required. Install it from https://docs.railway.com/guides/cli and run railway login." >&2
  exit 2
fi
if ! railway whoami >/dev/null 2>&1; then
  echo "Railway CLI is not authenticated. Run railway login, then rerun this script." >&2
  exit 2
fi

echo "Railway CLI is authenticated. Create/link the project and PostgreSQL service in the Railway UI or with your chosen project name."
echo "Deploy the web service with railway.json and a second service with railway.cron.json."
echo "This script intentionally does not invent APP_PASSWORD or mutate an unspecified Railway project."
