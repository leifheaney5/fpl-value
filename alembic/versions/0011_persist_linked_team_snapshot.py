"""Persist the last valid linked FPL team payload."""

from alembic import op
import sqlalchemy as sa


revision = "0011"
down_revision = "0010"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "linked_team_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("entry_id", sa.Integer(), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("selected_event", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("last_error", sa.Text(), nullable=False, server_default=""),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("entry_id", name="uq_linked_team_snapshot_entry"),
    )
    op.create_index(
        "ix_linked_team_snapshots_entry_id",
        "linked_team_snapshots",
        ["entry_id"],
        unique=True,
    )


def downgrade() -> None:
    op.drop_index(
        "ix_linked_team_snapshots_entry_id",
        table_name="linked_team_snapshots",
    )
    op.drop_table("linked_team_snapshots")
