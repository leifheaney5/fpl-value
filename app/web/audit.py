from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from fastapi import Request
from sqlalchemy.orm import Session

from app.db.models import AuditEvent

logger = logging.getLogger(__name__)

_SENSITIVE = {
    "password",
    "csrf_token",
    "api_key",
    "session_secret",
    "token",
    "secret",
    "authorization",
}


def _clean(detail: dict[str, Any] | None) -> dict[str, Any]:
    return {
        key: ("[redacted]" if key.casefold() in _SENSITIVE else value)
        for key, value in (detail or {}).items()
    }


def record(
    db: Session,
    action: str,
    request: Request,
    actor: str | None = None,
    detail: dict[str, Any] | None = None,
) -> None:
    """Write an audit row.

    Never raises. An audit write failure must not turn a successful request
    into a 500, so the exception is logged and swallowed.
    """
    try:
        db.add(
            AuditEvent(
                occurred_at=datetime.now(timezone.utc),
                action=action,
                actor=actor or "anonymous",
                path=str(request.url.path),
                client=request.client.host if request.client else "",
                detail=_clean(detail),
            )
        )
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("audit_write_failed action=%s", action)
    logger.info(
        "audit action=%s path=%s client=%s",
        action,
        request.url.path,
        request.client.host if request.client else "",
    )
