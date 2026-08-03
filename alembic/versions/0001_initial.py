"""Initial schema

Revision ID: 0001
Revises:

This revision is frozen: it creates the schema exactly as it stood when the
revision was authored, using explicit column definitions.

It previously called ``Base.metadata.create_all`` against the live models, which
meant a fresh database jumped straight to whatever the models looked like today
rather than to the state this revision describes. Later revisions then found
their columns already present and silently did nothing, and downgrades removed
columns this revision had created. Deployed databases are unaffected by this
change because Alembic records their revision in ``alembic_version`` and will
not re-run 0001.
"""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def _metric(name: str) -> sa.Column:
    """A derived metric as it stood at this revision: NOT NULL, zero default.

    Revision 0004 relaxes these to nullable so that an absent measurement can be
    distinguished from a measured zero.
    """
    return sa.Column(name, sa.Float(), nullable=False, server_default="0")


def upgrade() -> None:
    op.create_table(
        "teams",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("short_name", sa.String(10), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )

    op.create_table(
        "players",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("first_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("second_name", sa.String(100), nullable=False, server_default=""),
        sa.Column("web_name", sa.String(100), nullable=False),
        sa.Column("team_id", sa.Integer(), sa.ForeignKey("teams.id"), nullable=False),
        sa.Column("position", sa.String(20), nullable=False),
        sa.Column("position_short", sa.String(5), nullable=False),
        sa.Column("status", sa.String(5), nullable=False, server_default="a"),
        sa.Column("news", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_players_team_id", "players", ["team_id"])

    op.create_table(
        "fixtures",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("event", sa.Integer(), nullable=True),
        sa.Column("kickoff_time", sa.DateTime(timezone=True), nullable=True),
        sa.Column("team_h", sa.Integer(), nullable=False),
        sa.Column("team_a", sa.Integer(), nullable=False),
        sa.Column("team_h_difficulty", sa.Integer(), nullable=False),
        sa.Column("team_a_difficulty", sa.Integer(), nullable=False),
        sa.Column("finished", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_fixtures_event", "fixtures", ["event"])
    op.create_index("ix_fixtures_team_h", "fixtures", ["team_h"])
    op.create_index("ix_fixtures_team_a", "fixtures", ["team_a"])
    op.create_index("ix_fixtures_finished", "fixtures", ["finished"])

    op.create_table(
        "refresh_runs",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("player_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column(
            "schema_change_count", sa.Integer(), nullable=False, server_default="0"
        ),
        sa.Column("error", sa.Text(), nullable=False, server_default=""),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
    )
    op.create_index("ix_refresh_runs_started_at", "refresh_runs", ["started_at"])
    op.create_index("ix_refresh_runs_status", "refresh_runs", ["status"])

    op.create_table(
        "schema_fields",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("field_name", sa.String(150), nullable=False),
        sa.Column("first_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("last_seen", sa.DateTime(timezone=True), nullable=False),
        sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.UniqueConstraint("category", "field_name", name="uq_schema_field"),
    )
    op.create_index("ix_schema_fields_category", "schema_fields", ["category"])
    op.create_index("ix_schema_fields_active", "schema_fields", ["active"])

    op.create_table(
        "schema_changes",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("detected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("category", sa.String(50), nullable=False),
        sa.Column("change_type", sa.String(20), nullable=False),
        sa.Column("field_name", sa.String(150), nullable=False),
    )
    op.create_index("ix_schema_changes_detected_at", "schema_changes", ["detected_at"])

    op.create_table(
        "player_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False
        ),
        sa.Column(
            "refresh_run_id",
            sa.Integer(),
            sa.ForeignKey("refresh_runs.id"),
            nullable=False,
        ),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("price", sa.Float(), nullable=False),
        sa.Column("total_points", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Integer(), nullable=False),
        sa.Column("team_matches", sa.Integer(), nullable=False),
        sa.Column("goals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clean_sheets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bps", sa.Integer(), nullable=False, server_default="0"),
        _metric("form"),
        _metric("points_per_game"),
        _metric("points_per_minute"),
        _metric("points_per_90"),
        _metric("points_per_start"),
        _metric("points_per_team_match"),
        _metric("value_per_90"),
        _metric("start_rate"),
        _metric("minutes_per_team_match"),
        _metric("expected_goals"),
        _metric("expected_assists"),
        _metric("expected_goal_involvements"),
        _metric("ict_index"),
        _metric("ownership"),
        _metric("value"),
        sa.Column("value_rank", sa.Integer(), nullable=True),
        sa.Column("value_percentile", sa.Float(), nullable=True),
        sa.Column(
            "value_tier", sa.String(30), nullable=False, server_default="Not Ranked"
        ),
        _metric("reliability_factor"),
        _metric("reliable_value"),
        sa.Column("reliable_rank", sa.Integer(), nullable=True),
        sa.Column("reliable_percentile", sa.Float(), nullable=True),
        sa.Column(
            "reliable_tier", sa.String(30), nullable=False, server_default="Not Ranked"
        ),
        sa.Column("position_reliable_rank", sa.Integer(), nullable=True),
        sa.Column("position_reliable_percentile", sa.Float(), nullable=True),
        sa.Column(
            "position_reliable_tier",
            sa.String(30),
            nullable=False,
            server_default="Not Ranked",
        ),
        sa.Column("rotation_risk", sa.Float(), nullable=True),
        sa.Column(
            "rotation_tier",
            sa.String(30),
            nullable=False,
            server_default="Insufficient Data",
        ),
        sa.Column(
            "rotation_confidence", sa.String(20), nullable=False, server_default="Low"
        ),
        sa.Column(
            "availability_factor", sa.Float(), nullable=False, server_default="1"
        ),
        _metric("expected_minutes"),
        _metric("projected_points_5"),
        _metric("forward_value"),
        sa.Column("forward_rank", sa.Integer(), nullable=True),
        sa.Column("forward_percentile", sa.Float(), nullable=True),
        sa.Column(
            "forward_tier", sa.String(30), nullable=False, server_default="Not Ranked"
        ),
        sa.Column("position_forward_rank", sa.Integer(), nullable=True),
        sa.Column("position_forward_percentile", sa.Float(), nullable=True),
        sa.Column(
            "position_forward_tier",
            sa.String(30),
            nullable=False,
            server_default="Not Ranked",
        ),
        sa.Column("upcoming_fixtures", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("raw", sa.JSON(), nullable=False, server_default="{}"),
        sa.UniqueConstraint(
            "player_id", "captured_at", name="uq_player_snapshot_time"
        ),
    )
    op.create_index("ix_player_snapshots_player_id", "player_snapshots", ["player_id"])
    op.create_index(
        "ix_player_snapshots_refresh_run_id", "player_snapshots", ["refresh_run_id"]
    )
    op.create_index(
        "ix_player_snapshots_captured_at", "player_snapshots", ["captured_at"]
    )
    op.create_index(
        "ix_snapshot_player_time", "player_snapshots", ["player_id", "captured_at"]
    )
    op.create_index(
        "ix_snapshot_value_rank", "player_snapshots", ["captured_at", "value_rank"]
    )


def downgrade() -> None:
    op.drop_table("player_snapshots")
    op.drop_table("schema_changes")
    op.drop_table("schema_fields")
    op.drop_table("refresh_runs")
    op.drop_table("fixtures")
    op.drop_table("players")
    op.drop_table("teams")
