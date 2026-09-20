from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.fpl_client import FPLClient, FPLRequestError
from app.config import Settings
from app.db.models import LinkedTeamSnapshot
from app.services.freshness import FreshnessCache, MY_TEAM_POLICY
from app.services.queries import latest_rows


REMOTE_CACHE_POLICY = MY_TEAM_POLICY
_REMOTE_CACHE = FreshnessCache()


def _picks_event_candidates(entry: dict[str, Any], events: list[dict[str, Any]]) -> list[int]:
    """Return every usable FPL event, newest first, regardless of flags."""
    candidates: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        try:
            event_id = int(event.get("id"))
        except (TypeError, ValueError):
            continue
        if event_id > 0:
            candidates.add(event_id)
    try:
        current_event = int(entry.get("current_event"))
    except (TypeError, ValueError):
        current_event = 0
    if current_event > 0:
        candidates.add(current_event)
    return sorted(candidates, reverse=True)


def clear_remote_team_cache(entry_id: int | None = None) -> None:
    """Invalidate one linked team, or every linked team when no ID is given."""
    key = f"entry:{entry_id}" if entry_id is not None else None
    _REMOTE_CACHE.invalidate(key)


def _load_remote_team_data(client: FPLClient, entry_id: int) -> dict[str, Any]:
    """Load one linked-team snapshot without caching or presentation metadata."""
    with ThreadPoolExecutor(max_workers=2) as executor:
        entry_future = executor.submit(client.entry, entry_id)
        bootstrap_future = executor.submit(client.bootstrap)
        entry = entry_future.result()
        benchmark_events = bootstrap_future.result().get("events", [])

    if not isinstance(benchmark_events, list):
        benchmark_events = []
    picks: list[dict[str, Any]] = []
    picks_event: int | None = None
    for candidate in _picks_event_candidates(entry, benchmark_events):
        try:
            candidate_payload = client.entry_picks(entry_id, candidate)
        except FPLRequestError as error:
            if error.status_code == 404:
                continue
            raise
        except RuntimeError as error:
            # Some test doubles and older client implementations expose the
            # unpublished-picks condition without an HTTP status. Treat only
            # that explicit condition as an expected fallback; timeouts and
            # other runtime failures must preserve the newer snapshot.
            error_text = str(error).lower()
            if "not published" in error_text or "not been published" in error_text:
                continue
            raise
        except ValueError:
            raise
        candidate_picks = candidate_payload.get("picks") if isinstance(candidate_payload, dict) else None
        if isinstance(candidate_picks, list) and candidate_picks:
            picks = candidate_picks
            picks_event = candidate
            break

    history: dict[str, Any] = {"current": []}
    if picks_event is not None:
        history = client.entry_history(entry_id)
    return {
        "entry": entry,
        "history": history,
        "benchmark_events": benchmark_events,
        "picks": picks,
        "picks_event": picks_event,
    }


def _freshness_metadata(record: Any, event: int | None) -> dict[str, Any]:
    return {
        "dataset": REMOTE_CACHE_POLICY.name,
        "fetched_at": record.fetched_at,
        "expires_at": record.expires_at,
        "event": event,
        "cache_hit": record.cache_hit,
        "stale": record.stale,
        "last_error": record.last_error,
    }


def _remote_team_data(
    client: FPLClient, entry_id: int, *, force: bool = False
) -> dict[str, Any]:
    """Return a cached linked-team snapshot with internal freshness metadata."""
    try:
        record = _REMOTE_CACHE.get(
            f"entry:{entry_id}",
            lambda: _load_remote_team_data(client, entry_id),
            REMOTE_CACHE_POLICY,
            force=force,
        )
    except (RuntimeError, ValueError) as error:
        return {
            "error": "Team data is temporarily unavailable.",
            "_freshness": {
                "dataset": REMOTE_CACHE_POLICY.name,
                "fetched_at": None,
                "expires_at": None,
                "event": None,
                "cache_hit": False,
                "stale": False,
                "last_error": str(error),
            },
        }
    data = dict(record.value)
    data["_freshness"] = _freshness_metadata(record, data.get("picks_event"))
    return data


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _snapshot_freshness(snapshot: LinkedTeamSnapshot, error: str = "") -> dict[str, Any]:
    return {
        "dataset": REMOTE_CACHE_POLICY.name,
        "fetched_at": snapshot.fetched_at,
        "expires_at": snapshot.expires_at,
        "event": snapshot.selected_event,
        "cache_hit": True,
        "stale": True,
        "last_error": error or snapshot.last_error,
    }


def _persist_snapshot(
    db: Session,
    entry_id: int,
    remote: dict[str, Any],
) -> None:
    """Upsert payload metadata without storing credentials or session data."""
    freshness = remote.get("_freshness") or {}
    stale = bool(freshness.get("stale"))
    error = str(freshness.get("last_error") or "")[:300]
    payload = {
        key: value for key, value in remote.items() if key != "_freshness"
    }
    now = _now_utc()
    existing = db.scalar(
        select(LinkedTeamSnapshot).where(
            LinkedTeamSnapshot.entry_id == entry_id
        )
    )
    if existing is None:
        existing = LinkedTeamSnapshot(
            entry_id=entry_id,
            payload=payload,
            selected_event=payload.get("picks_event"),
            fetched_at=now,
            expires_at=now + timedelta(seconds=REMOTE_CACHE_POLICY.ttl_seconds),
            stale=stale,
            last_error=error,
            updated_at=now,
        )
        db.add(existing)
    else:
        existing.payload = payload
        existing.selected_event = payload.get("picks_event")
        existing.stale = stale
        existing.last_error = error
        existing.updated_at = now
        if not stale:
            existing.fetched_at = now
            existing.expires_at = now + timedelta(
                seconds=REMOTE_CACHE_POLICY.ttl_seconds
            )
    db.commit()


def _team_view(
    db: Session,
    remote: dict[str, Any],
    settings: Settings,
    rows: list[dict[str, Any]] | None,
) -> dict[str, Any]:
    entry = remote["entry"]
    current_event = remote["picks_event"]
    history = remote["history"]
    benchmark_events = remote["benchmark_events"]
    picks = remote["picks"]

    current_rows = rows if rows is not None else latest_rows(db, settings.current_season)
    rows_by_id = {row["player"].id: row for row in current_rows}
    squad = []
    for pick in picks:
        row = rows_by_id.get(pick.get("element"))
        if row:
            squad.append({"row": row, "pick": pick})
    benchmark_by_event = {event.get("id"): event for event in benchmark_events}
    average_total = 0
    performance = []
    for item in history.get("current", []):
        event_id = item.get("event")
        event = benchmark_by_event.get(event_id, {})
        average_total += event.get("average_entry_score") or 0
        performance.append({
            "event": event_id,
            "points": item.get("points"),
            "total_points": item.get("total_points"),
            "average_total": average_total,
            "top_10k_total": None,
            "top_10_percent_total": None,
        })
    return {
        "entry_id": settings.fpl_entry_id,
        "name": entry.get("name") or "Linked FPL team",
        "manager": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "overall_rank": entry.get("summary_overall_rank"),
        "overall_points": entry.get("summary_overall_points"),
        "event": current_event,
        "squad": squad,
        "picks_available": bool(picks),
        "performance": performance,
        "benchmark_status": "The public FPL payload does not currently provide top-10k or top-10% performance series.",
        "_freshness": remote["_freshness"],
    }


def linked_team_data(
    db: Session,
    client: FPLClient,
    settings: Settings,
    *,
    rows: list[dict[str, Any]] | None = None,
) -> dict[str, Any] | None:
    """Return public team information and current picks when an entry is configured."""
    if not settings.fpl_entry_id:
        return None
    remote = _remote_team_data(client, settings.fpl_entry_id)
    if "error" in remote:
        if db is not None:
            snapshot = db.scalar(
                select(LinkedTeamSnapshot).where(
                    LinkedTeamSnapshot.entry_id == settings.fpl_entry_id
                )
            )
            if snapshot is not None:
                remote = dict(snapshot.payload)
                remote["_freshness"] = _snapshot_freshness(
                    snapshot, remote.get("error", "")
                )
        if "error" in remote:
            return {
                "entry_id": settings.fpl_entry_id,
                "error": remote["error"],
                "_freshness": remote["_freshness"],
            }
    if db is not None:
        _persist_snapshot(db, settings.fpl_entry_id, remote)
    return _team_view(db, remote, settings, rows)


def transfer_plan(team: dict[str, Any] | None, recommendation: dict[str, Any] | None) -> list[dict[str, Any]]:
    if not team or not team.get("picks_available") or not recommendation:
        return []
    current = {item["row"]["player"].id: item["row"] for item in team["squad"]}
    suggested = [item["row"] for item in recommendation["starting"] + recommendation["bench"]]
    incoming = [row for row in suggested if row["player"].id not in current]
    outgoing = [row for row in current.values() if row["player"].id not in {item["player"].id for item in suggested}]
    plan = []
    for position in ("GKP", "DEF", "MID", "FWD"):
        sellers = [row for row in outgoing if row["player"].position_short == position]
        buyers = [row for row in incoming if row["player"].position_short == position]
        for seller, buyer in zip(sellers, buyers):
            plan.append({
                "out": seller,
                "in": buyer,
                "price_change": round(float(buyer["snapshot"].price) - float(seller["snapshot"].price), 1),
                "reason": "Higher recommender score in the selected strategy.",
            })
    return plan
