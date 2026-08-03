from __future__ import annotations

from datetime import timedelta
from typing import Any

from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

from app.db.models import Player, PlayerSnapshot, RefreshRun, SchemaChange, Team


def _sort_key(value: Any, descending: bool) -> tuple[int, float]:
    """Order a possibly-null metric.

    Unavailable values sort last in either direction rather than being coerced
    to zero, which would rank a player we know nothing about alongside one we
    know scores nothing.
    """
    if value is None:
        return (1, 0.0)
    return (0, -float(value) if descending else float(value))


def _at_least(value: Any, threshold: float) -> bool:
    """A null metric never satisfies a minimum filter."""
    return value is not None and float(value) >= threshold


def _at_most(value: Any, threshold: float) -> bool:
    """A null metric never satisfies a maximum filter."""
    return value is not None and float(value) <= threshold


def latest_snapshot_time(db: Session):
    return db.scalar(select(func.max(PlayerSnapshot.captured_at)))


def _direction(value: float | None) -> str:
    if value is None:
        return "No History"
    if value > 0.0005:
        return "Increase"
    if value < -0.0005:
        return "Decrease"
    return "No Change"


def _historical_map(
    db: Session,
    target_time,
) -> dict[int, PlayerSnapshot]:
    ranked = (
        select(
            PlayerSnapshot.id.label("snapshot_id"),
            func.row_number()
            .over(
                partition_by=PlayerSnapshot.player_id,
                order_by=PlayerSnapshot.captured_at.desc(),
            )
            .label("row_number"),
        )
        .where(PlayerSnapshot.captured_at <= target_time)
        .subquery()
    )
    snapshots = db.scalars(
        select(PlayerSnapshot)
        .join(ranked, PlayerSnapshot.id == ranked.c.snapshot_id)
        .where(ranked.c.row_number == 1)
    ).all()
    return {snapshot.player_id: snapshot for snapshot in snapshots}


class CrossSeasonError(ValueError):
    """Raised when a calculation would mix observations from different seasons.

    A previous-season snapshot differenced against a current-season one produces
    a number that looks like movement but measures a season rollover.
    """


def _delta(current: Any, previous: Any, digits: int) -> float | None:
    """Difference two possibly-null metrics; null in, null out."""
    if current is None or previous is None:
        return None
    return round(current - previous, digits)


def _history_comparison(
    current: PlayerSnapshot,
    previous: PlayerSnapshot | None,
) -> dict[str, Any]:
    if previous is None:
        return {
            "reference": None,
            "snapshot": None,
            "value": None,
            "delta_value": None,
            "value_direction": "No History",
            "price": None,
            "delta_price": None,
            "price_direction": "No History",
            "delta_ownership": None,
            "delta_rank": None,
        }

    current_season = getattr(current, "season", None)
    previous_season = getattr(previous, "season", None)
    if current_season and previous_season and current_season != previous_season:
        raise CrossSeasonError(
            f"Refusing to compare a {previous_season} snapshot against "
            f"{current_season}: the difference would measure a season rollover, "
            "not player movement."
        )

    delta_value = _delta(current.value, previous.value, 3)
    delta_price = _delta(current.price, previous.price, 1)
    delta_ownership = _delta(current.ownership, previous.ownership, 2)
    delta_rank = (
        previous.value_rank - current.value_rank
        if previous.value_rank is not None
        and current.value_rank is not None
        else None
    )

    return {
        "reference": previous.captured_at,
        "snapshot": previous,
        "value": previous.value,
        "delta_value": delta_value,
        "value_direction": _direction(delta_value),
        "price": previous.price,
        "delta_price": delta_price,
        "price_direction": _direction(delta_price),
        "delta_ownership": delta_ownership,
        "delta_rank": delta_rank,
    }


def latest_rows(db: Session) -> list[dict[str, Any]]:
    latest = latest_snapshot_time(db)
    if latest is None:
        return []

    rows = db.execute(
        select(PlayerSnapshot, Player, Team)
        .join(Player, Player.id == PlayerSnapshot.player_id)
        .join(Team, Team.id == Player.team_id)
        .where(PlayerSnapshot.captured_at == latest)
    ).all()

    historical = {
        "1D": _historical_map(db, latest - timedelta(days=1)),
        "7D": _historical_map(db, latest - timedelta(days=7)),
        "30D": _historical_map(db, latest - timedelta(days=30)),
    }

    result = []
    for snapshot, player, team in rows:
        result.append(
            {
                "snapshot": snapshot,
                "player": player,
                "team": team,
                "history": {
                    label: _history_comparison(
                        snapshot,
                        mapping.get(snapshot.player_id),
                    )
                    for label, mapping in historical.items()
                },
            }
        )
    return result


def filtered_players(
    db: Session,
    *,
    position: str | None = None,
    max_price: float | None = None,
    max_rotation: float | None = None,
    min_minutes: int | None = None,
    min_starts: int | None = None,
    min_start_rate: float | None = None,
    min_reliable_value: float | None = None,
    min_forward_value: float | None = None,
    max_ownership: float | None = None,
    status: str | None = None,
    team_id: int | None = None,
    sort: str = "reliable_value",
    movement_period: str = "1D",
) -> list[dict[str, Any]]:
    rows = latest_rows(db)

    if position:
        rows = [
            row for row in rows
            if row["player"].position_short == position
        ]
    if max_price is not None:
        rows = [
            row for row in rows
            if row["snapshot"].price <= max_price
        ]
    if max_rotation is not None:
        rows = [row for row in rows if _at_most(row["snapshot"].rotation_risk, max_rotation)]
    if min_minutes is not None:
        rows = [
            row for row in rows
            if row["snapshot"].minutes >= min_minutes
        ]
    if min_starts is not None:
        rows = [row for row in rows if row["snapshot"].starts >= min_starts]
    if min_start_rate is not None:
        rows = [row for row in rows if _at_least(row["snapshot"].start_rate, min_start_rate)]
    if min_reliable_value is not None:
        rows = [row for row in rows if _at_least(row["snapshot"].reliable_value, min_reliable_value)]
    if min_forward_value is not None:
        rows = [row for row in rows if _at_least(row["snapshot"].forward_value, min_forward_value)]
    if max_ownership is not None:
        rows = [row for row in rows if row["snapshot"].ownership <= max_ownership]
    if status:
        rows = [row for row in rows if row["player"].status == status]
    if team_id is not None:
        rows = [row for row in rows if row["player"].team_id == team_id]

    allowed = {
        "value": "value",
        "reliable_value": "reliable_value",
        "forward_value": "forward_value",
        "total_points": "total_points",
        "price": "price",
        "rotation_risk": "rotation_risk",
        "ownership": "ownership",
        "points_per_90": "points_per_90",
        "starts": "starts",
        "start_rate": "start_rate",
        "expected_minutes": "expected_minutes",
    }
    if sort in {"value_movement", "price_movement", "ownership_movement", "rank_movement"}:
        history_key = {
            "value_movement": "delta_value",
            "price_movement": "delta_price",
            "ownership_movement": "delta_ownership",
            "rank_movement": "delta_rank",
        }[sort]
        rows.sort(key=lambda row: row["history"].get(movement_period, {}).get(history_key) is not None, reverse=True)
        rows.sort(key=lambda row: row["history"].get(movement_period, {}).get(history_key) or 0, reverse=True)
        return rows
    key = allowed.get(sort, "reliable_value")
    # Rotation risk is the one metric where lower is better.
    descending = key != "rotation_risk"
    rows.sort(key=lambda row: _sort_key(getattr(row["snapshot"], key), descending))
    return rows


def player_history(
    db: Session,
    player_id: int,
    limit: int = 180,
) -> list[PlayerSnapshot]:
    return list(
        db.scalars(
            select(PlayerSnapshot)
            .where(PlayerSnapshot.player_id == player_id)
            .order_by(PlayerSnapshot.captured_at.desc())
            .limit(limit)
        ).all()
    )[::-1]


def latest_run(db: Session) -> RefreshRun | None:
    return db.scalar(
        select(RefreshRun)
        .order_by(RefreshRun.started_at.desc())
        .limit(1)
    )


def recent_schema_changes(db: Session, limit: int = 50):
    return db.scalars(
        select(SchemaChange)
        .order_by(SchemaChange.detected_at.desc())
        .limit(limit)
    ).all()


# A delta must clear its threshold to count as movement. Without this, players
# whose value did not change at all appeared in both the risers and the fallers.
MOVEMENT_THRESHOLDS = {
    "value": 0.01,
    "price": 0.05,
    "ownership": 0.10,
    "rank": 1.0,
}
MOVEMENT_FIELDS = {
    "value": "delta_value",
    "price": "delta_price",
    "ownership": "delta_ownership",
    "rank": "delta_rank",
}


def classify_movement(delta: float | None, threshold: float) -> str:
    """Classify a delta as movement in one direction, or as no movement at all.

    ``riser`` and ``faller`` are mutually exclusive by construction, and neither
    can contain an unchanged player.
    """
    if delta is None:
        return "no_history"
    if delta > threshold:
        return "riser"
    if delta < -threshold:
        return "faller"
    return "unchanged"


def movers_data(
    db: Session,
    period: str = "7D",
    thresholds: dict[str, float] | None = None,
    limit: int = 20,
) -> dict[str, Any]:
    if period not in {"1D", "7D", "30D"}:
        period = "7D"
    thresholds = {**MOVEMENT_THRESHOLDS, **(thresholds or {})}
    rows = latest_rows(db)

    result: dict[str, Any] = {}
    for label, field in MOVEMENT_FIELDS.items():
        threshold = thresholds[label]
        risers: list[dict[str, Any]] = []
        fallers: list[dict[str, Any]] = []
        observed = 0
        for row in rows:
            delta = row["history"][period].get(field)
            classification = classify_movement(delta, threshold)
            if classification == "no_history":
                continue
            observed += 1
            if classification == "riser":
                risers.append(row)
            elif classification == "faller":
                fallers.append(row)

        risers.sort(key=lambda row: row["history"][period][field], reverse=True)
        fallers.sort(key=lambda row: row["history"][period][field])
        result[f"{label}_risers"] = risers[:limit]
        result[f"{label}_fallers"] = fallers[:limit]
        result[f"{label}_window"] = {
            "period": period,
            "threshold": threshold,
            "observations": observed,
            "risers": len(risers),
            "fallers": len(fallers),
            "unchanged": observed - len(risers) - len(fallers),
        }
    return result


def diagnostics_data(db: Session) -> dict[str, Any]:
    rows = latest_rows(db)
    history_points = sum(1 for row in rows if len(player_history(db, row["player"].id, 2)) >= 2)
    return {
        "rows": rows,
        "history_points": history_points,
        "sufficient": history_points >= 10,
        "message": "Diagnostics require at least two snapshots for ten players." if history_points < 10 else "Historical diagnostic inputs are available; outcome calibration is still heuristic.",
    }


def dashboard_data(db: Session) -> dict[str, Any]:
    rows = latest_rows(db)
    snapshots = [row["snapshot"] for row in rows]

    # Only players with a value can be ranked. Padding these lists with
    # unavailable players would present them as the best available.
    by_reliable = sorted(
        [row for row in rows if row["snapshot"].reliable_value is not None],
        key=lambda row: row["snapshot"].reliable_value,
        reverse=True,
    )
    by_forward = sorted(
        [row for row in rows if row["snapshot"].forward_value is not None],
        key=lambda row: row["snapshot"].forward_value,
        reverse=True,
    )
    low_rotation = sorted(
        [
            row for row in rows
            if row["snapshot"].rotation_risk is not None
        ],
        key=lambda row: row["snapshot"].rotation_risk,
    )

    positions: dict[str, list[float]] = {}
    for row in rows:
        if row["snapshot"].reliable_value is None:
            continue
        positions.setdefault(
            row["player"].position_short, []
        ).append(row["snapshot"].reliable_value)

    position_averages = {
        position: round(sum(values) / len(values), 3)
        for position, values in positions.items()
        if values
    }

    comparison_label = "7D"
    comparable = [
        row for row in rows
        if row["history"][comparison_label]["delta_value"] is not None
    ]
    if not comparable:
        comparison_label = "1D"
        comparable = [
            row for row in rows
            if row["history"][comparison_label]["delta_value"] is not None
        ]

    top_risers = sorted(
        comparable,
        key=lambda row: row["history"][comparison_label]["delta_value"],
        reverse=True,
    )[:8]
    top_fallers = sorted(
        comparable,
        key=lambda row: row["history"][comparison_label]["delta_value"],
    )[:8]

    return {
        "rows": rows,
        "player_count": len(rows),
        "ranked_count": sum(
            1 for snapshot in snapshots if snapshot.value_rank is not None
        ),
        "best_reliable": by_reliable[:10],
        "best_forward": by_forward[:10],
        "low_rotation": low_rotation[:10],
        "position_averages": position_averages,
        "latest_run": latest_run(db),
        "latest_time": latest_snapshot_time(db),
        "schema_changes": recent_schema_changes(db, 10),
        "comparison_label": comparison_label,
        "top_risers": top_risers,
        "top_fallers": top_fallers,
    }
