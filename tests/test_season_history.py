from types import SimpleNamespace

import pytest
from sqlalchemy import event, select

from app.db.models import GameweekHistory, PlayerSeasonAggregate
from app.services.season_history import (
    career_summaries,
    rebuild_season_aggregates,
    stored_seasons,
    summarise_career,
)


def _aggregate(**overrides):
    values = {
        "player_code": 1, "season": "2025/26", "fixtures": 38, "appearances": 30,
        "minutes": 2700, "starts": 30, "starts_derived": False, "points": 150,
        "goals": 0, "assists": 0, "clean_sheets": 0, "bonus": 0,
        "appearance_points": 150, "appearance_points_sq": 1050,
        "blanks": 10, "hauls": 3, "price_min": 6.0, "price_max": 6.5,
        "position": "MID", "team_name": "Test FC",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_rebuild_aggregates_each_player_season(seeded_history_db):
    # A mid-season transfer: the season is described by where he finished.
    for row in seeded_history_db.scalars(
        select(GameweekHistory).where(
            GameweekHistory.player_code == 1001,
            GameweekHistory.season == "2025/26",
        )
    ):
        row.position = "MID"
        row.team_name = "New FC" if row.gameweek == 10 else "Old FC"
    seeded_history_db.commit()

    result = rebuild_season_aggregates(seeded_history_db)

    assert result == {"seasons": 2, "rows": 4}
    regular = seeded_history_db.scalar(
        select(PlayerSeasonAggregate).where(
            PlayerSeasonAggregate.player_code == 1001,
            PlayerSeasonAggregate.season == "2025/26",
        )
    )
    assert (regular.fixtures, regular.appearances, regular.minutes) == (10, 10, 900)
    assert (regular.starts, regular.points) == (10, 55)
    assert (regular.appearance_points, regular.appearance_points_sq) == (55, 313)
    assert (regular.blanks, regular.hauls) == (0, 0)
    assert (regular.price_min, regular.price_max) == (7.5, 7.5)
    assert (regular.position, regular.team_name) == ("MID", "New FC")

    fringe = seeded_history_db.scalar(
        select(PlayerSeasonAggregate).where(
            PlayerSeasonAggregate.player_code == 1002,
            PlayerSeasonAggregate.season == "2024/25",
        )
    )
    # Fixtures count every registered match; appearances only those played.
    assert (fringe.fixtures, fringe.appearances, fringe.minutes) == (10, 7, 140)
    assert (fringe.points, fringe.blanks, fringe.starts) == (23, 2, 0)


def test_rebuild_is_idempotent_and_can_target_one_season(seeded_history_db):
    rebuild_season_aggregates(seeded_history_db)
    result = rebuild_season_aggregates(seeded_history_db, seasons=["2025/26"])

    assert result == {"seasons": 1, "rows": 2}
    assert len(seeded_history_db.scalars(select(PlayerSeasonAggregate)).all()) == 4


def test_career_summaries_exclude_the_current_season_in_one_query(seeded_history_db):
    rebuild_season_aggregates(seeded_history_db)
    statements = []
    engine = seeded_history_db.get_bind()
    event.listen(engine, "before_cursor_execute", lambda *args: statements.append(args[2]))

    summaries = career_summaries(
        seeded_history_db, [1001, 1002, 9999], exclude_season="2025/26"
    )

    assert len(statements) == 1
    assert set(summaries) == {1001, 1002}
    assert [item["season"] for item in summaries[1001]["seasons"]] == ["2024/25"]
    assert stored_seasons(seeded_history_db, exclude_season="2025/26") == ["2024/25"]
    assert stored_seasons(seeded_history_db) == ["2025/26", "2024/25"]


def test_steady_regular_is_summarised_across_seasons(seeded_history_db):
    rebuild_season_aggregates(seeded_history_db)
    summary = career_summaries(seeded_history_db, [1001])[1001]

    assert summary["season_count"] == 2
    assert summary["qualifying_seasons"] == 2
    assert summary["mean_p90"] == pytest.approx(5.5)
    assert summary["p90_spread"] == pytest.approx(0.0)
    assert summary["consistency"] == "Steady"
    assert summary["points_by_season"] == {"2025/26": 55, "2024/25": 55}
    # 20 appearances pooled: mean 5.5, population variance 1.05.
    assert summary["gw_sd"] == pytest.approx(1.05 ** 0.5, abs=1e-3)
    assert summary["blank_rate"] == 0.0
    assert summary["minutes_share"] == pytest.approx(100.0)
    assert summary["durability"] == "High"


def test_thin_samples_are_withheld_rather_than_scored(seeded_history_db):
    rebuild_season_aggregates(seeded_history_db)
    summary = career_summaries(seeded_history_db, [1002])[1002]

    # 140 minutes a season never reaches the P/90 floor or a qualifying season.
    assert summary["seasons"][0]["p90"] is None
    assert summary["qualifying_seasons"] == 0
    assert summary["mean_p90"] is None
    assert summary["p90_spread"] is None
    assert summary["consistency"] is None
    # 14 pooled appearances and 20 fixtures are enough for the pooled measures.
    assert summary["blank_rate"] == pytest.approx(100 * 4 / 14)
    assert summary["durability"] == "Low"


def test_consistency_bands_follow_relative_spread():
    steady = summarise_career([
        _aggregate(season="2025/26", points=150),
        _aggregate(season="2024/25", points=165),
    ])
    volatile = summarise_career([
        _aggregate(season="2025/26", points=240),
        _aggregate(season="2024/25", points=120),
    ])
    single = summarise_career([_aggregate()])

    assert steady["consistency"] == "Steady"
    assert volatile["consistency"] == "Volatile"
    assert single["mean_p90"] == pytest.approx(5.0)
    assert single["p90_spread"] is None and single["consistency"] is None


def test_pooled_measures_need_a_minimum_sample():
    summary = summarise_career([
        _aggregate(fixtures=12, appearances=6, minutes=300, starts=3, points=20,
                   appearance_points=20, appearance_points_sq=90, blanks=3, hauls=0),
    ])

    assert summary["gw_sd"] is None
    assert summary["blank_rate"] is None and summary["haul_rate"] is None
    assert summary["minutes_share"] is None and summary["start_rate"] is None
    assert summary["durability"] is None


def test_derived_starts_are_flagged_on_the_summary():
    summary = summarise_career([
        _aggregate(season="2025/26"),
        _aggregate(season="2020/21", starts_derived=True),
    ])

    assert summary["starts_estimated"] is True
    assert summary["seasons"][1]["starts_derived"] is True


def test_pooled_measures_start_at_the_first_qualifying_season():
    academy = _aggregate(
        season="2022/23", fixtures=38, appearances=2, minutes=20, starts=0,
        points=2, appearance_points=2, appearance_points_sq=2, blanks=2, hauls=0,
    )
    summary = summarise_career([
        _aggregate(season="2025/26"),
        _aggregate(season="2024/25", minutes=400, appearances=10, starts=4),
        _aggregate(season="2023/24"),
        academy,
    ])

    # The academy season is listed but not pooled; the thin season between two
    # qualifying ones is part of the established record and is.
    assert summary["season_count"] == 4
    assert summary["pooled_since"] == "2023/24"
    assert summary["minutes_share"] == pytest.approx(100 * 5800 / (90 * 114))
    assert summary["blank_rate"] == pytest.approx(100 * 30 / 70)

    never_established = summarise_career([academy, _aggregate(
        season="2023/24", fixtures=38, appearances=9, minutes=200, starts=1,
        points=12, appearance_points=12, appearance_points_sq=30, blanks=7, hauls=0,
    )])
    assert never_established["pooled_since"] == "2022/23"
    assert never_established["durability"] == "Low"
