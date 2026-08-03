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


REQUIRED_DIFFERENTIAL_INPUTS = ("forward_value", "expected_minutes")


def differential_score(snapshot: Any) -> dict[str, Any]:
    """Score how attractive a low-owned player is.

    Requires a projection and an expected-minutes estimate. Without them the
    score is not calculable, and it is reported as such: low ownership on its own
    never makes a player a good differential, and substituting zero for the
    missing inputs would rank an entire preseason database as punts.
    """
    missing = [
        name
        for name in REQUIRED_DIFFERENTIAL_INPUTS
        if getattr(snapshot, name, None) is None
    ]
    if missing:
        return {
            "score": None,
            "category": "Not calculable",
            "confidence": "None",
            "components": {},
            "missing_inputs": missing,
            "explanation": (
                "A differential score needs a projection and an expected-minutes "
                "estimate. "
                + ", ".join(name.replace("_", " ") for name in missing)
                + " is not available yet, so no score is shown."
            ),
        }

    ownership = clamp(safe_float(snapshot.ownership), 0.0, 100.0)
    rotation_risk = snapshot.rotation_risk
    components = {
        "low_ownership": round((100.0 - ownership) * 0.35, 1),
        "forward_value": round(clamp(safe_float(snapshot.forward_value), 0.0, 10.0) * 3.0, 1),
        "expected_minutes": round(clamp(safe_float(snapshot.expected_minutes), 0.0, 90.0) / 90.0 * 20.0, 1),
        "form": round(clamp(safe_float(snapshot.form), 0.0, 10.0), 1),
        "availability": round(clamp(safe_float(snapshot.availability_factor), 0.0, 1.0) * 10.0, 1),
        "rotation_safety": round(
            (100.0 - clamp(safe_float(rotation_risk, 50.0), 0.0, 100.0)) * 0.05, 1
        ),
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

    secure_minutes = safe_float(snapshot.expected_minutes) >= 70
    secure_role = rotation_risk is not None and rotation_risk <= 25
    confidence = "High" if secure_minutes and secure_role else "Medium"
    return {
        "score": score,
        "category": category,
        "confidence": confidence,
        "components": components,
        "missing_inputs": [],
        "explanation": (
            "Weighted from low ownership, projected value, expected minutes, "
            "form, availability and rotation safety."
        ),
    }


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


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a timestamp to UTC.

    SQLite returns naive datetimes while PostgreSQL returns aware ones, so the
    freshness calculation crashed on SQLite. Assume naive timestamps are UTC,
    which is what the refresh pipeline writes.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def build_player_intelligence(rows: list[dict[str, Any]], now: datetime | None = None) -> list[dict[str, Any]]:
    now = _as_utc(now) or datetime.now(timezone.utc)
    result = []
    for row in rows:
        snapshot = row["snapshot"]
        captured_at = snapshot.captured_at
        reference = _as_utc(captured_at)
        age_hours = (
            max(0.0, (now - reference).total_seconds() / 3600.0)
            if reference is not None
            else None
        )
        result.append({
            **row,
            "differential": differential_score(snapshot),
            "transfer_trend": _transfer_trend(row),
            "provenance": {
                "source": "Official FPL bootstrap-static snapshot",
                "captured_at": captured_at,
                "age_hours": None if age_hours is None else round(age_hours, 1),
                "freshness": (
                    "Unknown"
                    if age_hours is None
                    else "Fresh" if age_hours <= 48 else "Stale"
                ),
                "status": "Calculated",
            },
        })
    return result
