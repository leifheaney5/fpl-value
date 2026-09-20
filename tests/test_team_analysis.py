from datetime import datetime, timedelta, timezone

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.db.base import Base
from app.db.models import Fixture, Team
from app.services.team_analysis import _badge_url, fixture_analysis, team_performance


def _session(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'team-analysis.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False)()


def _teams(db):
    now = datetime(2026, 9, 1, tzinfo=timezone.utc)
    db.add_all(
        [
            Team(id=1, name="Alpha", short_name="ALP", updated_at=now),
            Team(id=2, name="Bravo", short_name="BRV", updated_at=now),
        ]
    )


def _fixture_raw(home_difficulty, away_difficulty, **scores):
    return {
        "team_h_difficulty": home_difficulty,
        "team_a_difficulty": away_difficulty,
        **scores,
    }


def test_badge_urls_use_official_club_codes_with_an_unknown_fallback():
    assert _badge_url(1, "Arsenal").endswith("/t3.png")
    assert _badge_url(1, "Imported Arsenal", team_code=3).endswith("/t3.png")
    assert _badge_url(99, "Imported Club").endswith("/t99.png")
    assert _badge_url(99, "Imported Club", team_code=None).endswith("/t99.png")


def test_fixture_analysis_uses_the_next_ten_correct_side_fdrs_and_badges(tmp_path):
    """Swapping home and away FDRs must change the displayed difficulty."""
    db = _session(tmp_path)
    _teams(db)
    kickoff = datetime(2026, 9, 10, 12, tzinfo=timezone.utc)
    for event in range(1, 13):
        alpha_home = event % 2 == 1
        db.add(
            Fixture(
                id=event,
                event=event,
                kickoff_time=kickoff + timedelta(days=event),
                team_h=1 if alpha_home else 2,
                team_a=2 if alpha_home else 1,
                team_h_difficulty=2 if alpha_home else 4,
                team_a_difficulty=4 if alpha_home else 2,
                finished=False,
                raw=_fixture_raw(
                    2 if alpha_home else 4,
                    4 if alpha_home else 2,
                ),
            )
        )
    db.commit()

    rows = fixture_analysis(db)

    assert [row["team"].name for row in rows] == ["Alpha", "Bravo"]
    alpha, bravo = rows
    assert len(alpha["fixtures"]) == 10
    assert alpha["average_difficulty"] == 2.0
    assert bravo["average_difficulty"] == 4.0
    assert alpha["fixtures"][0] == {
        "event": 1,
        "kickoff_time": (kickoff + timedelta(days=1)).replace(tzinfo=None),
        "opponent": "Bravo",
        "opponent_id": 2,
        "is_home": True,
        "difficulty": 2,
        "badge_url": "https://resources.premierleague.com/premierleague/badges/t2.png",
    }
    assert alpha["fixtures"][1]["is_home"] is False
    assert alpha["fixtures"][1]["difficulty"] == 2


def test_fixture_analysis_excludes_missing_and_out_of_domain_fdr_from_averages(tmp_path):
    """A missing, fractional, or out-of-range FDR must not rank as a fixture."""
    db = _session(tmp_path)
    _teams(db)
    db.add(
        Fixture(
            id=1,
            event=1,
            kickoff_time=datetime(2026, 9, 11, tzinfo=timezone.utc),
            team_h=1,
            team_a=2,
            # Refresh must retain a non-null column for compatibility, but the
            # missing upstream key in raw means this placeholder is not FDR.
            team_h_difficulty=3,
            team_a_difficulty=4,
            finished=False,
            raw={"team_a_difficulty": 4},
        )
    )
    db.add(
        Fixture(
            id=2,
            event=2,
            kickoff_time=datetime(2026, 9, 12, tzinfo=timezone.utc),
            team_h=1,
            team_a=2,
            team_h_difficulty=2,
            team_a_difficulty=4,
            finished=False,
            raw=_fixture_raw(2, 4),
        )
    )
    db.commit()

    alpha = next(row for row in fixture_analysis(db) if row["team"].name == "Alpha")

    assert alpha["complete"] is False
    assert alpha["average_difficulty"] == 2.0
    assert alpha["fixtures"][0]["difficulty"] is None

    for fixture_id, raw_difficulty in enumerate((0, 6, 2.5, True), start=3):
        db.add(
            Fixture(
                id=fixture_id,
                event=fixture_id,
                kickoff_time=datetime(2026, 9, fixture_id + 10, tzinfo=timezone.utc),
                team_h=1,
                team_a=2,
                team_h_difficulty=3,
                team_a_difficulty=4,
                finished=False,
                raw=_fixture_raw(raw_difficulty, 4),
            )
        )
    db.commit()

    alpha = next(row for row in fixture_analysis(db) if row["team"].name == "Alpha")

    assert alpha["complete"] is False
    assert alpha["average_difficulty"] == 2.0
    assert [fixture["difficulty"] for fixture in alpha["fixtures"]] == [
        None, 2, None, None, None, None,
    ]


def test_fixture_analysis_keeps_both_double_gameweek_fixtures(tmp_path):
    """Collapsing fixtures by gameweek would hide a double gameweek."""
    db = _session(tmp_path)
    _teams(db)
    kickoff = datetime(2026, 9, 10, tzinfo=timezone.utc)
    for fixture_id, hour in ((1, 0), (2, 48)):
        db.add(
            Fixture(
                id=fixture_id,
                event=4,
                kickoff_time=kickoff + timedelta(hours=hour),
                team_h=1,
                team_a=2,
                team_h_difficulty=2,
                team_a_difficulty=4,
                finished=False,
                raw=_fixture_raw(2, 4),
            )
        )
    db.commit()

    alpha = fixture_analysis(db)[0]

    assert [fixture["event"] for fixture in alpha["fixtures"]] == [4, 4]
    assert alpha["available"] == 2


def test_team_performance_uses_latest_ten_scored_matches_in_chronological_order(tmp_path):
    """Including an old result or treating a score gap as a loss changes form."""
    db = _session(tmp_path)
    _teams(db)
    kickoff = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    # Alpha's event-one win must fall outside the last-ten window. Events 2-11
    # contain four wins, three draws, and three losses for 15 points.
    scores = [(2, 0), (3, 0), (1, 1), (2, 2), (0, 0), (0, 1), (1, 2), (0, 3), (4, 1), (2, 1), (1, 0)]
    for event, (home_score, away_score) in enumerate(scores, start=1):
        db.add(
            Fixture(
                id=event,
                event=event,
                kickoff_time=kickoff + timedelta(days=event),
                team_h=1,
                team_a=2,
                team_h_difficulty=3,
                team_a_difficulty=3,
                finished=True,
                raw=_fixture_raw(
                    3,
                    3,
                    team_h_score=home_score,
                    team_a_score=away_score,
                ),
            )
        )
    db.add(
        Fixture(
            id=12,
            event=12,
            kickoff_time=kickoff + timedelta(days=12),
            team_h=1,
            team_a=2,
            team_h_difficulty=3,
            team_a_difficulty=3,
            finished=True,
            raw=_fixture_raw(3, 3, team_h_score=None, team_a_score=None),
        )
    )
    db.commit()

    alpha = team_performance(db)[0]

    assert alpha["team"].name == "Alpha"
    assert [result["event"] for result in alpha["results"]] == list(range(2, 12))
    assert [result["result"] for result in alpha["results"]] == [
        "W", "D", "D", "D", "L", "L", "L", "W", "W", "W",
    ]
    assert alpha["wins"] == 4
    assert alpha["draws"] == 3
    assert alpha["losses"] == 3
    assert alpha["points"] == 15
    assert alpha["points_per_game"] == 1.5
    assert alpha["goals_for"] == 14
    assert alpha["goals_against"] == 11
    assert alpha["available"] == 10
    assert alpha["complete"] is True


def test_team_performance_ignores_invalid_newer_scores_before_limiting(tmp_path):
    """Malformed latest scores must not displace the valid last-ten matches."""
    db = _session(tmp_path)
    _teams(db)
    kickoff = datetime(2026, 8, 1, 12, tzinfo=timezone.utc)
    for event in range(1, 12):
        db.add(
            Fixture(
                id=event,
                event=event,
                kickoff_time=kickoff + timedelta(days=event),
                team_h=1,
                team_a=2,
                team_h_difficulty=3,
                team_a_difficulty=3,
                finished=True,
                raw=_fixture_raw(3, 3, team_h_score=1, team_a_score=0),
            )
        )
    invalid_scores = [
        (True, 0),
        (-1, 0),
        (1.5, 0),
        ("2", 0),
        (2147483648, 0),
    ]
    for event, (home_score, away_score) in enumerate(invalid_scores, start=12):
        db.add(
            Fixture(
                id=event,
                event=event,
                kickoff_time=kickoff + timedelta(days=event),
                team_h=1,
                team_a=2,
                team_h_difficulty=3,
                team_a_difficulty=3,
                finished=True,
                raw=_fixture_raw(
                    3,
                    3,
                    team_h_score=home_score,
                    team_a_score=away_score,
                ),
            )
        )
    db.commit()

    alpha = team_performance(db)[0]

    assert [result["event"] for result in alpha["results"]] == list(range(2, 12))
    assert alpha["points"] == 30
