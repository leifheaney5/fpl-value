from app.models.dataset import build_dataset
from app.models.features import FEATURE_NAMES, InformationState


def test_dataset_rows_align_across_every_parallel_array(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    n = len(data.y)
    assert n > 0
    for name in ("x", "mask", "minutes", "started", "season", "gameweek", "player_code"):
        assert len(getattr(data, name)) == n, f"{name} is not aligned with y"
    assert data.feature_names == FEATURE_NAMES
    assert all(len(row) == len(FEATURE_NAMES) for row in data.x)
    assert all(len(row) == len(FEATURE_NAMES) for row in data.mask)


def test_slicing_by_season_preserves_alignment(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    seasons = sorted(set(data.season))
    assert len(seasons) >= 2

    sliced = data.slice_seasons([seasons[0]])
    assert set(sliced.season) == {seasons[0]}
    assert len(sliced.x) == len(sliced.y) == len(sliced.season) == len(sliced.mask)
    assert len(sliced.y) < len(data.y)
    assert sliced.information_state == data.information_state


def test_slicing_an_absent_season_yields_an_empty_dataset(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    empty = data.slice_seasons(["1999/00"])
    assert empty.y == []
    assert empty.x == []


def test_preseason_dataset_masks_current_season_features(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.PRESEASON
    )
    current = [i for i, name in enumerate(FEATURE_NAMES) if name.startswith("cur_")]
    assert current
    for row_mask, row_values in zip(data.mask, data.x):
        for index in current:
            assert row_mask[index] == 0.0
            assert row_values[index] == 0.0


def test_in_season_dataset_reveals_current_season_features(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    current = [i for i, name in enumerate(FEATURE_NAMES) if name.startswith("cur_")]
    assert any(row[index] for row in data.mask for index in current)


def test_target_is_the_points_scored_in_that_fixture(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    assert any(value > 0 for value in data.y)
    # A dataset where every target is identical cannot distinguish a model from
    # a constant, so tests built on it would pass for the wrong reason.
    assert len(set(data.y)) > 1


def test_minutes_and_started_are_carried_for_the_minutes_head(seeded_history_db):
    data = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    assert any(data.started)
    assert not all(data.started)
    assert any(value == 90 for value in data.minutes)


def test_restricting_seasons_at_build_time_matches_slicing(seeded_history_db):
    full = build_dataset(
        seeded_history_db, information_state=InformationState.IN_SEASON
    )
    season = sorted(set(full.season))[0]
    built = build_dataset(
        seeded_history_db,
        seasons=[season],
        information_state=InformationState.IN_SEASON,
    )
    sliced = full.slice_seasons([season])
    assert built.y == sliced.y
    assert built.x == sliced.x
