from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from threading import Lock
import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.metrics import (
    assign_global_ranks,
    assign_position_ranks,
    availability_factor,
    expected_minutes,
    project_next_fixtures,
    reliability_factor,
    rotation_risk,
    safe_float,
    safe_int,
)
from app.api.fpl_client import FPLClient
from app.config import Settings
from app.db.models import (
    Fixture,
    GameweekHistory,
    Player,
    PlayerSnapshot,
    RefreshRun,
    SchemaChange,
    SchemaField,
    Team,
)


_refresh_lock = Lock()
logger = logging.getLogger(__name__)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None


def _schema_sets(
    bootstrap: dict[str, Any],
    fixtures: list[dict[str, Any]],
) -> dict[str, set[str]]:
    def keys(records: Any) -> set[str]:
        if not isinstance(records, list):
            return set()
        result: set[str] = set()
        for record in records:
            if isinstance(record, dict):
                result.update(str(key) for key in record)
        return result

    return {
        "bootstrap": set(str(key) for key in bootstrap),
        "players": keys(bootstrap.get("elements")),
        "teams": keys(bootstrap.get("teams")),
        "gameweeks": keys(bootstrap.get("events")),
        "positions": keys(bootstrap.get("element_types")),
        "fixtures": keys(fixtures),
    }


def _update_schema(
    db: Session,
    schema: dict[str, set[str]],
    captured_at: datetime,
) -> int:
    existing = {
        (field.category, field.field_name): field
        for field in db.scalars(select(SchemaField)).all()
    }
    change_count = 0

    current_pairs = {
        (category, field)
        for category, fields in schema.items()
        for field in fields
    }

    for category, field_name in sorted(current_pairs):
        row = existing.get((category, field_name))
        if row is None:
            db.add(
                SchemaField(
                    category=category,
                    field_name=field_name,
                    first_seen=captured_at,
                    last_seen=captured_at,
                    active=True,
                )
            )
            db.add(
                SchemaChange(
                    detected_at=captured_at,
                    category=category,
                    change_type="Added",
                    field_name=field_name,
                )
            )
            change_count += 1
        else:
            if not row.active:
                db.add(
                    SchemaChange(
                        detected_at=captured_at,
                        category=category,
                        change_type="Added",
                        field_name=field_name,
                    )
                )
                change_count += 1
            row.active = True
            row.last_seen = captured_at

    for pair, row in existing.items():
        if pair not in current_pairs and row.active:
            row.active = False
            db.add(
                SchemaChange(
                    detected_at=captured_at,
                    category=row.category,
                    change_type="Removed",
                    field_name=row.field_name,
                )
            )
            change_count += 1

    return change_count


def _previous_snapshot(
    db: Session,
    player_id: int,
    captured_at: datetime,
) -> PlayerSnapshot | None:
    target = captured_at - timedelta(days=7)
    return db.scalar(
        select(PlayerSnapshot)
        .where(
            PlayerSnapshot.player_id == player_id,
            PlayerSnapshot.captured_at <= target,
        )
        .order_by(PlayerSnapshot.captured_at.desc())
        .limit(1)
    )


def refresh_data(
    db: Session,
    settings: Settings,
    client: FPLClient | None = None,
) -> RefreshRun:
    if not _refresh_lock.acquire(blocking=False):
        raise RuntimeError("A refresh is already in progress")
    database_lock = False
    if db.bind is not None and db.bind.dialect.name == "postgresql":
        try:
            database_lock = bool(db.scalar(text("SELECT pg_try_advisory_lock(731942)")))
        except Exception:
            _refresh_lock.release()
            raise
        if not database_lock:
            _refresh_lock.release()
            raise RuntimeError("A refresh is already in progress")
    client = client or FPLClient(settings)
    started_at = utcnow()
    logger.info("refresh_start started_at=%s", started_at.isoformat())

    active_run = db.scalar(
        select(RefreshRun)
        .where(
            RefreshRun.status == "running",
            RefreshRun.started_at >= started_at - timedelta(hours=2),
        )
        .order_by(RefreshRun.started_at.desc())
        .limit(1)
    )
    if active_run:
        if database_lock:
            db.execute(text("SELECT pg_advisory_unlock(731942)"))
        _refresh_lock.release()
        raise RuntimeError("A refresh is already in progress")

    run = RefreshRun(
        started_at=started_at,
        status="running",
        details={},
    )
    db.add(run)
    db.commit()
    db.refresh(run)

    try:
        bootstrap = client.bootstrap()
        fixtures_payload = client.fixtures()
        captured_at = utcnow()
        logger.info("refresh_api_payload players=%s fixtures=%s", len(bootstrap.get("elements", [])), len(fixtures_payload))

        schema_change_count = _update_schema(
            db,
            _schema_sets(bootstrap, fixtures_payload),
            captured_at,
        )
        logger.info("refresh_schema_changes count=%s", schema_change_count)

        teams_payload = [
            item for item in bootstrap.get("teams", [])
            if isinstance(item, dict)
        ]
        positions = {
            safe_int(item.get("id")): item
            for item in bootstrap.get("element_types", [])
            if isinstance(item, dict)
        }

        for item in teams_payload:
            team_id = safe_int(item.get("id"))
            team = db.get(Team, team_id)
            if team is None:
                team = Team(
                    id=team_id,
                    name=str(item.get("name") or "Unknown"),
                    short_name=str(item.get("short_name") or ""),
                    updated_at=captured_at,
                )
                db.add(team)
            else:
                team.name = str(item.get("name") or team.name)
                team.short_name = str(
                    item.get("short_name") or team.short_name
                )
                team.updated_at = captured_at

        db.flush()

        for item in fixtures_payload:
            fixture_id = safe_int(item.get("id"))
            fixture = db.get(Fixture, fixture_id)
            values = {
                "event": item.get("event"),
                "kickoff_time": _parse_datetime(item.get("kickoff_time")),
                "team_h": safe_int(item.get("team_h")),
                "team_a": safe_int(item.get("team_a")),
                "team_h_difficulty": safe_int(
                    item.get("team_h_difficulty"), 3
                ),
                "team_a_difficulty": safe_int(
                    item.get("team_a_difficulty"), 3
                ),
                "finished": bool(item.get("finished")),
                "raw": item,
            }
            if fixture is None:
                db.add(Fixture(id=fixture_id, **values))
            else:
                for key, value in values.items():
                    setattr(fixture, key, value)

        db.flush()

        team_matches: dict[int, int] = defaultdict(int)
        upcoming: dict[int, list[dict[str, Any]]] = defaultdict(list)
        team_names = {
            safe_int(item.get("id")): str(item.get("short_name") or "")
            for item in teams_payload
        }

        sorted_fixtures = sorted(
            fixtures_payload,
            key=lambda item: (
                item.get("event") is None,
                safe_int(item.get("event"), 999),
                str(item.get("kickoff_time") or ""),
            ),
        )
        for fixture in sorted_fixtures:
            home = safe_int(fixture.get("team_h"))
            away = safe_int(fixture.get("team_a"))
            if fixture.get("finished"):
                team_matches[home] += 1
                team_matches[away] += 1
            elif fixture.get("event") is not None:
                upcoming[home].append(
                    {
                        "event": fixture.get("event"),
                        "opponent": team_names.get(away, str(away)),
                        "difficulty": safe_int(
                            fixture.get("team_h_difficulty"), 3
                        ),
                        "is_home": True,
                    }
                )
                upcoming[away].append(
                    {
                        "event": fixture.get("event"),
                        "opponent": team_names.get(home, str(home)),
                        "difficulty": safe_int(
                            fixture.get("team_a_difficulty"), 3
                        ),
                        "is_home": False,
                    }
                )

        players_payload = [
            item for item in bootstrap.get("elements", [])
            if isinstance(item, dict)
        ]
        computed: list[dict[str, Any]] = []

        for item in players_payload:
            player_id = safe_int(item.get("id"))
            team_id = safe_int(item.get("team"))
            position_data = positions.get(
                safe_int(item.get("element_type")), {}
            )
            position = str(
                position_data.get("singular_name") or "Unknown"
            )
            position_short = str(
                position_data.get("singular_name_short") or "UNK"
            )

            player = db.get(Player, player_id)
            if player is None:
                player = Player(
                    id=player_id,
                    first_name=str(item.get("first_name") or ""),
                    second_name=str(item.get("second_name") or ""),
                    web_name=str(item.get("web_name") or ""),
                    team_id=team_id,
                    position=position,
                    position_short=position_short,
                    status=str(item.get("status") or "a"),
                    news=str(item.get("news") or ""),
                    updated_at=captured_at,
                    raw=item,
                )
                db.add(player)
            else:
                player.first_name = str(item.get("first_name") or "")
                player.second_name = str(item.get("second_name") or "")
                player.web_name = str(item.get("web_name") or "")
                player.team_id = team_id
                player.position = position
                player.position_short = position_short
                player.status = str(item.get("status") or "a")
                player.news = str(item.get("news") or "")
                player.updated_at = captured_at
                player.raw = item

            price = safe_float(item.get("now_cost")) / 10.0
            points = safe_int(item.get("total_points"))
            minutes = safe_int(item.get("minutes"))
            starts = safe_int(item.get("starts"))
            matches = team_matches.get(team_id, 0)

            value = points / price if price > 0 and points > 0 else 0.0
            p90 = points * 90.0 / minutes if minutes > 0 else 0.0
            ppm = points / minutes if minutes > 0 else 0.0
            pps = points / starts if starts > 0 else 0.0
            pptm = points / matches if matches > 0 else 0.0
            start_rate = 100.0 * starts / matches if matches > 0 else 0.0
            mptm = minutes / matches if matches > 0 else 0.0
            value_p90 = p90 / price if price > 0 else 0.0

            reliable_factor = reliability_factor(
                minutes, starts, matches, settings.reliability_sample_minutes
            )
            reliable_value = value * reliable_factor

            old = _previous_snapshot(db, player_id, captured_at)
            old_data = (
                {
                    "minutes": old.minutes,
                    "starts": old.starts,
                    "team_matches": old.team_matches,
                }
                if old else None
            )
            risk, risk_tier, risk_confidence = rotation_risk(
                minutes,
                starts,
                matches,
                old_data,
                settings.rotation_season_start_weight,
                settings.rotation_season_minutes_weight,
                settings.rotation_recent_start_weight,
                settings.rotation_recent_minutes_weight,
            )

            recent_matches = recent_starts = recent_minutes = 0
            historical_reference = old.captured_at if old else None
            if old and matches > old.team_matches and starts >= old.starts and minutes >= old.minutes:
                recent_matches = matches - old.team_matches
                recent_starts = starts - old.starts
                recent_minutes = minutes - old.minutes

            availability = availability_factor(item)
            exp_minutes = expected_minutes(
                minutes, starts, matches, availability
            )
            next_fixtures = upcoming.get(
                team_id, []
            )[: settings.forward_fixture_count]
            average_difficulty = (
                round(sum(safe_float(item.get("difficulty"), 3.0) for item in next_fixtures) / len(next_fixtures), 2)
                if next_fixtures else 0.0
            )
            form = safe_float(item.get("form"))
            ppg = safe_float(item.get("points_per_game"))
            projected = project_next_fixtures(
                form=form,
                points_per_game=ppg,
                points_per_90=p90,
                expected_minutes_value=exp_minutes,
                availability=availability,
                fixtures=next_fixtures,
            )
            forward_value = (
                projected / price if price > 0 else 0.0
            )

            computed.append(
                {
                    "player": player,
                    "player_id": player_id,
                    "player_name": player.full_name,
                    "position_short": position_short,
                    "price": round(price, 1),
                    "total_points": points,
                    "minutes": minutes,
                    "starts": starts,
                    "team_matches": matches,
                    "goals": safe_int(item.get("goals_scored")),
                    "assists": safe_int(item.get("assists")),
                    "clean_sheets": safe_int(item.get("clean_sheets")),
                    "bonus": safe_int(item.get("bonus")),
                    "bps": safe_int(item.get("bps")),
                    "form": form,
                    "points_per_game": ppg,
                    "points_per_minute": round(ppm, 6),
                    "points_per_90": round(p90, 3),
                    "points_per_start": round(pps, 3),
                    "points_per_team_match": round(pptm, 3),
                    "value_per_90": round(value_p90, 3),
                    "start_rate": round(start_rate, 1),
                    "minutes_per_team_match": round(mptm, 2),
                    "average_minutes_per_start": round(minutes / starts, 2) if starts > 0 else 0.0,
                    "expected_goals": safe_float(
                        item.get("expected_goals")
                    ),
                    "expected_assists": safe_float(
                        item.get("expected_assists")
                    ),
                    "expected_goal_involvements": safe_float(
                        item.get("expected_goal_involvements")
                    ),
                    "ict_index": safe_float(item.get("ict_index")),
                    "ownership": safe_float(
                        item.get("selected_by_percent")
                    ),
                    "value": round(value, 3),
                    "reliability_factor": reliable_factor,
                    "reliable_value": round(reliable_value, 3),
                    "rotation_risk": risk,
                    "rotation_tier": risk_tier,
                    "rotation_confidence": risk_confidence,
                    "recent_team_matches": recent_matches,
                    "recent_starts": recent_starts,
                    "recent_minutes": recent_minutes,
                    "historical_reference_at": historical_reference,
                    "availability_factor": availability,
                    "availability_status": str(item.get("status") or "a"),
                    "chance_of_playing": safe_float(item.get("chance_of_playing_next_round"), None),
                    "expected_minutes": exp_minutes,
                    "projected_points_5": projected,
                    "upcoming_fixture_count": len(next_fixtures),
                    "average_fixture_difficulty": average_difficulty,
                    "forward_value": round(forward_value, 3),
                    "upcoming_fixtures": next_fixtures,
                    "raw": item,
                }
            )

        if settings.collect_gameweek_history and hasattr(client, "player_history"):
            for item in players_payload:
                player_id = safe_int(item.get("id"))
                history_payload = client.player_history(player_id)
                for record in history_payload.get("history", []):
                    if not isinstance(record, dict) or safe_int(record.get("round"), 0) <= 0:
                        continue
                    gameweek = safe_int(record.get("round"))
                    existing = db.scalar(select(GameweekHistory).where(
                        GameweekHistory.player_id == player_id,
                        GameweekHistory.gameweek == gameweek,
                    ))
                    values = {
                        "opponent": str(record.get("opponent_team") or ""),
                        "is_home": bool(record.get("was_home")),
                        "minutes": safe_int(record.get("minutes")),
                        "started": safe_int(record.get("minutes")) >= 60,
                        "points": safe_int(record.get("total_points")),
                        "goals": safe_int(record.get("goals_scored")),
                        "assists": safe_int(record.get("assists")),
                        "clean_sheets": safe_int(record.get("clean_sheets")),
                        "bonus": safe_int(record.get("bonus")),
                        "expected_goals": safe_float(record.get("expected_goals")),
                        "expected_assists": safe_float(record.get("expected_assists")),
                        "price": safe_float(record.get("value")) / 10.0,
                        "ownership": safe_float(record.get("selected")),
                        "captured_at": captured_at,
                        "raw": record,
                    }
                    if existing is None:
                        db.add(GameweekHistory(player_id=player_id, gameweek=gameweek, **values))
                    else:
                        for key, value in values.items():
                            setattr(existing, key, value)

        assign_global_ranks(
            computed,
            "value",
            "value_rank",
            "value_percentile",
            "value_tier",
        )
        assign_global_ranks(
            computed,
            "reliable_value",
            "reliable_rank",
            "reliable_percentile",
            "reliable_tier",
        )
        assign_position_ranks(
            computed,
            "reliable_value",
            "position_reliable_rank",
            "position_reliable_percentile",
            "position_reliable_tier",
        )
        assign_global_ranks(
            computed,
            "forward_value",
            "forward_rank",
            "forward_percentile",
            "forward_tier",
        )
        assign_position_ranks(
            computed,
            "forward_value",
            "position_forward_rank",
            "position_forward_percentile",
            "position_forward_tier",
        )

        for row in computed:
            player = row.pop("player")
            row.pop("player_id", None)
            row.pop("player_name", None)
            row.pop("position_short", None)
            db.add(
                PlayerSnapshot(
                    player_id=player.id,
                    refresh_run_id=run.id,
                    captured_at=captured_at,
                    **row,
                )
            )

        run.status = "success"
        run.completed_at = utcnow()
        run.player_count = len(computed)
        run.schema_change_count = schema_change_count
        run.details = {
            "captured_at": captured_at.isoformat(),
            "fixture_count": len(fixtures_payload),
        }
        db.commit()
        db.refresh(run)
        logger.info("refresh_complete run_id=%s players=%s", run.id, run.player_count)
        return run

    except Exception as exc:
        db.rollback()
        failed_run = db.get(RefreshRun, run.id)
        if failed_run:
            failed_run.status = "failed"
            failed_run.completed_at = utcnow()
            failed_run.error = str(exc)
            db.commit()
        logger.exception("refresh_failed run_id=%s", run.id)
        raise
    finally:
        if database_lock:
            try:
                db.execute(text("SELECT pg_advisory_unlock(731942)"))
            except Exception:
                logger.exception("refresh_lock_release_failed")
        _refresh_lock.release()
