import pytest
from sqlalchemy import select

from app.db.models import Prediction
from app.models.artefact import Manifest, load_artefact, save_artefact
from app.models.features import FEATURE_NAMES, VERSION as FEATURE_VERSION
from app.services.predictions import generate_predictions
from app.services.season_state import Readiness


def _manifest(**overrides):
    payload = dict(
        model_name="two_stage_network",
        model_version="1.0.0",
        feature_names=FEATURE_NAMES,
        feature_version=FEATURE_VERSION,
        information_states=("preseason",),
        training_seasons=("2024/25",),
        training_cutoff="2025-05-25T00:00:00+00:00",
        seed=17,
        metrics={"mae": 1.13},
    )
    payload.update(overrides)
    return Manifest(**payload)


@pytest.fixture
def served_model(tmp_path, tiny_dataset):
    pytest.importorskip("torch")
    from app.models.network import NetworkCandidate

    model = NetworkCandidate(seed=17, epochs=1)
    model.fit(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())
    return load_artefact(tmp_path)


def test_no_artefact_yields_not_ready_rather_than_zero(seeded_history_db):
    result = generate_predictions(seeded_history_db, "2026/27", 1, served=None)
    assert result["readiness"] == Readiness.NOT_READY
    assert result["written"] == 0
    assert result["reason"]
    assert result["activates_when"]


def test_a_feature_mismatch_is_not_ready_not_a_wrong_number(
    seeded_history_db, tmp_path, tiny_dataset
):
    """A model trained on different features must not quietly predict.

    The artefact is saved correctly and then its manifest is rewritten to
    declare a different feature set, which is what a stale artefact left behind
    by an earlier feature version actually looks like on disk.
    """
    pytest.importorskip("torch")
    from app.models.network import NetworkCandidate

    model = NetworkCandidate(seed=17, epochs=1)
    model.fit(tiny_dataset)
    save_artefact(tmp_path, model, _manifest())

    stale = _manifest(feature_names=("a", "b"), feature_version="0.0.1")
    (tmp_path / "manifest.json").write_text(stale.to_json(), encoding="utf-8")
    served = load_artefact(tmp_path)

    result = generate_predictions(seeded_history_db, "2026/27", 1, served)
    assert result["readiness"] == Readiness.NOT_READY
    assert result["written"] == 0
    assert "feature set" in result["reason"]


def test_predictions_record_the_model_that_made_them(seeded_history_db, served_model):
    result = generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    assert result["readiness"] == Readiness.READY
    assert result["written"] > 0

    stored = seeded_history_db.scalars(select(Prediction)).all()
    assert stored
    for row in stored:
        assert row.model_name == "two_stage_network"
        assert row.model_version == "1.0.0"
        assert row.feature_version == FEATURE_VERSION
        assert row.information_state
        assert row.created_at is not None


def test_predictions_are_keyed_on_the_stable_player_code(
    seeded_history_db, served_model
):
    generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    stored = seeded_history_db.scalars(select(Prediction)).all()
    assert all(row.player_code is not None for row in stored)
    assert len({row.player_code for row in stored}) == len(stored)


def test_rerunning_replaces_rather_than_accumulates(seeded_history_db, served_model):
    first = generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    second = generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    stored = seeded_history_db.scalars(select(Prediction)).all()
    assert first["written"] == second["written"]
    assert len(stored) == second["written"], "re-running duplicated predictions"


def test_an_unavailable_distribution_is_null_not_zero(seeded_history_db, served_model):
    """Floor, median and ceiling are absent until a distributional model is
    served. Absent is null; zero would be a claim."""
    generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    for row in seeded_history_db.scalars(select(Prediction)).all():
        assert row.floor is None
        assert row.median is None
        assert row.ceiling is None


def test_expected_points_are_never_negative(seeded_history_db, served_model):
    generate_predictions(seeded_history_db, "2026/27", 1, served_model)
    for row in seeded_history_db.scalars(select(Prediction)).all():
        assert row.expected_points >= 0.0


def test_predictions_feature_is_registered_for_readiness():
    """An absent model must surface through the same registry as everything
    else, so the interface can say why rather than showing nothing."""
    from app.services.season_state import FEATURES

    assert "predictions" in FEATURES
    feature = FEATURES["predictions"]
    assert feature.required_inputs
    assert feature.activates_when
    assert feature.fallback
