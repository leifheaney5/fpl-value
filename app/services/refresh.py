from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any
from threading import Lock
import logging

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.analytics.contracts import CONTRACTS, MetricStatus
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
    Gameweek,
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
) -> dict[str, Any]:
    """Record the observed API schema and report what changed.

    The first observation is a baseline, not a change. Treating it as one
    produced hundreds of "Added" rows on a new database and buried every real
    change that followed.
    """
    existing = {
        (field.category, field.field_name): field
        for field in db.scalars(select(SchemaField)).all()
    }
    is_baseline = not existing
    added = 0
    removed = 0

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
                    change_type="Baseline" if is_baseline else "Added",
                    field_name=field_name,
                )
            )
            if not is_baseline:
                added += 1
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
                added += 1
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
            removed += 1

    return {
        "baseline": is_baseline,
        "added": added,
        "removed": removed,
        "fields": len(current_pairs),
    }


def _status(
    value: float | None,
    metric: str,
    reason_when_null: str,
    has_sample: bool,
    previous_season: bool = False,
) -> dict[str, str]:
    """Record why a metric holds its value.

    A stored 0.0 is only a measurement when there was something to measure;
    otherwise it is an absence that happens to look like a number.

    ``previous_season`` marks a real measurement that describes the season just
    finished. The FPL API keeps serving last season's counting stats until the
    new season starts, so these are genuine numbers about the wrong season --
    useful, but only if the interface says so.
    """
    if value is not None and previous_season:
        return {
            "status": MetricStatus.PREVIOUS_SEASON,
            "reason": "From last season; no match has been played in this one",
        }
    if value is None:
        contract = CONTRACTS.get(metric)
        return {
            "status": MetricStatus.NOT_YET_AVAILABLE,
            "reason": reason_when_null
            or (contract.null_behaviour if contract else "Inputs unavailable"),
        }
    if value == 0.0:
        return {
            "status": MetricStatus.REAL_ZERO if has_sample else MetricStatus.MISSING,
            "reason": "Measured as zero" if has_sample else reason_when_null,
        }
    return {"status": MetricStatus.VALUE, "reason": ""}


def _round(value: float | None, digits: int) -> float | None:
    return None if value is None else round(value, digits)


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

        schema_result = _update_schema(
            db,
            _schema_sets(bootstrap, fixtures_payload),
            captured_at,
        )
        schema_change_count = schema_result["added"] + schema_result["removed"]
        logger.info(
            "refresh_schema baseline=%s added=%s removed=%s fields=%s",
            schema_result["baseline"],
            schema_result["added"],
            schema_result["removed"],
            schema_result["fields"],
        )

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

        # Persist the season calendar. These records were previously parsed only
        # for schema detection and discarded, leaving the application unable to
        # name the current gameweek or the next deadline.
        for item in bootstrap.get("events", []):
            if not isinstance(item, dict):
                continue
            number = safe_int(item.get("id"))
            if number <= 0:
                continue
            gameweek = db.scalar(
                select(Gameweek).where(
                    Gameweek.season == settings.current_season,
                    Gameweek.number == number,
                )
            )
            values = {
                "name": str(item.get("name") or f"Gameweek {number}"),
                "deadline_time": _parse_datetime(item.get("deadline_time")),
                "finished": bool(item.get("finished")),
                "data_checked": bool(item.get("data_checked")),
                "is_current": bool(item.get("is_current")),
                "is_next": bool(item.get("is_next")),
                "raw": item,
            }
            if gameweek is None:
                db.add(
                    Gameweek(
                        season=settings.current_season, number=number, **values
                    )
                )
            else:
                for key, value in values.items():
                    setattr(gameweek, key, value)

        db.flush()

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
                    code=safe_int(item.get("code")) or None,
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
                player.code = safe_int(item.get("code")) or None
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

            has_matches = matches > 0
            # Minutes only count as this season's once a match has been played.
            # The preseason bootstrap still reports the previous season's
            # minutes and points, so a rate built from them would describe a
            # season that has ended while being labelled as this one.
            has_minutes = matches > 0 and minutes > 0
            has_starts = matches > 0 and starts > 0
            no_matches_reason = "No matches played yet this season"
            no_minutes_reason = "No minutes played yet this season"

            # Carry-over: no fixture has been played this season, yet the API
            # still reports last season's counting stats. These rates used to be
            # suppressed entirely, on the reasoning that they would describe a
            # finished season while being labelled as this one. The second half
            # of that was the real objection, and it is a presentation problem:
            # the numbers themselves are sound measurements of last season.
            #
            # They are now computed and carry MetricStatus.PREVIOUS_SEASON, and
            # the interface names the season they describe. The line is drawn on
            # the denominator, not on convenience:
            #
            #   computable from carry-over alone -> value, p90, ppm, pps
            #   needs a this-season quantity     -> start_rate, pptm, everything
            #                                       derived from team_matches
            #
            # Making only `value` an exception would have reproduced the exact
            # inconsistency that exposed the recommender bug: one rate present
            # and its neighbours absent, with no principle separating them.
            carry_over = not has_matches and (points > 0 or minutes > 0)
            has_carry_minutes = carry_over and minutes > 0
            has_carry_starts = carry_over and starts > 0

            value = (
                points / price if price > 0 and (has_matches or carry_over) else None
            )
            p90 = (
                points * 90.0 / minutes
                if has_minutes or has_carry_minutes
                else None
            )
            ppm = points / minutes if has_minutes or has_carry_minutes else None
            pps = points / starts if has_starts or has_carry_starts else None
            # team_matches is zero in preseason and the API does not report last
            # season's, so these have no denominator in any form.
            pptm = points / matches if has_matches else None
            start_rate = 100.0 * starts / matches if has_matches else None
            mptm = minutes / matches if has_matches else None
            value_p90 = p90 / price if p90 is not None and price > 0 else None

            reliable_factor = reliability_factor(
                minutes, starts, matches, settings.reliability_sample_minutes
            )
            reliable_value = (
                value * reliable_factor
                if value is not None and reliable_factor is not None
                else None
            )

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
                if next_fixtures else None
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
                form_weight=settings.forward_form_weight,
                ppg_weight=settings.forward_ppg_weight,
                p90_weight=settings.forward_p90_weight,
                difficulty_weight=settings.fixture_difficulty_weight,
                home_advantage_factor=settings.home_advantage_factor,
            )
            forward_value = (
                projected / price if projected is not None and price > 0 else None
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
                    "points_per_minute": _round(ppm, 6),
                    "points_per_90": _round(p90, 3),
                    "points_per_start": _round(pps, 3),
                    "points_per_team_match": _round(pptm, 3),
                    "value_per_90": _round(value_p90, 3),
                    "start_rate": _round(start_rate, 1),
                    "minutes_per_team_match": _round(mptm, 2),
                    "average_minutes_per_start": round(minutes / starts, 2) if has_starts or has_carry_starts else None,
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
                    "value": _round(value, 3),
                    "reliability_factor": reliable_factor,
                    "reliable_value": _round(reliable_value, 3),
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
                    "forward_value": _round(forward_value, 3),
                    "upcoming_fixtures": next_fixtures,
                    "season": settings.current_season,
                    "metric_status": {
                        "value": _status(
                            value, "value", no_matches_reason,
                            has_matches or carry_over, previous_season=carry_over,
                        ),
                        "reliability_factor": _status(
                            reliable_factor, "reliability_factor", no_matches_reason, has_matches
                        ),
                        "reliable_value": _status(
                            reliable_value, "reliable_value", no_matches_reason, has_matches
                        ),
                        "start_rate": _status(
                            start_rate, "start_rate", no_matches_reason, has_matches
                        ),
                        "points_per_90": _status(
                            p90,
                            "points_per_90",
                            no_matches_reason if not has_matches else no_minutes_reason,
                            has_minutes,
                            previous_season=carry_over,
                        ),
                        "expected_minutes": _status(
                            exp_minutes, "expected_minutes", no_matches_reason, has_matches
                        ),
                        "projected_points_5": _status(
                            projected,
                            "projected_points_5",
                            "No upcoming fixtures or no expected-minutes estimate",
                            bool(next_fixtures) and exp_minutes is not None,
                        ),
                        "forward_value": _status(
                            forward_value,
                            "forward_value",
                            "No projection available",
                            bool(next_fixtures) and exp_minutes is not None,
                        ),
                        "rotation_risk": _status(
                            risk, "rotation_risk", no_matches_reason, has_matches
                        ),
                    },
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

        value_exclusions = assign_global_ranks(
            computed,
            "value",
            "value_rank",
            "value_percentile",
            "value_tier",
        )
        assign_position_ranks(
            computed,
            "value",
            "position_value_rank",
            "position_value_percentile",
            "position_value_tier",
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
            "season": settings.current_season,
            "ranked_count": sum(
                1 for row in computed if row.get("value_rank") is not None
            ),
            "ranking_exclusions": value_exclusions,
            "schema_baseline": schema_result["baseline"],
            "schema_fields": schema_result["fields"],
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
