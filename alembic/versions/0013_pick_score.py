"""Store the Perfect Pick and its rank on each snapshot."""

from alembic import op
import sqlalchemy as sa

revision = "0013"
down_revision = "0012"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("player_snapshots", sa.Column("pick_score", sa.Float(), nullable=True))
    op.add_column("player_snapshots", sa.Column("pick_rank", sa.Integer(), nullable=True))
    op.add_column(
        "player_snapshots", sa.Column("pick_percentile", sa.Float(), nullable=True)
    )
    op.add_column(
        "player_snapshots", sa.Column("pick_tier", sa.String(length=30), nullable=True)
    )


def downgrade() -> None:
    op.drop_column("player_snapshots", "pick_tier")
    op.drop_column("player_snapshots", "pick_percentile")
    op.drop_column("player_snapshots", "pick_rank")
    op.drop_column("player_snapshots", "pick_score")
