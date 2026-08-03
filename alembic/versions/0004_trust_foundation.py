"""Season identity, metric status, gameweeks, audit events, nullable metrics.

Derived metric columns were previously NOT NULL with a zero default, which made
"the team has played no matches yet" indistinguishable from "the player scored
zero". Relaxing them to nullable lets the refresh pipeline record the absence of
a measurement instead of inventing one.
"""

from alembic import op
import sqlalchemy as sa

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

NULLABLE_METRICS = [
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
]
DEFAULT_SEASON = "2026/27"


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    tables = set(inspector.get_table_names())

    if "gameweeks" not in tables:
        op.create_table(
            "gameweeks",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("season", sa.String(9), nullable=False),
            sa.Column("number", sa.Integer(), nullable=False),
            sa.Column("name", sa.String(60), nullable=False, server_default=""),
            sa.Column("deadline_time", sa.DateTime(timezone=True), nullable=True),
            sa.Column(
                "finished", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "data_checked", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "is_current", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column(
                "is_next", sa.Boolean(), nullable=False, server_default=sa.false()
            ),
            sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
            sa.UniqueConstraint("season", "number", name="uq_gameweek_season_number"),
        )

    if "audit_events" not in tables:
        op.create_table(
            "audit_events",
            sa.Column("id", sa.Integer(), primary_key=True),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("action", sa.String(50), nullable=False),
            sa.Column(
                "actor", sa.String(100), nullable=False, server_default="anonymous"
            ),
            sa.Column("path", sa.String(300), nullable=False, server_default=""),
            sa.Column("client", sa.String(60), nullable=False, server_default=""),
            sa.Column("detail", sa.JSON(), nullable=False, server_default="{}"),
        )
        op.create_index("ix_audit_events_occurred_at", "audit_events", ["occurred_at"])
        op.create_index("ix_audit_events_action", "audit_events", ["action"])

    snapshot_columns = {
        column["name"] for column in inspector.get_columns("player_snapshots")
    }
    if "season" not in snapshot_columns:
        op.add_column(
            "player_snapshots",
            sa.Column(
                "season", sa.String(9), nullable=False, server_default=DEFAULT_SEASON
            ),
        )
    if "metric_status" not in snapshot_columns:
        op.add_column(
            "player_snapshots",
            sa.Column("metric_status", sa.JSON(), nullable=False, server_default="{}"),
        )

    with op.batch_alter_table("player_snapshots") as batch:
        for name in NULLABLE_METRICS:
            if name in snapshot_columns:
                batch.alter_column(
                    name,
                    existing_type=sa.Float(),
                    nullable=True,
                    existing_server_default=None,
                    server_default=None,
                )

    # Created after the batch rebuild: on SQLite a batch operation recreates the
    # table and replays its reflected indexes, so an index added beforehand would
    # be rebuilt against an intermediate table definition.
    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("player_snapshots")
    }
    if "ix_snapshot_season_time" not in existing_indexes:
        op.create_index(
            "ix_snapshot_season_time", "player_snapshots", ["season", "captured_at"]
        )

    history_columns = {
        column["name"] for column in inspector.get_columns("gameweek_history")
    }
    if "season" not in history_columns:
        op.add_column(
            "gameweek_history",
            sa.Column(
                "season", sa.String(9), nullable=False, server_default=DEFAULT_SEASON
            ),
        )

    with op.batch_alter_table("gameweek_history") as batch:
        try:
            batch.drop_constraint("uq_player_gameweek", type_="unique")
        except Exception:  # pragma: no cover - constraint may already be absent
            pass
        batch.create_unique_constraint(
            "uq_player_season_gameweek", ["player_id", "season", "gameweek"]
        )


def downgrade() -> None:
    # Drop the season index first: the batch rebuild below replays reflected
    # indexes, and this one references a column the rebuild removes.
    existing_indexes = {
        index["name"]
        for index in sa.inspect(op.get_bind()).get_indexes("player_snapshots")
    }
    if "ix_snapshot_season_time" in existing_indexes:
        op.drop_index("ix_snapshot_season_time", table_name="player_snapshots")

    with op.batch_alter_table("gameweek_history") as batch:
        try:
            batch.drop_constraint("uq_player_season_gameweek", type_="unique")
        except Exception:  # pragma: no cover
            pass
        batch.create_unique_constraint(
            "uq_player_gameweek", ["player_id", "gameweek"]
        )
        batch.drop_column("season")

    with op.batch_alter_table("player_snapshots") as batch:
        for name in NULLABLE_METRICS:
            batch.alter_column(
                name,
                existing_type=sa.Float(),
                nullable=False,
                server_default="0",
            )
        batch.drop_column("metric_status")
        batch.drop_column("season")

    inspector = sa.inspect(op.get_bind())
    tables = set(inspector.get_table_names())
    if "audit_events" in tables:
        op.drop_table("audit_events")
    if "gameweeks" in tables:
        op.drop_table("gameweeks")
