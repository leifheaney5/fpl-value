from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.analytics.metrics import clamp, safe_float


def _transfer_totals(snapshot: Any | None) -> tuple[int, int] | None:
    if snapshot is None:
        return None
    raw = getattr(snapshot, "raw", {}) or {}
    if "transfers_in_event" not in raw or "transfers_out_event" not in raw:
        return None
    return (int(safe_float(raw.get("transfers_in_event"))), int(safe_float(raw.get("transfers_out_event"))))


def differential_score(snapshot: Any) -> dict[str, Any]:
    ownership = clamp(safe_float(snapshot.ownership), 0.0, 100.0)
    components = {
        "low_ownership": round((100.0 - ownership) * 0.35, 1),
        "forward_value": round(clamp(safe_float(snapshot.forward_value), 0.0, 10.0) * 3.0, 1),
        "expected_minutes": round(clamp(safe_float(snapshot.expected_minutes), 0.0, 90.0) / 90.0 * 20.0, 1),
        "form": round(clamp(safe_float(snapshot.form), 0.0, 10.0), 1),
        "availability": round(clamp(safe_float(snapshot.availability_factor), 0.0, 1.0) * 10.0, 1),
        "rotation_safety": round((100.0 - clamp(safe_float(snapshot.rotation_risk, 50.0), 0.0, 100.0)) * 0.05, 1),
    }
    score = round(sum(components.values()), 1)
    if ownership <= 5.0 and score >= 75:
        category = "Safe differential"
    elif ownership <= 10.0 and score >= 60:
        category = "Moderate-risk differential"
    elif ownership <= 5.0:
        category = "High-upside punt"
    else:
        category = "Emerging differential"
    confidence = "High" if snapshot.expected_minutes >= 70 and snapshot.rotation_risk <= 25 else "Medium"
    return {"score": score, "category": category, "confidence": confidence, "components": components}


def _transfer_trend(row: dict[str, Any]) -> dict[str, Any]:
    current = _transfer_totals(row["snapshot"])
    previous = _transfer_totals(row.get("history", {}).get("1D", {}).get("snapshot"))
    if current is None or previous is None:
        return {"classification": "Not available", "velocity": None, "acceleration": None, "net": None}
    current_net = current[0] - current[1]
    previous_net = previous[0] - previous[1]
    velocity = current_net - previous_net
    classification = "Spiking" if velocity >= 25 else "Rising" if velocity > 0 else "Declining" if velocity < 0 else "Stable"
    return {"classification": classification, "velocity": velocity, "acceleration": None, "net": current_net}


def build_player_intelligence(rows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = now or datetime.now(timezone.utc)
    result = []
    for row in rows:
        snapshot = row["snapshot"]
        captured_at = snapshot.captured_at
        age_hours = max(0.0, (now - captured_at).total_seconds() / 3600.0)
        result.append({
            **row,
            "differential": differential_score(snapshot),
            "transfer_trend": _transfer_trend(row),
            "provenance": {
                "source": "Official FPL bootstrap-static snapshot",
                "captured_at": captured_at,
                "freshness": "Fresh" if age_hours <= 48 else "Stale",
                "status": "Calculated",
            },
        })
    return result
