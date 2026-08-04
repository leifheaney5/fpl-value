"""Position-relative ranks for points-per-million.

Raw points-per-million systematically favours cheap defenders and goalkeepers:
their price floor is lower and clean-sheet points are steady, so a global
ranking on it puts six defenders and two keepers in the top eight and says
nothing about which forward is worth owning. `reliable_value` and
`forward_value` already carry position-relative ranks; `value` did not, which
made the one metric most people sort by the one metric least comparable across
positions.
"""

from alembic import op
import sqlalchemy as sa

revision = "0009"
down_revision = "0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "player_snapshots",
        sa.Column("position_value_rank", sa.Integer(), nullable=True),
    )
    op.add_column(
        "player_snapshots",
        sa.Column("position_value_percentile", sa.Float(), nullable=True),
    )
    op.add_column(
        "player_snapshots",
        sa.Column("position_value_tier", sa.String(length=30), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("player_snapshots", "position_value_tier")
    op.drop_column("player_snapshots", "position_value_percentile")
    op.drop_column("player_snapshots", "position_value_rank")
