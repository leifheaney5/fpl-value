from __future__ import annotations

from collections.abc import Generator

from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


settings = get_settings()
is_sqlite = settings.sqlalchemy_url.startswith("sqlite")
connect_args = {"check_same_thread": False} if is_sqlite else {}

# Pooling restored. This briefly used NullPool so that Railway Serverless could
# put the web service to sleep without idle Postgres connections holding it
# awake. The self-hosted container never sleeps, so discarding the pool bought
# nothing and cost a TCP connection and authentication handshake on every single
# request. pool_pre_ping still covers connections dropped while idle.
engine = create_engine(
    settings.sqlalchemy_url,
    pool_pre_ping=True,
    connect_args=connect_args,
)
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
