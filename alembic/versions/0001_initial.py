"""Initial schema

Revision ID: 0001
Revises:
"""
from alembic import op

from app.db.base import Base
from app.db import models  # noqa: F401


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    original_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name not in {"gameweek_history", "import_records"}
    ]
    Base.metadata.create_all(bind=op.get_bind(), tables=original_tables)


def downgrade() -> None:
    original_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name not in {"gameweek_history", "import_records"}
    ]
    Base.metadata.drop_all(bind=op.get_bind(), tables=original_tables)
