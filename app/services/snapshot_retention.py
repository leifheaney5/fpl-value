"""Keep hourly snapshots recent, and one a day after that.

Every refresh stores a snapshot per player, raw payload included: about 1.4 MB
a run. Daily that is modest. Hourly it is roughly 12 GB a year, on a host whose
disk is nearly full. Hour-level history is only useful for the recent past, so
older days are collapsed to their final snapshot. Day, week and month deltas
read the latest snapshot at or before a target time and are unaffected.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db.models import PlayerSnapshot


def _as_utc(value: datetime) -> datetime:
    """SQLite hands back naive datetimes; they were stored as UTC."""
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def thin_snapshots(
    db: Session,
    season: str,
    *,
    now: datetime,
    keep_hours: int,
    timezone_name: str,
) -> int:
    """Delete all but the last snapshot of each local day older than ``keep_hours``.

    Only the given season is touched: imported snapshots of earlier seasons are
    not the refresh job's to prune. The day boundary is the application's
    timezone, so a late-evening refresh belongs to the day it was run on.
    Returns the number of snapshot rows removed.
    """
    cutoff = now - timedelta(hours=keep_hours)
    zone = ZoneInfo(timezone_name)
    captures = db.scalars(
        select(PlayerSnapshot.captured_at)
        .where(
            PlayerSnapshot.season == season,
            PlayerSnapshot.captured_at < cutoff,
        )
        .distinct()
    ).all()

    latest_by_day: dict[object, datetime] = {}
    for captured in captures:
        day = _as_utc(captured).astimezone(zone).date()
        if day not in latest_by_day or _as_utc(captured) > _as_utc(latest_by_day[day]):
            latest_by_day[day] = captured
    surplus = [value for value in captures if value not in latest_by_day.values()]
    if not surplus:
        return 0

    result = db.execute(
        delete(PlayerSnapshot).where(
            PlayerSnapshot.season == season,
            PlayerSnapshot.captured_at.in_(surplus),
        )
    )
    return result.rowcount or 0
