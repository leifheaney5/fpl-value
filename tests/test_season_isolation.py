"""Season boundaries must be enforced, not assumed.

The application stores previous-season imports in the same table as
current-season snapshots. Without a season discriminator, a "7 day value change"
could silently be the difference between two seasons.
"""

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from app.config import Settings
from app.db.base import Base
from app.db.models import PlayerSnapshot
from app.services.history_import import import_history_directory
from app.services.queries import CrossSeasonError, _history_comparison, latest_rows
from app.services.refresh import refresh_data

from fakes import FakeClient


class _Snap:
    def __init__(self, season, value, price, ownership, rank):
        self.season = season
        self.value = value
        self.price = price
        self.ownership = ownership
        self.value_rank = rank
        self.captured_at = None


def test_comparing_two_seasons_raises_rather_than_returning_a_number():
    current = _Snap("2026/27", 5.0, 7.0, 10.0, 4)
    previous = _Snap("2025/26", 9.0, 6.5, 22.0, 1)

    with pytest.raises(CrossSeasonError):
        _history_comparison(current, previous)


def test_same_season_comparison_still_works():
    current = _Snap("2026/27", 5.0, 7.0, 10.0, 4)
    previous = _Snap("2026/27", 4.0, 6.5, 8.0, 9)

    result = _history_comparison(current, previous)

    assert result["delta_value"] == 1.0
    assert result["delta_price"] == 0.5
    assert result["delta_rank"] == 5


def test_a_null_metric_produces_a_null_delta_not_a_fabricated_one():
    current = _Snap("2026/27", None, 7.0, 10.0, None)
    previous = _Snap("2026/27", 4.0, 6.5, 8.0, 9)

    result = _history_comparison(current, previous)

    assert result["delta_value"] is None
    assert result["delta_rank"] is None
    assert result["delta_price"] == 0.5


def _seeded(tmp_path, name):
    url = f"sqlite:///{tmp_path / name}"
    engine = create_engine(url, connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(engine, expire_on_commit=False)
    settings = Settings(database_url=url, current_season="2026/27")
    with Session() as db:
        refresh_data(db, settings, FakeClient())
    return Session, settings


def test_latest_rows_only_returns_the_requested_season(tmp_path):
    Session, settings = _seeded(tmp_path, "seasons.db")

    with Session() as db:
        current = latest_rows(db, season="2026/27")
        assert current

        # Plant a previous-season snapshot at a later timestamp. Without season
        # scoping it would become "the latest" and take over every page.
        newest = max(row["snapshot"].captured_at for row in current)
        db.add(
            PlayerSnapshot(
                player_id=current[0]["player"].id,
                refresh_run_id=current[0]["snapshot"].refresh_run_id,
                captured_at=newest.replace(year=newest.year + 1),
                season="2025/26",
                price=5.0,
                total_points=200,
                minutes=3000,
                starts=34,
                team_matches=38,
            )
        )
        db.commit()

        still_current = latest_rows(db, season="2026/27")
        assert {row["snapshot"].season for row in still_current} == {"2026/27"}

        previous = latest_rows(db, season="2025/26")
        assert {row["snapshot"].season for row in previous} == {"2025/26"}


def test_history_import_requires_an_explicit_season(tmp_path):
    Session, _ = _seeded(tmp_path, "import.db")
    directory = tmp_path / "history"
    directory.mkdir()
    (directory / "old.csv").write_text(
        "player_id,total_points,minutes,starts,team_matches,price\n10,180,3000,34,38,7.5\n",
        encoding="utf-8",
    )

    with Session() as db:
        with pytest.raises(TypeError):
            import_history_directory(db, directory)  # season is required

        import_history_directory(db, directory, season="2025/26")
        imported = db.scalars(
            select(PlayerSnapshot).where(PlayerSnapshot.season == "2025/26")
        ).all()
        assert imported
        assert all(snapshot.season == "2025/26" for snapshot in imported)

        # And the imported rows must not leak into the current season.
        assert all(
            row["snapshot"].season == "2026/27"
            for row in latest_rows(db, season="2026/27")
        )
