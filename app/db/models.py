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

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
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
    points_per_minute: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_90: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_start: Mapped[float] = mapped_column(Float, default=0.0)
    points_per_team_match: Mapped[float] = mapped_column(Float, default=0.0)
    value_per_90: Mapped[float] = mapped_column(Float, default=0.0)
    start_rate: Mapped[float] = mapped_column(Float, default=0.0)
    minutes_per_team_match: Mapped[float] = mapped_column(Float, default=0.0)
    average_minutes_per_start: Mapped[float] = mapped_column(Float, default=0.0)

    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    expected_goal_involvements: Mapped[float] = mapped_column(
        Float, default=0.0
    )
    ict_index: Mapped[float] = mapped_column(Float, default=0.0)
    ownership: Mapped[float] = mapped_column(Float, default=0.0)

    value: Mapped[float] = mapped_column(Float, default=0.0)
    value_rank: Mapped[int | None] = mapped_column(Integer, nullable=True)
    value_percentile: Mapped[float | None] = mapped_column(
        Float, nullable=True
    )
    value_tier: Mapped[str] = mapped_column(String(30), default="Not Ranked")

    reliability_factor: Mapped[float] = mapped_column(Float, default=0.0)
    reliable_value: Mapped[float] = mapped_column(Float, default=0.0)
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
    expected_minutes: Mapped[float] = mapped_column(Float, default=0.0)
    projected_points_5: Mapped[float] = mapped_column(Float, default=0.0)
    upcoming_fixture_count: Mapped[int] = mapped_column(Integer, default=0)
    average_fixture_difficulty: Mapped[float] = mapped_column(Float, default=0.0)
    forward_value: Mapped[float] = mapped_column(Float, default=0.0)
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
    upcoming_fixtures: Mapped[list[dict[str, Any]]] = mapped_column(
        JSON, default=list
    )
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)

    player: Mapped[Player] = relationship(back_populates="snapshots")
    refresh_run: Mapped[RefreshRun] = relationship()


class GameweekHistory(Base):
    __tablename__ = "gameweek_history"
    __table_args__ = (
        UniqueConstraint("player_id", "gameweek", name="uq_player_gameweek"),
        Index("ix_gameweek_player_event", "player_id", "gameweek"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[int] = mapped_column(ForeignKey("players.id"), nullable=False)
    gameweek: Mapped[int] = mapped_column(Integer, nullable=False)
    opponent: Mapped[str] = mapped_column(String(100), default="")
    is_home: Mapped[bool] = mapped_column(Boolean, default=False)
    minutes: Mapped[int] = mapped_column(Integer, default=0)
    started: Mapped[bool] = mapped_column(Boolean, default=False)
    points: Mapped[int] = mapped_column(Integer, default=0)
    goals: Mapped[int] = mapped_column(Integer, default=0)
    assists: Mapped[int] = mapped_column(Integer, default=0)
    clean_sheets: Mapped[int] = mapped_column(Integer, default=0)
    bonus: Mapped[int] = mapped_column(Integer, default=0)
    expected_goals: Mapped[float] = mapped_column(Float, default=0.0)
    expected_assists: Mapped[float] = mapped_column(Float, default=0.0)
    price: Mapped[float] = mapped_column(Float, default=0.0)
    ownership: Mapped[float] = mapped_column(Float, default=0.0)
    captured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


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
