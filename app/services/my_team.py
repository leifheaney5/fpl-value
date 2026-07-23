from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from app.api.fpl_client import FPLClient
from app.config import Settings
from app.services.queries import latest_rows


def linked_team_data(db: Session, client: FPLClient, settings: Settings) -> dict[str, Any] | None:
    """Return public team information and current picks when an entry is configured."""
    if not settings.fpl_entry_id:
        return None
    try:
        entry = client.entry(settings.fpl_entry_id)
    except (RuntimeError, ValueError):
        return {"entry_id": settings.fpl_entry_id, "error": "Team data is temporarily unavailable."}

    current_event = entry.get("current_event")
    picks: list[dict[str, Any]] = []
    if current_event:
        try:
            picks = client.entry_picks(settings.fpl_entry_id, int(current_event)).get("picks", [])
        except (RuntimeError, ValueError):
            picks = []

    rows_by_id = {row["player"].id: row for row in latest_rows(db)}
    squad = []
    for pick in picks:
        row = rows_by_id.get(pick.get("element"))
        if row:
            squad.append({"row": row, "pick": pick})
    return {
        "entry_id": settings.fpl_entry_id,
        "name": entry.get("name") or "Linked FPL team",
        "manager": f"{entry.get('player_first_name', '')} {entry.get('player_last_name', '')}".strip(),
        "overall_rank": entry.get("summary_overall_rank"),
        "overall_points": entry.get("summary_overall_points"),
        "event": current_event,
        "squad": squad,
        "picks_available": bool(picks),
    }
