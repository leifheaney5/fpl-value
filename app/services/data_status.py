"""Read-only projection of persisted FPL data freshness."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Gameweek, RefreshRun
from app.services.queries import latest_snapshot_time


def _utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


def _run_is_for_season(run: RefreshRun, season: str) -> bool:
    details = run.details if isinstance(run.details, dict) else {}
    recorded_season = details.get("season")
    return recorded_season in (None, "", season)


def _safe_error(value: Any) -> str:
    if not value:
        return ""
    return str(value).replace("\r", " ").replace("\n", " ")[:300]


def data_status(
    db: Session,
    season: str,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Return freshness metadata without contacting FPL or mutating the DB."""
    observed_at = _utc(now) or datetime.now(timezone.utc)
    runs = [
        run
        for run in db.scalars(
            select(RefreshRun).order_by(RefreshRun.started_at.desc())
        ).all()
        if _run_is_for_season(run, season)
    ]
    latest = runs[0] if runs else None
    successful = next(
        (run for run in runs if run.status in {"success", "completed"}),
        None,
    )
    success_at = _utc(
        successful.completed_at if successful else None
    ) or (_utc(successful.started_at) if successful else None)
    snapshot_at = _utc(latest_snapshot_time(db, season))
    age_seconds = (
        max(0.0, (observed_at - snapshot_at).total_seconds())
        if snapshot_at is not None
        else None
    )

    current_event = db.scalar(
        select(Gameweek.number)
        .where(Gameweek.season == season, Gameweek.is_current.is_(True))
        .order_by(Gameweek.number.desc())
        .limit(1)
    )
    if current_event is None:
        current_event = db.scalar(
            select(Gameweek.number)
            .where(Gameweek.season == season)
            .order_by(Gameweek.number.desc())
            .limit(1)
        )

    last_status = None
    if latest is not None:
        last_status = "completed" if latest.status == "success" else latest.status
    last_error = _safe_error(latest.error if latest is not None else "")

    if snapshot_at is None and successful is None:
        state = (
            "failed"
            if latest is not None and latest.status == "failed"
            else "never_refreshed"
        )
    elif latest is not None and latest.status == "failed":
        state = "failed"
    elif age_seconds is not None and age_seconds > 48 * 60 * 60:
        state = "stale"
    else:
        state = "fresh"

    return {
        "last_success_at": success_at,
        "last_run_status": last_status,
        "last_run_error": last_error,
        "snapshot_age_seconds": age_seconds,
        "season": season,
        "current_event": current_event,
        "state": state,
    }
