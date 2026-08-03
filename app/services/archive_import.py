"""Ingest the FPL community archive into gameweek_history.

Source: https://github.com/vaastav/Fantasy-Premier-League (MIT licensed).

Two things this module is careful about:

1. **Season.** Rows are stored against the season they describe, never the
   current one. A season label is what stops a previous-season row being
   differenced against this one.
2. **Identity.** FPL re-assigns element ids every season, so the archive's
   ``element`` column cannot be joined to a player directly. Each season ships a
   ``players_raw.csv`` mapping that season's element ids to the stable FPL
   player ``code``, and that code is what resolves to a player here.
"""

from __future__ import annotations

import csv
import io
import logging
from pathlib import Path
from typing import Iterator, Protocol

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory, Player
from app.services.archive_schema import (
    ArchiveRow,
    detect_era,
    normalise_row,
    season_label,
)

logger = logging.getLogger(__name__)

ARCHIVE_BASE = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master/data"
)
SEASONS = (
    "2016-17", "2017-18", "2018-19", "2019-20", "2020-21",
    "2021-22", "2022-23", "2023-24", "2024-25", "2025-26",
)


class ArchiveReader(Protocol):
    def read_gameweeks(self, season_directory: str) -> Iterator[dict[str, str]]: ...
    def read_players(self, season_directory: str) -> Iterator[dict[str, str]]: ...


def _rows_from_csv(text: str) -> Iterator[dict[str, str]]:
    for row in csv.DictReader(io.StringIO(text)):
        yield {
            str(key).strip().strip('"'): value
            for key, value in row.items()
            if key is not None
        }


class HttpArchiveReader:
    def __init__(self, base_url: str = ARCHIVE_BASE, timeout: float = 180.0):
        self.base_url = base_url
        self.timeout = timeout

    def _fetch(self, path: str) -> str:
        with httpx.Client(timeout=self.timeout, follow_redirects=True) as client:
            response = client.get(f"{self.base_url}/{path}")
            response.raise_for_status()
            return response.content.decode("utf-8", "replace")

    def read_gameweeks(self, season_directory: str) -> Iterator[dict[str, str]]:
        yield from _rows_from_csv(
            self._fetch(f"{season_directory}/gws/merged_gw.csv")
        )

    def read_players(self, season_directory: str) -> Iterator[dict[str, str]]:
        yield from _rows_from_csv(
            self._fetch(f"{season_directory}/players_raw.csv")
        )


class LocalArchiveReader:
    def __init__(self, root: str | Path):
        self.root = Path(root)

    def _read(self, relative: str) -> Iterator[dict[str, str]]:
        path = self.root / relative
        with path.open("r", encoding="utf-8", errors="replace", newline="") as handle:
            yield from _rows_from_csv(handle.read())

    def read_gameweeks(self, season_directory: str) -> Iterator[dict[str, str]]:
        yield from self._read(f"{season_directory}/gws/merged_gw.csv")

    def read_players(self, season_directory: str) -> Iterator[dict[str, str]]:
        yield from self._read(f"{season_directory}/players_raw.csv")


def _element_to_code(reader: ArchiveReader, season_directory: str) -> dict[int, int]:
    """That season's element id -> stable FPL player code."""
    mapping: dict[int, int] = {}
    for row in reader.read_players(season_directory):
        try:
            element = int(str(row.get("id", "")).strip())
            code = int(str(row.get("code", "")).strip())
        except (TypeError, ValueError):
            continue
        mapping[element] = code
    return mapping


def _to_model(row: ArchiveRow) -> dict:
    return {
        "season": row.season,
        "gameweek": row.gameweek,
        "source": "archive",
        "opponent": str(row.opponent_team_id or ""),
        "opponent_team_id": row.opponent_team_id,
        "is_home": row.is_home,
        "fixture_id": row.fixture_id,
        "kickoff_time": row.kickoff_time,
        "position": row.position,
        "team_name": row.team_name,
        "minutes": row.minutes,
        "started": row.started,
        "started_is_derived": row.started_is_derived,
        "starts": row.starts,
        "points": row.total_points,
        "goals": row.goals,
        "assists": row.assists,
        "clean_sheets": row.clean_sheets,
        "goals_conceded": row.goals_conceded,
        "saves": row.saves,
        "bonus": row.bonus or 0,
        "bps": row.bps,
        "yellow_cards": row.yellow_cards,
        "red_cards": row.red_cards,
        "own_goals": row.own_goals,
        "penalties_missed": row.penalties_missed,
        "penalties_saved": row.penalties_saved,
        "expected_goals": row.expected_goals or 0.0,
        "expected_assists": row.expected_assists or 0.0,
        "expected_goal_involvements": row.expected_goal_involvements,
        "expected_goals_conceded": row.expected_goals_conceded,
        "influence": row.influence,
        "creativity": row.creativity,
        "threat": row.threat,
        "price": row.price or 0.0,
        "ownership": 0.0,
        "selected": row.selected,
        "transfers_in": row.transfers_in,
        "transfers_out": row.transfers_out,
        "transfers_balance": row.transfers_balance,
    }


def import_archive_season(
    db: Session,
    season_directory: str,
    reader: ArchiveReader,
) -> dict[str, int | str]:
    """Import one archive season. Idempotent on (player, season, gameweek)."""
    season = season_label(season_directory)

    code_to_player = {
        code: player_id
        for player_id, code in db.execute(select(Player.id, Player.code))
        if code is not None
    }
    element_to_code = _element_to_code(reader, season_directory)
    # Keyed on fixture too: a double gameweek gives a player two fixtures in
    # one gameweek, and both are real observations.
    existing = {
        (code, gameweek, fixture)
        for code, gameweek, fixture in db.execute(
            select(
                GameweekHistory.player_code,
                GameweekHistory.gameweek,
                GameweekHistory.fixture_id,
            ).where(GameweekHistory.season == season)
        )
    }

    rows_read = rows_written = rows_rejected = derived = departed = 0
    era = "unknown"
    fallback_time = None

    for raw in reader.read_gameweeks(season_directory):
        rows_read += 1
        if era == "unknown":
            era = detect_era(set(raw))

        parsed = normalise_row(raw, season)
        if parsed is None:
            rows_rejected += 1
            continue

        code = element_to_code.get(parsed.player_element)
        if code is None:
            # Without a code the row cannot be attributed to a player across
            # seasons, so it is not safe to train on.
            rows_rejected += 1
            continue
        # Departed players have no current element id. They are kept: excluding
        # them would train the model only on careers that survived to today.
        player_id = code_to_player.get(code)

        key = (code, parsed.gameweek, parsed.fixture_id)
        if key in existing:
            continue

        if fallback_time is None:
            fallback_time = parsed.kickoff_time
        captured = parsed.kickoff_time or fallback_time
        if captured is None:
            rows_rejected += 1
            continue

        if parsed.started_is_derived:
            derived += 1

        db.add(
            GameweekHistory(
                player_code=code,
                player_id=player_id,
                captured_at=captured,
                raw={},
                **_to_model(parsed),
            )
        )
        existing.add(key)
        if player_id is None:
            departed += 1
        rows_written += 1

    db.commit()
    logger.info(
        "archive_import season=%s era=%s read=%s written=%s rejected=%s "
        "derived=%s departed=%s",
        season, era, rows_read, rows_written, rows_rejected, derived, departed,
    )
    return {
        "season": season,
        "era": era,
        "rows_read": rows_read,
        "rows_written": rows_written,
        "rows_rejected": rows_rejected,
        "derived_starts": derived,
        "departed_players": departed,
    }
