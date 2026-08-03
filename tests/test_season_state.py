from datetime import datetime, timedelta, timezone

from app.services.season_state import (
    FEATURES,
    Readiness,
    SeasonState,
    readiness_for,
    season_state,
)


NOW = datetime(2026, 8, 3, 12, 0, tzinfo=timezone.utc)


class _GW:
    def __init__(
        self,
        number,
        deadline,
        finished=False,
        data_checked=None,
        is_current=False,
        is_next=False,
    ):
        self.number = number
        self.deadline_time = deadline
        self.finished = finished
        self.data_checked = finished if data_checked is None else data_checked
        self.is_current = is_current
        self.is_next = is_next


def test_uninitialized_when_no_gameweeks_exist():
    result = season_state([], NOW, NOW)
    assert result["state"] == SeasonState.UNINITIALIZED
    assert result["explanation"]


def test_fpl_unavailable_when_nothing_has_been_captured():
    result = season_state([_GW(1, NOW + timedelta(days=12), is_next=True)], None, NOW)
    assert result["state"] == SeasonState.FPL_UNAVAILABLE


def test_preseason_before_the_first_deadline():
    result = season_state([_GW(1, NOW + timedelta(days=12), is_next=True)], NOW, NOW)
    assert result["state"] == SeasonState.PRESEASON
    assert result["next_gameweek"] == 1
    assert result["current_gameweek"] is None
    assert result["seconds_to_deadline"] > 0


def test_pre_deadline_inside_the_warning_window():
    result = season_state([_GW(3, NOW + timedelta(hours=6), is_next=True)], NOW, NOW)
    assert result["state"] == SeasonState.PRE_DEADLINE
    assert 0 < result["seconds_to_deadline"] <= 24 * 3600


def test_deadline_passed_before_the_gameweek_is_finished():
    gameweeks = [
        _GW(1, NOW - timedelta(days=7), finished=True),
        _GW(2, NOW - timedelta(hours=2), is_current=True),
    ]
    result = season_state(gameweeks, NOW, NOW)
    assert result["state"] == SeasonState.DEADLINE_PASSED
    assert result["current_gameweek"] == 2


def test_provisional_when_finished_but_not_data_checked():
    gameweeks = [
        _GW(1, NOW - timedelta(days=7), finished=True),
        _GW(
            2,
            NOW - timedelta(days=1),
            finished=True,
            data_checked=False,
            is_current=True,
        ),
        _GW(3, NOW + timedelta(days=5), is_next=True),
    ]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.PROVISIONAL


def test_international_break_when_the_next_deadline_is_far_away():
    gameweeks = [
        _GW(1, NOW - timedelta(days=7), finished=True, is_current=True),
        _GW(2, NOW + timedelta(days=14), is_next=True),
    ]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.INTERNATIONAL_BREAK


def test_gameweek_open_between_deadlines():
    gameweeks = [
        _GW(1, NOW - timedelta(days=3), finished=True, is_current=True),
        _GW(2, NOW + timedelta(days=4), is_next=True),
    ]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.GAMEWEEK_OPEN


def test_postseason_when_every_gameweek_is_finished():
    gameweeks = [
        _GW(number, NOW - timedelta(days=40), finished=True)
        for number in range(1, 39)
    ]
    assert season_state(gameweeks, NOW, NOW)["state"] == SeasonState.POSTSEASON


def test_stale_capture_is_reported_even_in_a_valid_state():
    result = season_state(
        [_GW(1, NOW + timedelta(days=12), is_next=True)],
        NOW - timedelta(days=4),
        NOW,
    )
    assert result["state"] == SeasonState.PRESEASON
    assert result["data_is_stale"] is True


def test_readiness_reports_missing_inputs_and_the_activation_condition():
    result = readiness_for(
        "projections",
        {"projections_available": 0, "player_count": 500},
        state=SeasonState.PRESEASON,
    )
    assert result["state"] == Readiness.NOT_READY
    assert result["missing"]
    assert result["activates_when"]
    assert result["explanation"]


def test_readiness_is_ready_when_requirements_are_met():
    result = readiness_for(
        "projections",
        {"projections_available": 480, "player_count": 500},
        state=SeasonState.GAMEWEEK_OPEN,
    )
    assert result["state"] == Readiness.READY
    assert result["missing"] == []


def test_readiness_is_stale_when_the_data_is_too_old():
    result = readiness_for(
        "projections",
        {"projections_available": 480, "player_count": 500},
        state=SeasonState.GAMEWEEK_OPEN,
        age_hours=100.0,
    )
    assert result["state"] == Readiness.STALE


def test_readiness_is_not_ready_in_an_unsupported_season_state():
    result = readiness_for(
        "movers",
        {"snapshot_count": 5},
        state=SeasonState.UNINITIALIZED,
    )
    assert result["state"] == Readiness.NOT_READY


def test_every_feature_declares_its_requirements():
    assert FEATURES
    for name, feature in FEATURES.items():
        assert feature.required_inputs, name
        assert feature.supported_states, name
        assert feature.activates_when, name
        assert feature.fallback, name


def test_an_unknown_feature_is_an_error_not_a_silent_pass():
    result = readiness_for("no_such_feature", {}, state=SeasonState.GAMEWEEK_OPEN)
    assert result["state"] == Readiness.ERROR
