"""Key gameweek history on the stable player code, not the current element id.

Historical rows were previously kept only for players present in the current
bootstrap, because ``player_id`` is a foreign key to the current-season element
id. Every player who has since left the Premier League was therefore discarded:
94% of 2016-17 rows and 44% of 2025-26 rows, a textbook survivorship bias that
would teach a model only what surviving careers look like.

``player_code`` is the stable FPL player identifier and exists for every player
in every season, whether or not they are still in the game. It becomes the key
for historical rows, and ``player_id`` becomes an optional convenience link to a
currently-active player.

The key also includes ``fixture_id``, because a double gameweek gives a player
two fixtures in one gameweek and a key without it keeps only one of them.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing = {column["name"] for column in inspector.get_columns("gameweek_history")}

    if "player_code" not in existing:
        op.add_column(
            "gameweek_history", sa.Column("player_code", sa.Integer(), nullable=True)
        )

    # Backfill from the current players table where the link is known.
    op.execute(
        """
        UPDATE gameweek_history
        SET player_code = (
            SELECT players.code FROM players WHERE players.id = gameweek_history.player_id
        )
        WHERE player_code IS NULL
        """
    )

    # Rows that still have no code cannot be attributed to a player across
    # seasons and are not safe to train on.
    op.execute("DELETE FROM gameweek_history WHERE player_code IS NULL")

    with op.batch_alter_table("gameweek_history") as batch:
        batch.alter_column("player_code", existing_type=sa.Integer(), nullable=False)
        batch.alter_column("player_id", existing_type=sa.Integer(), nullable=True)
        try:
            batch.drop_constraint("uq_player_season_gameweek", type_="unique")
        except Exception:  # pragma: no cover - constraint may already be absent
            pass
        # fixture_id is part of the key: in a double gameweek a player has two
        # fixtures in the same gameweek, and keying without it silently discards
        # one of them -- 1,548 such rows in 2022-23 alone.
        batch.create_unique_constraint(
            "uq_history_code_season_fixture",
            ["player_code", "season", "gameweek", "fixture_id"],
        )

    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("gameweek_history")
    }
    if "ix_gameweek_history_code_season" not in existing_indexes:
        op.create_index(
            "ix_gameweek_history_code_season",
            "gameweek_history",
            ["player_code", "season"],
        )


def downgrade() -> None:
    bind = op.get_bind()
    existing_indexes = {
        index["name"] for index in sa.inspect(bind).get_indexes("gameweek_history")
    }
    if "ix_gameweek_history_code_season" in existing_indexes:
        op.drop_index(
            "ix_gameweek_history_code_season", table_name="gameweek_history"
        )

    op.execute("DELETE FROM gameweek_history WHERE player_id IS NULL")

    with op.batch_alter_table("gameweek_history") as batch:
        try:
            batch.drop_constraint("uq_history_code_season_fixture", type_="unique")
        except Exception:  # pragma: no cover
            pass
        batch.create_unique_constraint(
            "uq_player_season_gameweek", ["player_id", "season", "gameweek"]
        )
        batch.alter_column("player_id", existing_type=sa.Integer(), nullable=False)
        batch.drop_column("player_code")
