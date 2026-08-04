"""Materialise point-in-time features into aligned arrays.

Features are built once per information state and then sliced by season, rather
than rebuilt per fold. That is not a micro-optimisation: rebuilding per fold took
roughly twenty minutes for the baseline run alone, which would make repeated
training passes impractical.

Every array in a ``Dataset`` is index-aligned with every other. ``slice_seasons``
filters all of them through the same index set, so alignment cannot drift.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from typing import Any, Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import GameweekHistory
from app.models.features import (
    FEATURE_NAMES,
    InformationState,
    build_features,
)
from app.models.opponent import (
    TeamStrength,
    build_fixture_opponents,
    build_strength_by_gameweek,
)

logger = logging.getLogger(__name__)


@dataclass
class Dataset:
    x: list[list[float]]
    mask: list[list[float]]
    y: list[float]
    minutes: list[float]
    started: list[bool]
    season: list[str]
    gameweek: list[int]
    player_code: list[int]
    information_state: str
    feature_names: tuple[str, ...]

    def __len__(self) -> int:
        return len(self.y)

    def slice_seasons(self, seasons: Sequence[str]) -> "Dataset":
        wanted = set(seasons)
        keep = [i for i, season in enumerate(self.season) if season in wanted]
        return Dataset(
            x=[self.x[i] for i in keep],
            mask=[self.mask[i] for i in keep],
            y=[self.y[i] for i in keep],
            minutes=[self.minutes[i] for i in keep],
            started=[self.started[i] for i in keep],
            season=[self.season[i] for i in keep],
            gameweek=[self.gameweek[i] for i in keep],
            player_code=[self.player_code[i] for i in keep],
            information_state=self.information_state,
            feature_names=self.feature_names,
        )


class _Target:
    """The fixture being predicted, described only by what is knowable first."""

    __slots__ = (
        "season", "gameweek", "is_home", "difficulty",
        "opponent_attack", "opponent_defence", "opponent_matches",
    )

    def __init__(
        self,
        row: Any,
        opponent: TeamStrength | None = None,
    ) -> None:
        self.season = row.season
        self.gameweek = row.gameweek
        self.is_home = bool(row.is_home)
        # The archive carries no fixture difficulty rating, so 3 (neutral) is
        # used throughout. Opponent strength below is the derived replacement.
        self.difficulty = 3
        # None where the opponent cannot be identified (no team names before
        # 2021-22) or has not yet played. Masked rather than defaulted.
        self.opponent_attack = None if opponent is None else opponent.attack
        self.opponent_defence = None if opponent is None else opponent.defence
        self.opponent_matches = None if opponent is None else float(opponent.matches)


def build_dataset(
    db: Session,
    seasons: Sequence[str] | None = None,
    information_state: str = InformationState.IN_SEASON,
    limit: int | None = None,
) -> Dataset:
    """Build one feature row per stored gameweek observation."""
    query = select(GameweekHistory).order_by(
        GameweekHistory.player_code, GameweekHistory.kickoff_time
    )
    if seasons is not None:
        query = query.where(GameweekHistory.season.in_(list(seasons)))

    all_rows = list(db.scalars(query).all())

    by_player: dict[int, list[GameweekHistory]] = defaultdict(list)
    for row in all_rows:
        by_player[row.player_code].append(row)

    # Opponent strength needs the whole league, not one player's history, so it
    # is computed once here and looked up per row.
    opponents = build_fixture_opponents(all_rows)
    strength: dict[str, dict[int, dict[str, TeamStrength]]] = {
        season: build_strength_by_gameweek(all_rows, season)
        for season in {row.season for row in all_rows}
    }

    def _opponent_strength(row: GameweekHistory) -> TeamStrength | None:
        opponent = opponents.get((row.season, row.fixture_id, row.team_name))
        if opponent is None:
            return None
        return strength.get(row.season, {}).get(int(row.gameweek), {}).get(opponent)

    data = Dataset(
        x=[], mask=[], y=[], minutes=[], started=[], season=[], gameweek=[],
        player_code=[], information_state=information_state,
        feature_names=FEATURE_NAMES,
    )

    for player_code, rows in by_player.items():
        for row in rows:
            if row.kickoff_time is None:
                # Without a timestamp the row cannot be placed in time, so it
                # can be neither a feature nor a safely-dated target.
                continue
            vector = build_features(
                rows,
                _Target(row, _opponent_strength(row)),
                row.kickoff_time,
                information_state,
            )
            data.x.append(vector.as_list())
            data.mask.append(vector.mask_list())
            data.y.append(float(row.points))
            data.minutes.append(float(row.minutes or 0))
            data.started.append(bool(row.started))
            data.season.append(row.season)
            data.gameweek.append(int(row.gameweek))
            data.player_code.append(player_code)

            if limit is not None and len(data.y) >= limit:
                logger.info("dataset truncated at limit=%s", limit)
                return data

    logger.info(
        "dataset built rows=%s state=%s seasons=%s",
        len(data.y), information_state, len(set(data.season)),
    )
    return data
