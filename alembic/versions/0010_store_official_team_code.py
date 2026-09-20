"""Store the official FPL badge code separately from the team row ID."""

from alembic import op
import sqlalchemy as sa


revision = "0010"
down_revision = "0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("teams", sa.Column("code", sa.Integer(), nullable=True))
    op.create_index("ix_teams_code", "teams", ["code"], unique=False)


def downgrade() -> None:
    op.drop_index("ix_teams_code", table_name="teams")
    op.drop_column("teams", "code")
