"""Archive detail columns on gameweek_history.

The FPL community archive carries far more per-gameweek detail than the current
model stores, and it spans four schema eras. Columns a given season never
recorded stay null, so a null here means "this season did not record it" rather
than zero.
"""

from alembic import op
import sqlalchemy as sa

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None

INT_COLUMNS = [
    "starts",
    "saves",
    "bps",
    "yellow_cards",
    "red_cards",
    "own_goals",
    "penalties_missed",
    "penalties_saved",
    "goals_conceded",
    "fixture_id",
    "opponent_team_id",
    "transfers_in",
    "transfers_out",
    "transfers_balance",
    "selected",
]
FLOAT_COLUMNS = [
    "expected_goal_involvements",
    "expected_goals_conceded",
    "influence",
    "creativity",
    "threat",
]


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing = {column["name"] for column in inspector.get_columns("gameweek_history")}

    for name in INT_COLUMNS:
        if name not in existing:
            op.add_column(
                "gameweek_history", sa.Column(name, sa.Integer(), nullable=True)
            )
    for name in FLOAT_COLUMNS:
        if name not in existing:
            op.add_column(
                "gameweek_history", sa.Column(name, sa.Float(), nullable=True)
            )
    if "kickoff_time" not in existing:
        op.add_column(
            "gameweek_history",
            sa.Column("kickoff_time", sa.DateTime(timezone=True), nullable=True),
        )
    if "position" not in existing:
        op.add_column(
            "gameweek_history", sa.Column("position", sa.String(5), nullable=True)
        )
    if "team_name" not in existing:
        op.add_column(
            "gameweek_history", sa.Column("team_name", sa.String(60), nullable=True)
        )
    if "source" not in existing:
        op.add_column(
            "gameweek_history",
            sa.Column("source", sa.String(20), nullable=False, server_default="api"),
        )
    if "started_is_derived" not in existing:
        # NOT NULL deliberately: whether a start was observed or inferred from a
        # minutes threshold is never unknown, and the evaluation harness must be
        # able to segment on it.
        op.add_column(
            "gameweek_history",
            sa.Column(
                "started_is_derived",
                sa.Boolean(),
                nullable=False,
                server_default=sa.false(),
            ),
        )

    existing_indexes = {
        index["name"] for index in inspector.get_indexes("gameweek_history")
    }
    if "ix_gameweek_history_season_gw" not in existing_indexes:
        op.create_index(
            "ix_gameweek_history_season_gw",
            "gameweek_history",
            ["season", "gameweek"],
        )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {
        index["name"] for index in inspector.get_indexes("gameweek_history")
    }
    if "ix_gameweek_history_season_gw" in existing_indexes:
        op.drop_index(
            "ix_gameweek_history_season_gw", table_name="gameweek_history"
        )

    with op.batch_alter_table("gameweek_history") as batch:
        for name in (
            INT_COLUMNS
            + FLOAT_COLUMNS
            + ["kickoff_time", "position", "team_name", "source", "started_is_derived"]
        ):
            batch.drop_column(name)
