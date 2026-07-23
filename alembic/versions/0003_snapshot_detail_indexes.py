"""Store rotation and projection detail and add ranking indexes."""

from alembic import op
import sqlalchemy as sa

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_columns = {column["name"] for column in inspector.get_columns("player_snapshots")}
    columns = [
        ("average_minutes_per_start", sa.Float(), "0"),
        ("recent_team_matches", sa.Integer(), "0"),
        ("recent_starts", sa.Integer(), "0"),
        ("recent_minutes", sa.Integer(), "0"),
        ("historical_reference_at", sa.DateTime(timezone=True), None),
        ("availability_status", sa.String(5), "'a'"),
        ("chance_of_playing", sa.Float(), None),
        ("upcoming_fixture_count", sa.Integer(), "0"),
        ("average_fixture_difficulty", sa.Float(), "0"),
    ]
    for name, type_, default in columns:
        if name not in existing_columns:
            kwargs = {"nullable": True} if default is None else {"nullable": False, "server_default": default}
            op.add_column("player_snapshots", sa.Column(name, type_, **kwargs))
    existing_indexes = {index["name"] for index in inspector.get_indexes("player_snapshots")}
    if "ix_snapshot_reliable_rank" not in existing_indexes:
        op.create_index("ix_snapshot_reliable_rank", "player_snapshots", ["captured_at", "reliable_rank"])
    if "ix_snapshot_forward_rank" not in existing_indexes:
        op.create_index("ix_snapshot_forward_rank", "player_snapshots", ["captured_at", "forward_rank"])


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    existing_indexes = {index["name"] for index in inspector.get_indexes("player_snapshots")}
    if "ix_snapshot_forward_rank" in existing_indexes:
        op.drop_index("ix_snapshot_forward_rank", table_name="player_snapshots")
    if "ix_snapshot_reliable_rank" in existing_indexes:
        op.drop_index("ix_snapshot_reliable_rank", table_name="player_snapshots")
    existing_columns = {column["name"] for column in inspector.get_columns("player_snapshots")}
    for name in [
        "average_fixture_difficulty", "upcoming_fixture_count", "chance_of_playing",
        "availability_status", "historical_reference_at", "recent_minutes",
        "recent_starts", "recent_team_matches", "average_minutes_per_start",
    ]:
        if name in existing_columns:
            op.drop_column("player_snapshots", name)
