import sqlalchemy as sa
from alembic import command
from alembic.config import Config


NULLABLE_METRICS = {
    "value",
    "reliability_factor",
    "reliable_value",
    "start_rate",
    "points_per_90",
    "points_per_start",
    "points_per_team_match",
    "points_per_minute",
    "minutes_per_team_match",
    "average_minutes_per_start",
    "value_per_90",
    "expected_minutes",
    "projected_points_5",
    "forward_value",
    "average_fixture_difficulty",
}


def _config(url):
    config = Config("alembic.ini")
    config.attributes["sqlalchemy.url"] = url
    return config


def test_migration_adds_season_metric_status_and_new_tables(tmp_path):
    url = f"sqlite:///{tmp_path / 'migrate.db'}"
    command.upgrade(_config(url), "head")

    inspector = sa.inspect(sa.create_engine(url))
    tables = set(inspector.get_table_names())
    assert "gameweeks" in tables
    assert "audit_events" in tables

    columns = {c["name"]: c for c in inspector.get_columns("player_snapshots")}
    assert "season" in columns
    assert "metric_status" in columns

    history = {c["name"] for c in inspector.get_columns("gameweek_history")}
    assert "season" in history


def test_migration_relaxes_derived_metric_columns_to_nullable(tmp_path):
    url = f"sqlite:///{tmp_path / 'nullable.db'}"
    command.upgrade(_config(url), "head")

    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"]: c for c in inspector.get_columns("player_snapshots")}
    for name in NULLABLE_METRICS:
        assert columns[name]["nullable"] is True, f"{name} must be nullable"


def test_gameweek_history_is_unique_per_season(tmp_path):
    url = f"sqlite:///{tmp_path / 'unique.db'}"
    command.upgrade(_config(url), "head")

    inspector = sa.inspect(sa.create_engine(url))
    constraints = inspector.get_unique_constraints("gameweek_history")
    columns = {tuple(c["column_names"]) for c in constraints}
    assert ("player_id", "season", "gameweek") in columns
    assert ("player_id", "gameweek") not in columns


ARCHIVE_COLUMNS = {
    "starts", "started_is_derived", "source", "fixture_id", "kickoff_time",
    "opponent_team_id", "saves", "bps", "yellow_cards", "red_cards", "own_goals",
    "penalties_missed", "penalties_saved", "goals_conceded",
    "expected_goal_involvements", "expected_goals_conceded", "influence",
    "creativity", "threat", "transfers_in", "transfers_out", "transfers_balance",
    "selected", "position", "team_name",
}


def test_gameweek_history_carries_archive_detail(tmp_path):
    url = f"sqlite:///{tmp_path / 'archive.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"] for c in inspector.get_columns("gameweek_history")}
    missing = ARCHIVE_COLUMNS - columns
    assert not missing, f"missing archive columns: {sorted(missing)}"


def test_derived_start_flag_is_not_nullable(tmp_path):
    url = f"sqlite:///{tmp_path / 'derived.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"]: c for c in inspector.get_columns("gameweek_history")}
    assert columns["started_is_derived"]["nullable"] is False
    # starts itself IS nullable: six of ten archive seasons never recorded it.
    assert columns["starts"]["nullable"] is True


def test_players_carry_a_stable_cross_season_code(tmp_path):
    """FPL reuses element ids between seasons; `code` is what does not move.

    Verified 2026-08-03: element id 1 is Shkodran Mustafi in 2019-20 and
    Fabio Vieira in 2024-25. Archive rows must resolve through `code`.
    """
    url = f"sqlite:///{tmp_path / 'code.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))
    columns = {c["name"]: c for c in inspector.get_columns("players")}
    assert "code" in columns
    assert columns["code"]["nullable"] is True
    constraints = {
        tuple(c["column_names"])
        for c in inspector.get_unique_constraints("players")
    }
    assert ("code",) in constraints


def test_migrated_schema_matches_the_models(tmp_path):
    """Migrations and models must agree.

    Revision 0001 used to call Base.metadata.create_all, so a migrated database
    matched the models by construction and later revisions were untested no-ops.
    Now that 0001 is frozen, this comparison is the thing that catches drift.
    """
    from app.db.base import Base
    from app.db import models  # noqa: F401

    url = f"sqlite:///{tmp_path / 'drift.db'}"
    command.upgrade(_config(url), "head")
    inspector = sa.inspect(sa.create_engine(url))

    migrated_tables = set(inspector.get_table_names()) - {"alembic_version"}
    assert migrated_tables == set(Base.metadata.tables)

    for name, table in Base.metadata.tables.items():
        migrated = {c["name"] for c in inspector.get_columns(name)}
        declared = {column.name for column in table.columns}
        assert migrated == declared, f"{name} columns drifted: {migrated ^ declared}"

    snapshot = {c["name"]: c for c in inspector.get_columns("player_snapshots")}
    for column in Base.metadata.tables["player_snapshots"].columns:
        assert snapshot[column.name]["nullable"] == column.nullable, (
            f"player_snapshots.{column.name} nullability drifted"
        )


def test_migration_downgrades_and_upgrades_again(tmp_path):
    url = f"sqlite:///{tmp_path / 'roundtrip.db'}"
    config = _config(url)
    command.upgrade(config, "head")
    command.downgrade(config, "0003")

    inspector = sa.inspect(sa.create_engine(url))
    assert "gameweeks" not in set(inspector.get_table_names())
    assert "season" not in {c["name"] for c in inspector.get_columns("player_snapshots")}

    command.upgrade(config, "head")
    inspector = sa.inspect(sa.create_engine(url))
    assert "gameweeks" in set(inspector.get_table_names())
