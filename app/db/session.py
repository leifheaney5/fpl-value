from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import NullPool

from app.config import get_settings


settings = get_settings()
is_sqlite = settings.sqlalchemy_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

engine_kwargs: dict[str, object] = {
    "pool_pre_ping": True,
    "connect_args": connect_args,
}
# The hosted web service is low traffic and refresh work runs as a short-lived
# Railway cron job. Avoid retaining idle Postgres connections between requests
# so Serverless can put the web service to sleep cleanly.
if not is_sqlite:
    engine_kwargs["poolclass"] = NullPool

engine = create_engine(settings.sqlalchemy_url, **engine_kwargs)
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    expire_on_commit=False,
)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
