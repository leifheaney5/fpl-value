"""Stable cross-season player identity.

FPL re-assigns element ids every season. Verified on 2026-08-03 against the
community archive: element id 1 is Shkodran Mustafi in 2019-20 and Fabio Vieira
in 2024-25. Joining historical rows on the element id would therefore attribute
one player's history to another.

`code` is the FPL player code and does not move between seasons. It is present
on every bootstrap element and in each archive season's players_raw.csv, so it
is the join key historical ingestion must use.
"""

from alembic import op
import sqlalchemy as sa

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("players")}
    if "code" not in existing:
        # Nullable: rows written before this migration have no code until the
        # next refresh populates them.
        op.add_column("players", sa.Column("code", sa.Integer(), nullable=True))
    constraints = {
        constraint["name"]
        for constraint in inspector.get_unique_constraints("players")
    }
    if "uq_player_code" not in constraints:
        with op.batch_alter_table("players") as batch:
            batch.create_unique_constraint("uq_player_code", ["code"])


def downgrade() -> None:
    with op.batch_alter_table("players") as batch:
        try:
            batch.drop_constraint("uq_player_code", type_="unique")
        except Exception:  # pragma: no cover - constraint may already be absent
            pass
        batch.drop_column("code")
