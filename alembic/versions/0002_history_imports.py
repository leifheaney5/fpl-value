"""Add gameweek history and legacy import audit tables."""

from alembic import op
import sqlalchemy as sa

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "gameweek_history",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("opponent", sa.String(100), nullable=False, server_default=""),
        sa.Column("is_home", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("minutes", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("started", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("points", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("goals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clean_sheets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("expected_goals", sa.Float(), nullable=False, server_default="0"),
        sa.Column("expected_assists", sa.Float(), nullable=False, server_default="0"),
        sa.Column("price", sa.Float(), nullable=False, server_default="0"),
        sa.Column("ownership", sa.Float(), nullable=False, server_default="0"),
        sa.Column("captured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("player_id", "gameweek", name="uq_player_gameweek"),
    )
    op.create_index("ix_gameweek_player_event", "gameweek_history", ["player_id", "gameweek"])
    op.create_table(
        "import_records",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("source_path", sa.String(500), nullable=False),
        sa.Column("imported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(20), nullable=False),
        sa.Column("row_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("skipped_count", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("details", sa.JSON(), nullable=False, server_default=sa.text("'{}'")),
        sa.UniqueConstraint("source_path", name="uq_import_source_path"),
    )


def downgrade() -> None:
    op.drop_table("import_records")
    op.drop_index("ix_gameweek_player_event", table_name="gameweek_history")
    op.drop_table("gameweek_history")
