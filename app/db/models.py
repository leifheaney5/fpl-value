from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db.base import Base


class Team(Base):
    __tablename__ = "teams"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    short_name: Mapped[str] = mapped_column(String(10), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )

    players: Mapped[list["Player"]] = relationship(back_populates="team")


class Player(Base):
    __tablename__ = "players"
    __table_args__ = (UniqueConstraint("code", name="uq_player_code"),)

    # `id` is the FPL element id, which is re-assigned every season. `code` is
    # the stable player identifier and is the only safe key for joining data
    # across seasons.
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    code: Mapped[int | None] = mapped_column(Integer, nullable=True)
    first_name: Mapped[str] = mapped_column(String(100), default="")
    second_name: Mapped[str] = mapped_column(String(100), default="")
    web_name: Mapped[str] = mapped_column(String(100), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), index=True)
    position: Mapped[str] = mapped_column(String(20), nullable=False)
    position_short: Mapped[str] = mapped_column(String(5), nullable=False)
    status: Mapped[str] = mapped_column(String(5), default="a")
    news: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    team: Mapped[Team] = relationship(back_populates="players")
    snapshots: Mapped[list["PlayerSnapshot"]] = relationship(
        back_populates="player",
        cascade="all, delete-orphan",
    )

    @property
    def full_name(self) -> str:
        return " ".join(
            part for part in (self.first_name, self.second_name) if part
        ) or self.web_name


class Fixture(Base):
    __tablename__ = "fixtures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    kickoff_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    team_h: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    team_a: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    team_h_difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    team_a_difficulty: Mapped[int] = mapped_column(Integer, nullable=False)
    finished: Mapped[bool] = mapped_column(Boolean, default=False, index=True)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class RefreshRun(Base):
    __tablename__ = "refresh_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, index=True)
    player_count: Mapped[int] = mapped_column(Integer, default=0)
    schema_change_count: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str] = mapped_column(Text, default="")
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class SchemaField(Base):
    __tablename__ = "schema_fields"
    __table_args__ = (
        UniqueConstraint("category", "field_name", name="uq_schema_field"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    field_name: Mapped[str] = mapped_column(String(150), nullable=False)
    first_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    last_seen: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class SchemaChange(Base):
    __tablename__ = "schema_changes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    category: Mapped[str] = mapped_column(String(50), nullable=False)
    change_type: Mapped[str] = mapped_column(String(20), nullable=False)
    field_name: Mapped[str] = mapped_column(String(150), nullable=False)


class PlayerSnapshot(Base):
    __tablename__ = "player_snapshots"
    __table_args__ = (
        UniqueConstraint(
            "player_id",
            "captured_at",
            name="uq_player_snapshot_time",
        ),
        Index("ix_snapshot_player_time", "player_id", "captured_at"),
        Index("ix_snapshot_season_time", "season", "captured_at"),
        Index("ix_snapshot_value_rank", "captured_at", "value_rank"),
        Index("ix_snapshot_reliable_rank", "captured_at", "reliable_rank"),
        Index("ix_snapshot_forward_rank", "captured_at", "forward_rank"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id"), nullable=False, index=True
    )
    refresh_run_id: Mapped[int] = mapped_column(
        ForeignKey("refresh_runs.id"), nullable=False, index=True
    )
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    season: Mapped[str] = mapped_column(
        String(9), nullable=False, default="2026/27", index=True
    )
    # Per-metric provenance: {"expected_minutes": {"status": ..., "reason": ...}}.
    # A null metric column says "no value"; this says why.
    metric_status: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    # Observations reported directly by the FPL API stay non-nullable.
    price: Mapped[float] = mapped_column(Float, nullable=False)
    total_points: Mapped[int] = mapped_column(Integer, nullable=False)
    minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    starts: Mapped[int] = mapped_column(Integer, nullable=False)
    team_matches: Mapped[int] = mapped_column(Integer, nullable=False)

    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int] = mapped_column(Integer, default=0)

    form: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_game: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_minute: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    points_per_90: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    points_per_start: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    points_per_team_match: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    value_per_90: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    start_rate: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    minutes_per_team_match: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    average_minutes_per_start: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )

    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float] = mapped_column(
        Float, default=0.0
    )
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)
    ownership: Mapped[float] = mapped_column(Float, default=0.0)

    value: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    value_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    value_tier: Mapped[str] = mapped_column(String(30), default="Not Ranked")

    reliability_factor: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    reliable_value: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    reliable_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    reliable_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    reliable_tier: Mapped[str] = mapped_column(
        String(30), default="Not Ranked"
    )
    position_reliable_rank: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    position_reliable_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    position_reliable_tier: Mapped[str] = mapped_column(
        String(30), default="Not Ranked"
    )

    rotation_risk: Mapped[float | None] = mapped_column(Float, nullable=True)
    rotation_tier: Mapped[str] = mapped_column(
        String(30), default="Insufficient Data"
    )
    rotation_confidence: Mapped[str] = mapped_column(
        String(20), default="Low"
    )
    recent_team_matches: Mapped[int] = mapped_column(Integer, default=0)
    recent_starts: Mapped[int] = mapped_column(Integer, default=0)
    recent_minutes: Mapped[int] = mapped_column(Integer, default=0)
    historical_reference_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    availability_factor: Mapped[float] = mapped_column(Float, default=1.0)
    availability_status: Mapped[str] = mapped_column(String(5), default="a")
    chance_of_playing: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_minutes: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    projected_points_5: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    upcoming_fixture_count: Mapped[int] = mapped_column(Integer, default=0)
    average_fixture_difficulty: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    forward_value: Mapped[float | None] = mapped_column(
        Float, nullable=True, default=None
    )
    forward_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    forward_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    forward_tier: Mapped[str] = mapped_column(
        String(30), default="Not Ranked"
    )
    position_forward_rank: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    position_forward_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    position_forward_tier: Mapped[str] = mapped_column(
        String(30), default="Not Ranked"
    )
    # Points-per-million compared within a position. Globally it favours cheap
    # defenders and keepers, so the raw ranking answers "who is cheap and
    # steady" rather than "who is good value for what they are".
    position_value_rank: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    position_value_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    position_value_tier: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    upcoming_fixtures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    player: Mapped[Player] = relationship(back_populates="snapshots")
    refresh_run: Mapped[RefreshRun] = relationship()


class Gameweek(Base):
    """Season calendar, persisted from the bootstrap ``events`` payload.

    Previously these records were parsed only to detect schema changes and then
    discarded, which left the application with no way to know the current
    gameweek or the next deadline.
    """

    __tablename__ = "gameweeks"
    __table_args__ = (
        UniqueConstraint("season", "number", name="uq_gameweek_season_number"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    season: Mapped[str] = mapped_column(String(9), nullable=False)
    number: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(60), default="")
    deadline_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished: Mapped[bool] = mapped_column(Boolean, default=False)
    data_checked: Mapped[bool] = mapped_column(Boolean, default=False)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    is_next: Mapped[bool] = mapped_column(Boolean, default=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class GameweekHistory(Base):
    __tablename__ = "gameweek_history"
    __table_args__ = (
        # fixture_id is part of the key: a double gameweek gives a player two
        # fixtures in the same gameweek.
        UniqueConstraint(
            "player_code", "season", "gameweek", "fixture_id",
            name="uq_history_code_season_fixture",
        ),
        Index("ix_gameweek_player_event", "player_id", "gameweek"),
        Index("ix_gameweek_history_season_gw", "season", "gameweek"),
        Index("ix_gameweek_history_code_season", "player_code", "season"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    # The stable FPL player code. Present for every player in every season,
    # including those who have since left the league -- excluding them would
    # train a model only on careers that survived.
    player_code: Mapped[int] = mapped_column(Integer, nullable=False)
    # Optional link to a currently-active player. Null for departed players.
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), nullable=True
    )
    season: Mapped[str] = mapped_column(String(9), nullable=False, default="2026/27")
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False)
    # "api" for rows collected from the live FPL endpoints, "archive" for rows
    # imported from the community historical dataset.
    source: Mapped[str] = mapped_column(String(20), nullable=False, default="api")
    opponent: Mapped[str] = mapped_column(String(100), default="")
    opponent_team_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_home: Mapped[bool] = mapped_column(Boolean, default=False)
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    kickoff_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    position: Mapped[str | None] = mapped_column(String(5), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(60), nullable=True)

    minutes: Mapped[int] = mapped_column(Integer, default=0)
    started: Mapped[bool] = mapped_column(Boolean, default=False)
    # Null for the six archive seasons that never recorded starts. When that is
    # the case `started` is inferred from a minutes threshold and the inference
    # is flagged below, so evaluation can measure whether those seasons help.
    starts: Mapped[int | None] = mapped_column(Integer, nullable=True)
    started_is_derived: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )

    points: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    goals_conceded: Mapped[int | None] = mapped_column(Integer, nullable=True)
    saves: Mapped[int | None] = mapped_column(Integer, nullable=True)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    bps: Mapped[int | None] = mapped_column(Integer, nullable=True)
    yellow_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    red_cards: Mapped[int | None] = mapped_column(Integer, nullable=True)
    own_goals: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_missed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    penalties_saved: Mapped[int | None] = mapped_column(Integer, nullable=True)

    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    expected_goals_conceded: Mapped[float | None] = mapped_column(Float, nullable=True)
    influence: Mapped[float | None] = mapped_column(Float, nullable=True)
    creativity: Mapped[float | None] = mapped_column(Float, nullable=True)
    threat: Mapped[float | None] = mapped_column(Float, nullable=True)

    price: Mapped[float] = mapped_column(Float, default=0.0)
    ownership: Mapped[float] = mapped_column(Float, default=0.0)
    selected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    transfers_balance: Mapped[int | None] = mapped_column(Integer, nullable=True)

    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class Prediction(Base):
    """A stored projection, with the provenance needed to audit it later.

    Every value column is nullable. A model that cannot produce a distribution
    records nulls rather than inventing one, in keeping with the rule that an
    absent measurement is never a zero.
    """

    __tablename__ = "predictions"
    __table_args__ = (
        UniqueConstraint(
            "player_code", "season", "gameweek", "horizon", "model_version",
            name="uq_prediction_player_gameweek_model",
        ),
        Index("ix_predictions_season_gameweek", "season", "gameweek"),
        Index("ix_predictions_player_code", "player_code"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_code: Mapped[int] = mapped_column(Integer, nullable=False)
    player_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id"), nullable=True
    )
    season: Mapped[str] = mapped_column(String(9), nullable=False)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False)
    fixture_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    horizon: Mapped[str] = mapped_column(String(20), nullable=False, default="next")

    expected_points: Mapped[float | None] = mapped_column(Float, nullable=True)
    floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    median: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling: Mapped[float | None] = mapped_column(Float, nullable=True)
    expected_minutes: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_probability: Mapped[float | None] = mapped_column(Float, nullable=True)
    confidence: Mapped[str | None] = mapped_column(String(20), nullable=True)

    model_name: Mapped[str] = mapped_column(String(60), nullable=False)
    model_version: Mapped[str] = mapped_column(String(40), nullable=False)
    feature_version: Mapped[str] = mapped_column(String(40), nullable=False)
    information_state: Mapped[str] = mapped_column(String(20), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )


class AuditEvent(Base):
    __tablename__ = "audit_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    action: Mapped[str] = mapped_column(String(50), nullable=False, index=True)
    actor: Mapped[str] = mapped_column(String(100), default="anonymous")
    path: Mapped[str] = mapped_column(String(300), default="")
    client: Mapped[str] = mapped_column(String(60), default="")
    detail: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


class ImportRecord(Base):
    __tablename__ = "import_records"
    __table_args__ = (UniqueConstraint("source_path", name="uq_import_source_path"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    imported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    skipped_count: Mapped[int] = mapped_column(Integer, default=0)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
