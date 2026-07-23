from __future__ import annotations

import csv
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.metrics import safe_float, safe_int
from app.db.models import ImportRecord, Player, PlayerSnapshot, RefreshRun


def _value(row: dict[str, Any], *names: str, default: Any = None) -> Any:
    normalize = lambda value: re.sub(r"[^a-z0-9]", "", str(value).casefold())
    normalized = {normalize(key): value for key, value in row.items()}
    for name in names:
        if normalize(name) in normalized:
            return normalized[normalize(name)]
    return default


def _timestamp(row: dict[str, Any], path: Path) -> datetime:
    raw = _value(row, "captured_at", "timestamp", "date", "snapshot_date")
    if raw:
        try:
            return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)


def import_history_directory(db: Session, directory: str | Path) -> dict[str, int]:
    root = Path(directory)
    if not root.is_dir():
        raise FileNotFoundError(f"History directory does not exist: {root}")
    result = {"files": 0, "rows": 0, "imported": 0, "skipped": 0}
    for path in sorted(root.glob("*.csv")):
        result["files"] += 1
        source = str(path.resolve())
        if db.scalar(select(ImportRecord).where(ImportRecord.source_path == source)):
            continue
        started = datetime.now(timezone.utc)
        run = RefreshRun(started_at=started, status="success", completed_at=started, details={"source": source})
        db.add(run)
        db.flush()
        imported = skipped = 0
        with path.open("r", encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle)
            for row in reader:
                result["rows"] += 1
                player_id = safe_int(_value(row, "player_id", "player id", "element_id", "element id", "id"), 0)
                player = db.get(Player, player_id)
                if not player or not player_id:
                    skipped += 1
                    continue
                captured = _timestamp(row, path)
                duplicate = db.scalar(select(PlayerSnapshot).where(
                    PlayerSnapshot.player_id == player_id,
                    PlayerSnapshot.captured_at == captured,
                ))
                if duplicate:
                    continue
                price = safe_float(_value(row, "price", "now_cost"))
                if price > 20:
                    price /= 10
                points = safe_int(_value(row, "total_points", "points"))
                snapshot = PlayerSnapshot(
                    player_id=player_id, refresh_run_id=run.id, captured_at=captured,
                    price=price, total_points=points,
                    minutes=safe_int(_value(row, "minutes")), starts=safe_int(_value(row, "starts")),
                    team_matches=safe_int(_value(row, "team_matches", "matches")),
                    form=safe_float(_value(row, "form")), points_per_game=safe_float(_value(row, "points_per_game", "ppg")),
                    ownership=safe_float(_value(row, "ownership", "selected_by_percent")),
                    value=safe_float(_value(row, "value", "raw_value")),
                    reliable_value=safe_float(_value(row, "reliable_value")),
                    forward_value=safe_float(_value(row, "forward_value")),
                    rotation_risk=safe_float(_value(row, "rotation_risk"), None),
                    raw=dict(row),
                )
                db.add(snapshot)
                imported += 1
        db.add(ImportRecord(source_path=source, imported_at=datetime.now(timezone.utc), status="success", row_count=imported, skipped_count=skipped, details={"unsupported_columns_preserved": True}))
        result["imported"] += imported
        result["skipped"] += skipped
    db.commit()
    return result
