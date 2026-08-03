import pytest

from app.models.candidates import GradientBoostedCandidate


def test_a_candidate_refuses_to_predict_before_it_is_fitted(tiny_dataset):
    model = GradientBoostedCandidate()
    with pytest.raises(RuntimeError, match="not fitted"):
        model.predict_batch(tiny_dataset.x, tiny_dataset.mask)


def test_fitting_then_predicting_returns_one_value_per_row(tiny_dataset):
    model = GradientBoostedCandidate()
    model.fit(tiny_dataset)
    predictions = model.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    assert len(predictions) == len(tiny_dataset.y)
    assert all(isinstance(value, float) for value in predictions)


def test_training_is_reproducible_from_the_seed(tiny_dataset):
    first = GradientBoostedCandidate(seed=17)
    second = GradientBoostedCandidate(seed=17)
    first.fit(tiny_dataset)
    second.fit(tiny_dataset)
    assert first.predict_batch(tiny_dataset.x, tiny_dataset.mask) == (
        second.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    )


def test_predictions_are_clamped_at_zero(tiny_dataset):
    """FPL points can be negative, but a projection below zero is never the
    useful answer: the floor of the distribution carries that information."""
    model = GradientBoostedCandidate()
    model.fit(tiny_dataset)
    assert all(
        value >= 0.0
        for value in model.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    )


def test_fitting_an_empty_dataset_is_refused(tiny_dataset):
    empty = tiny_dataset.slice_seasons(["1999/00"])
    model = GradientBoostedCandidate()
    with pytest.raises(ValueError, match="no training rows"):
        model.fit(empty)


def test_the_mask_is_given_to_the_model_as_input(tiny_dataset):
    """The model must be able to learn that a masked feature is not evidence."""
    model = GradientBoostedCandidate()
    model.fit(tiny_dataset)
    assert model.n_inputs == 2 * len(tiny_dataset.feature_names)


def test_the_candidate_has_a_stable_name():
    assert GradientBoostedCandidate().name == "gradient_boosted"
