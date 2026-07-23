# Contributing

Use Python 3.12+, install with `pip install -e ".[dev]"`, run migrations with
`alembic upgrade head`, and run tests with `pytest`. Keep analytics pure and
tested, use migrations for schema changes, preserve secrets in environment
variables, and do not commit databases or generated exports.
