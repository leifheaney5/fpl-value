from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    app_name: str = "FPL Value Studio"
    database_url: str = "sqlite:///./data/fpl.db"
    session_secret: str = "development-only-secret-change-me"
    app_username: str | None = None
    app_password: str | None = None
    app_timezone: str = "America/New_York"
    refresh_hour: int = Field(default=10, ge=0, le=23)
    forward_fixture_count: int = Field(default=5, ge=1, le=10)
    history_retention_days: int = Field(default=730, ge=30)
    reliability_sample_minutes: int = Field(default=900, ge=1)
    rotation_season_start_weight: float = Field(default=0.35, ge=0, le=1)
    rotation_season_minutes_weight: float = Field(default=0.25, ge=0, le=1)
    rotation_recent_start_weight: float = Field(default=0.25, ge=0, le=1)
    rotation_recent_minutes_weight: float = Field(default=0.15, ge=0, le=1)
    csrf_enabled: bool = True
    collect_gameweek_history: bool = False
    fpl_bootstrap_url: str = (
        "https://fantasy.premierleague.com/api/bootstrap-static/"
    )
    fpl_fixtures_url: str = (
        "https://fantasy.premierleague.com/api/fixtures/"
    )

    @property
    def auth_enabled(self) -> bool:
        return bool(self.app_username and self.app_password)

    @property
    def sqlalchemy_url(self) -> str:
        url = self.database_url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url.removeprefix("postgres://")
        if url.startswith("postgresql://") and "+psycopg" not in url:
            return "postgresql+psycopg://" + url.removeprefix("postgresql://")
        return url

    @property
    def production_cookie_secure(self) -> bool:
        return self.database_url.startswith(("postgres", "postgresql"))


@lru_cache
def get_settings() -> Settings:
    return Settings()
