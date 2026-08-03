import pytest

from app.services.archive_schema import (
    CORE_COLUMNS,
    detect_era,
    normalise_row,
    season_label,
)

MODERN = {
    "name": "Mohamed Salah", "position": "MID", "team": "Liverpool",
    "element": "328", "fixture": "12", "round": "3", "GW": "3",
    "minutes": "90", "starts": "1", "total_points": "13", "goals_scored": "2",
    "assists": "1", "clean_sheets": "0", "goals_conceded": "1", "saves": "0",
    "bonus": "3", "bps": "52", "yellow_cards": "0", "red_cards": "0",
    "own_goals": "0", "penalties_missed": "0", "penalties_saved": "0",
    "expected_goals": "0.87", "expected_assists": "0.31",
    "expected_goal_involvements": "1.18", "expected_goals_conceded": "1.02",
    "influence": "78.4", "creativity": "45.1", "threat": "62.0",
    "ict_index": "18.5", "value": "128", "selected": "4210000",
    "transfers_in": "120000", "transfers_out": "8000",
    "transfers_balance": "112000", "opponent_team": "7", "was_home": "True",
    "kickoff_time": "2026-08-30T14:00:00Z",
}

MINIMAL = {
    key: value for key, value in MODERN.items()
    if key not in {
        "position", "team", "starts", "expected_goals", "expected_assists",
        "expected_goal_involvements", "expected_goals_conceded",
    }
}


def test_season_label_converts_archive_directory_format():
    assert season_label("2024-25") == "2024/25"
    assert season_label("2016-17") == "2016/17"


def test_season_label_refuses_to_guess():
    with pytest.raises(ValueError):
        season_label("2024")
    with pytest.raises(ValueError):
        season_label("2024/25")


def test_era_detection():
    assert detect_era(set(MODERN)) == "modern"
    assert detect_era(set(MINIMAL)) == "minimal"
    assert detect_era(set(MINIMAL) | {"position", "team", "xP"}) == "transitional"
    assert detect_era(set(MINIMAL) | {"attempted_passes", "ea_index"}) == "legacy"


def test_core_columns_are_present_in_every_era():
    for payload in (MODERN, MINIMAL):
        assert CORE_COLUMNS <= set(payload)


def test_modern_row_records_observed_starts():
    row = normalise_row(MODERN, "2024/25")
    assert row.player_element == 328
    assert row.gameweek == 3
    assert row.minutes == 90
    assert row.starts == 1
    assert row.started is True
    assert row.started_is_derived is False
    assert row.expected_goals == 0.87
    assert row.price == 12.8
    assert row.is_home is True
    assert row.position == "MID"


def test_minimal_row_derives_started_and_flags_it():
    row = normalise_row(MINIMAL, "2019/20")
    assert row.starts is None
    assert row.started is True
    assert row.started_is_derived is True
    # Never recorded that season: absent, not zero.
    assert row.expected_goals is None
    assert row.position is None


def test_a_derived_start_uses_the_sixty_minute_rule():
    assert normalise_row(dict(MINIMAL, minutes="45"), "2019/20").started is False
    assert normalise_row(dict(MINIMAL, minutes="59"), "2019/20").started is False
    assert normalise_row(dict(MINIMAL, minutes="60"), "2019/20").started is True


def test_a_row_without_an_identity_is_rejected():
    assert normalise_row(dict(MODERN, element=""), "2024/25") is None
    assert normalise_row(dict(MODERN, GW="", round=""), "2024/25") is None
    assert normalise_row(dict(MODERN, GW="0", round="0"), "2024/25") is None


def test_quoted_legacy_headers_are_tolerated_once_stripped():
    stripped = {key.strip('"'): value for key, value in MINIMAL.items()}
    assert normalise_row(stripped, "2016/17") is not None


def test_missing_optional_numbers_are_none_not_zero():
    sparse = dict(MINIMAL)
    sparse["saves"] = ""
    sparse["bps"] = ""
    row = normalise_row(sparse, "2019/20")
    assert row.saves is None
    assert row.bps is None
    # A genuine zero survives as zero.
    assert normalise_row(dict(MINIMAL, saves="0"), "2019/20").saves == 0


def test_price_converts_from_tenths_of_a_million():
    assert normalise_row(dict(MODERN, value="55"), "2024/25").price == 5.5
    assert normalise_row(dict(MODERN, value=""), "2024/25").price is None


def test_gameweek_falls_back_to_round_when_gw_is_absent():
    payload = dict(MODERN)
    del payload["GW"]
    assert normalise_row(payload, "2024/25").gameweek == 3
