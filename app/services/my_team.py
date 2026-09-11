from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.api.fpl_client import FPLClient
from app.config import Settings
from app.services.queries import latest_rows


REMOTE_CACHE_TTL_SECONDS = 600
_REMOTE_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}


def _picks_event(entry: dict[str, Any], events: list[dict[str, Any]]) -> int | None:
    """Choose the event whose saved squad should be shown right now."""
    upcoming = next(
        (event for event in events if event.get("is_next") and event.get("id")),
        None,
    )
    current = next(
        (event for event in events if event.get("is_current") and event.get("id")),
        None,
    )
    selected = upcoming or current
    if selected is not None:
        return int(selected["id"])
    raw_event = entry.get("current_event")
    return int(raw_event) if raw_event else None


def _picks_event_candidates(entry: dict[str, Any], events: list[dict[str, Any]]) -> list[int]:
    candidates = []
    for flag in ("is_next", "is_current"):
        for event in events:
            if event.get(flag):
                event_id = event.get("id")
                if event_id:
                    candidates.append(int(event_id))
    raw_event = entry.get("current_event")
    if raw_event:
        candidates.append(int(raw_event))
    return list(dict.fromkeys(candidates))


def clear_remote_team_cache(entry_id: int | None = None) -> None:
    """Invalidate one linked team, or every linked team when no ID is given."""
    if entry_id is None:
        _REMOTE_CACHE.clear()
    else:
        _REMOTE_CACHE.pop(entry_id, None)


def _remote_team_data(client: FPLClient, entry_id: int) -> dict[str, Any]:
    now = time.monotonic()
    cached = _REMOTE_CACHE.get(entry_id)
    if cached and now - cached[0] < REMOTE_CACHE_TTL_SECONDS:
        return cached[1]
    try:
        entry = client.entry(entry_id)
        benchmark_events = client.bootstrap().get("events", [])
        event_candidates = _picks_event_candidates(entry, benchmark_events)
        history: dict[str, Any] = {"current": []}
        picks: list[dict[str, Any]] = []
        picks_event = None
        last_picks_error: Exception | None = None
        for candidate in event_candidates:
            try:
                candidate_picks = client.entry_picks(entry_id, candidate).get("picks", [])
            except (RuntimeError, ValueError) as exc:
                last_picks_error = exc
                continue
            picks_event = candidate
            picks = candidate_picks
            if picks:
                break
        if picks_event is None and last_picks_error is not None:
            raise last_picks_error
        if picks_event:
            history = client.entry_history(entry_id)
        data = {
            "entry": entry,
            "history": history,
            "benchmark_events": benchmark_events,
            "picks": picks,
            "picks_event": picks_event,
        }
    except (RuntimeError, ValueError):
        data = {"error": "Team data is temporarily unavailable."}
    _REMOTE_CACHE[entry_id] = (now, data)
    return data


def linked_team_data(db: Session, client: FPLClient, settings: Settings) -> dict[str, Any] | None:
    """Return public team information and current picks when an entry is configured."""
    if not settings.fpl_entry_id:
        return None
    remote = _remote_team_data(client, settings.fpl_entry_id)
    if "error" in remote:
        return {"entry_id": settings.fpl_entry_id, "error": remote["error"]}
    entry = remote["entry"]
    current_event = remote["picks_event"]
    history = remote["history"]
    benchmark_events = remote["benchmark_events"]
    picks = remote["picks"]

    rows_by_id = {row["player"].id: row for row in latest_rows(db, settings.current_season)}
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
    }


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
