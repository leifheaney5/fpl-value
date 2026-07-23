# Architecture

FastAPI serves Jinja pages and exports. `app/api` owns FPL HTTP access,
`app/analytics` contains deterministic calculations, `app/services` owns
refresh, queries, exports, and legacy imports, and `app/db` owns SQLAlchemy
models and sessions. PostgreSQL is the production source of truth; SQLite is
used for local tests. Alembic is the production schema evolution mechanism.

The refresh pipeline is shared by the web manual-refresh action, CLI, and
Railway cron service. Each successful run stores immutable player snapshots.
