"""Stored predictions with the provenance needed to audit them.

A projected point total is only useful if you can later ask which model
produced it, on which features, trained to when. Every column below other than
the numbers themselves exists to answer that question.
"""

from alembic import op
import sqlalchemy as sa

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "predictions" in set(inspector.get_table_names()):
        return

    op.create_table(
        "predictions",
        sa.Column("id", sa.Integer(), primary_key=True),
        # Identity: the stable code, plus an optional link to a current player.
        sa.Column("player_code", sa.Integer(), nullable=False),
        sa.Column(
            "player_id", sa.Integer(), sa.ForeignKey("players.id"), nullable=True
        ),
        sa.Column("season", sa.String(9), nullable=False),
        sa.Column("gameweek", sa.Integer(), nullable=False),
        sa.Column("fixture_id", sa.Integer(), nullable=True),
        sa.Column("horizon", sa.String(20), nullable=False, server_default="next"),
        # The prediction and its uncertainty. Nullable throughout: a model that
        # cannot produce a distribution says so rather than inventing one.
        sa.Column("expected_points", sa.Float(), nullable=True),
        sa.Column("floor", sa.Float(), nullable=True),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("ceiling", sa.Float(), nullable=True),
        sa.Column("expected_minutes", sa.Float(), nullable=True),
        sa.Column("start_probability", sa.Float(), nullable=True),
        sa.Column("confidence", sa.String(20), nullable=True),
        # Provenance.
        sa.Column("model_name", sa.String(60), nullable=False),
        sa.Column("model_version", sa.String(40), nullable=False),
        sa.Column("feature_version", sa.String(40), nullable=False),
        sa.Column("information_state", sa.String(20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint(
            "player_code", "season", "gameweek", "horizon", "model_version",
            name="uq_prediction_player_gameweek_model",
        ),
    )
    op.create_index(
        "ix_predictions_season_gameweek", "predictions", ["season", "gameweek"]
    )
    op.create_index("ix_predictions_player_code", "predictions", ["player_code"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "predictions" not in set(inspector.get_table_names()):
        return
    op.drop_index("ix_predictions_player_code", table_name="predictions")
    op.drop_index("ix_predictions_season_gameweek", table_name="predictions")
    op.drop_table("predictions")
