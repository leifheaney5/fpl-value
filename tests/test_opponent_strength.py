"""Opponent strength, computed strictly from matches already played.

The archive carries no fixture difficulty rating, so every model evaluated so
far has been blind to who the opponent is -- one of the strongest signals in the
sport. This derives it from results.

The whole difficulty is the point-in-time constraint. Computing team strength
from full-season totals would leak the outcome of later matches into every
earlier row, and would do so invisibly: the model would simply look excellent.
These tests exist to make that impossible.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from app.models.opponent import (
    TeamStrength,
    build_fixture_opponents,
    build_strength_by_gameweek,
    build_team_strength,
)

NOW = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)


def _row(team, opponent, scored, conceded, weeks_ago, season="2026/27"):
    return SimpleNamespace(
        season=season,
        team_name=team,
        opponent_team_id=opponent,
        goals=scored,
        goals_conceded=conceded,
        kickoff_time=NOW - timedelta(weeks=weeks_ago),
        minutes=90,
    )


def test_strength_uses_only_matches_before_the_cutoff():
    """The leakage guarantee for team strength."""
    played = [
        _row("ARS", 2, scored=3, conceded=0, weeks_ago=3),
        _row("ARS", 3, scored=2, conceded=1, weeks_ago=2),
    ]
    future = [
        _row("ARS", 4, scored=9, conceded=9, weeks_ago=-1),
        _row("ARS", 5, scored=9, conceded=9, weeks_ago=-2),
    ]

    clean = build_team_strength(played, NOW, "2026/27")
    poisoned = build_team_strength(played + future, NOW, "2026/27")

    assert clean["ARS"].attack == poisoned["ARS"].attack, (
        "a match after the cutoff changed team strength: this is leakage"
    )
    assert clean["ARS"].defence == poisoned["ARS"].defence


def test_a_match_exactly_at_the_cutoff_is_excluded():
    at_cutoff = [_row("ARS", 2, scored=5, conceded=5, weeks_ago=0)]
    assert build_team_strength(at_cutoff, NOW, "2026/27") == {}


def test_attack_and_defence_are_per_match_rates():
    rows = [
        _row("ARS", 2, scored=3, conceded=1, weeks_ago=3),
        _row("ARS", 3, scored=1, conceded=1, weeks_ago=2),
    ]
    strength = build_team_strength(rows, NOW, "2026/27")
    assert strength["ARS"].matches == 2
    assert strength["ARS"].attack == 2.0
    assert strength["ARS"].defence == 1.0


def test_only_the_requested_season_contributes():
    rows = [
        _row("ARS", 2, scored=9, conceded=9, weeks_ago=60, season="2025/26"),
        _row("ARS", 3, scored=1, conceded=1, weeks_ago=2, season="2026/27"),
    ]
    strength = build_team_strength(rows, NOW, "2026/27")
    assert strength["ARS"].matches == 1
    assert strength["ARS"].attack == 1.0


def test_a_team_with_no_matches_is_absent_rather_than_zero():
    """Absent means unknown. Zero would claim the team scores nothing."""
    strength = build_team_strength([], NOW, "2026/27")
    assert "ARS" not in strength
    assert strength == {}


def test_duplicate_player_rows_for_one_match_are_counted_once():
    """History is per player, so eleven players share one team match.

    Counting each player row as a match would divide by eleven and make every
    rate meaningless.
    """
    rows = [
        _row("ARS", 2, scored=3, conceded=1, weeks_ago=3) for _ in range(11)
    ]
    strength = build_team_strength(rows, NOW, "2026/27")
    assert strength["ARS"].matches == 1, (
        "eleven player rows for one fixture were counted as eleven matches"
    )
    assert strength["ARS"].attack == 3.0


def test_rows_without_a_team_or_timestamp_are_ignored():
    rows = [
        _row("ARS", 2, scored=3, conceded=1, weeks_ago=3),
        SimpleNamespace(season="2026/27", team_name=None, opponent_team_id=2,
                        goals=9, goals_conceded=9, kickoff_time=NOW - timedelta(weeks=1)),
        SimpleNamespace(season="2026/27", team_name="ARS", opponent_team_id=3,
                        goals=9, goals_conceded=9, kickoff_time=None),
    ]
    strength = build_team_strength(rows, NOW, "2026/27")
    assert strength["ARS"].matches == 1


def test_gameweek_strength_never_includes_its_own_results():
    """Strength for gameweek G must use only gameweeks before G."""
    rows = []
    for gameweek in (1, 2, 3):
        # A realistic squad: two scorers and nine who did not, so the team
        # scored 2 and conceded 1 each week.
        for goals in [1, 1] + [0] * 9:
            rows.append(
                SimpleNamespace(
                    season="2026/27", team_name="ARS", fixture_id=gameweek,
                    gameweek=gameweek, goals=goals, goals_conceded=1,
                    kickoff_time=NOW - timedelta(weeks=10 - gameweek),
                    opponent_team_id=9,
                )
            )

    by_gameweek = build_strength_by_gameweek(rows, "2026/27")

    # Gameweek 1 has nothing before it.
    assert by_gameweek[1] == {}
    # Gameweek 2 sees exactly one match; gameweek 3 sees two.
    assert by_gameweek[2]["ARS"].matches == 1
    assert by_gameweek[3]["ARS"].matches == 2
    assert by_gameweek[3]["ARS"].attack == 2.0


def test_team_goals_are_summed_across_the_squad():
    """Goals are per player, so a team total is the sum, not one player's row.

    Deduplicating to a single player row gives roughly zero goals per match,
    because most players do not score. Real 2024/25 data showed a mean team
    attack of 0.05 goals per match under that bug.
    """
    squad = [
        SimpleNamespace(season="2026/27", team_name="ARS", fixture_id=1, gameweek=1,
                        goals=goals, goals_conceded=1, kickoff_time=NOW,
                        opponent_team_id=9)
        # Two scorers and thirteen who did not score: a 3-1 win.
        for goals in [2, 1] + [0] * 13
    ]
    by_gameweek = build_strength_by_gameweek(squad, "2026/27")
    # Gameweek 1 sees nothing before it, so check via a later gameweek.
    later = squad + [
        SimpleNamespace(season="2026/27", team_name="ARS", fixture_id=2, gameweek=2,
                        goals=0, goals_conceded=0, kickoff_time=NOW,
                        opponent_team_id=8)
    ]
    by_gameweek = build_strength_by_gameweek(later, "2026/27")
    assert by_gameweek[2]["ARS"].matches == 1
    assert by_gameweek[2]["ARS"].attack == 3.0, "squad goals were not summed"


def test_goals_conceded_is_the_squad_maximum_not_the_sum():
    """Conceded is counted per player only while they were on the pitch.

    Summing it multiplies the true figure by the squad size; the player who
    played the whole match saw them all.
    """
    squad = [
        SimpleNamespace(season="2026/27", team_name="ARS", fixture_id=1, gameweek=1,
                        goals=0, goals_conceded=conceded, kickoff_time=NOW,
                        opponent_team_id=9)
        # Starters saw both goals; a late substitute saw one.
        for conceded in [2] * 10 + [1]
    ] + [
        SimpleNamespace(season="2026/27", team_name="ARS", fixture_id=2, gameweek=2,
                        goals=0, goals_conceded=0, kickoff_time=NOW,
                        opponent_team_id=8)
    ]
    by_gameweek = build_strength_by_gameweek(squad, "2026/27")
    assert by_gameweek[2]["ARS"].defence == 2.0, "conceded was summed across the squad"


def test_opponents_resolve_through_the_shared_fixture():
    rows = [
        SimpleNamespace(season="2026/27", fixture_id=7, team_name="ARS"),
        SimpleNamespace(season="2026/27", fixture_id=7, team_name="CHE"),
        SimpleNamespace(season="2026/27", fixture_id=8, team_name="LIV"),
        SimpleNamespace(season="2026/27", fixture_id=8, team_name="EVE"),
    ]
    opponents = build_fixture_opponents(rows)
    assert opponents[("2026/27", 7, "ARS")] == "CHE"
    assert opponents[("2026/27", 7, "CHE")] == "ARS"
    assert opponents[("2026/27", 8, "LIV")] == "EVE"


def test_a_fixture_without_two_sides_is_skipped_not_guessed():
    """Team names are absent before 2021-22, so one-sided fixtures are normal."""
    rows = [SimpleNamespace(season="2026/27", fixture_id=7, team_name="ARS")]
    assert build_fixture_opponents(rows) == {}

    nameless = [
        SimpleNamespace(season="2019/20", fixture_id=7, team_name=None),
        SimpleNamespace(season="2019/20", fixture_id=7, team_name=None),
    ]
    assert build_fixture_opponents(nameless) == {}


def test_team_strength_is_comparable_across_teams():
    rows = [
        _row("ARS", 2, scored=4, conceded=0, weeks_ago=3),
        _row("BUR", 3, scored=0, conceded=4, weeks_ago=3),
    ]
    strength = build_team_strength(rows, NOW, "2026/27")
    assert strength["ARS"].attack > strength["BUR"].attack
    assert strength["ARS"].defence < strength["BUR"].defence
