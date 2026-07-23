#!/usr/bin/env bash
set -euo pipefail

alembic upgrade head
exec python -m app.cli refresh "$@"
