"""Store per-season player aggregates derived from gameweek history."""

from alembic import op
import sqlalchemy as sa


revision = "0012"
down_revision = "0011"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "player_season_aggregates",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("player_code", sa.Integer(), nullable=False),
        sa.Column("season", sa.String(9), nullable=False),
        sa.Column("fixtures", sa.Integer(), nullable=False),
        sa.Column("appearances", sa.Integer(), nullable=False),
        sa.Column("minutes", sa.Integer(), nullable=False),
        sa.Column("starts", sa.Integer(), nullable=False),
        sa.Column(
            "starts_derived", sa.Boolean(), nullable=False, server_default=sa.false()
        ),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("goals", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("assists", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("clean_sheets", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("bonus", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("appearance_points", sa.Integer(), nullable=False),
        sa.Column("appearance_points_sq", sa.Integer(), nullable=False),
        sa.Column("blanks", sa.Integer(), nullable=False),
        sa.Column("hauls", sa.Integer(), nullable=False),
        sa.Column("price_min", sa.Float(), nullable=True),
        sa.Column("price_max", sa.Float(), nullable=True),
        sa.Column("position", sa.String(5), nullable=True),
        sa.Column("team_name", sa.String(60), nullable=True),
        sa.Column("computed_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("player_code", "season", name="uq_season_aggregate"),
    )
    op.create_index(
        "ix_season_aggregate_season", "player_season_aggregates", ["season"]
    )


def downgrade() -> None:
    op.drop_index(
        "ix_season_aggregate_season", table_name="player_season_aggregates"
    )
    op.drop_table("player_season_aggregates")
