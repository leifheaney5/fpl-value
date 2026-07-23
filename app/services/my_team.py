from __future__ import annotations

import time
from typing import Any

from sqlalchemy.orm import Session

from app.api.fpl_client import FPLClient
from app.config import Settings
from app.services.queries import latest_rows


_REMOTE_CACHE: dict[int, tuple[float, dict[str, Any]]] = {}


def _remote_team_data(client: FPLClient, entry_id: int) -> dict[str, Any]:
    now = time.monotonic()
    cached = _REMOTE_CACHE.get(entry_id)
    if cached and now - cached[0] < 300:
        return cached[1]
    try:
        entry = client.entry(entry_id)
        current_event = entry.get("current_event")
        history: dict[str, Any] = {"current": []}
        benchmark_events: list[dict[str, Any]] = []
        picks: list[dict[str, Any]] = []
        if current_event:
            history = client.entry_history(entry_id)
            benchmark_events = client.bootstrap().get("events", [])
            picks = client.entry_picks(entry_id, int(current_event)).get("picks", [])
        data = {"entry": entry, "history": history, "benchmark_events": benchmark_events, "picks": picks}
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
    current_event = entry.get("current_event")
    history = remote["history"]
    benchmark_events = remote["benchmark_events"]
    picks = remote["picks"]

    rows_by_id = {row["player"].id: row for row in latest_rows(db)}
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
