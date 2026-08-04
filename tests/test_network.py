import pytest

torch = pytest.importorskip("torch")

from app.models.network import NetworkCandidate, TwoStageNet  # noqa: E402

N_FEATURES = 36


def test_appearance_probabilities_sum_to_one():
    net = TwoStageNet(n_features=N_FEATURES)
    out = net(torch.zeros(4, N_FEATURES), torch.ones(4, N_FEATURES))
    probabilities = torch.softmax(out["appearance_logits"], dim=-1)
    assert probabilities.shape == (4, 3)
    assert torch.allclose(probabilities.sum(dim=-1), torch.ones(4), atol=1e-5)


def test_expected_minutes_stay_within_a_football_match():
    net = TwoStageNet(n_features=N_FEATURES)
    out = net(torch.randn(8, N_FEATURES) * 5, torch.ones(8, N_FEATURES))
    for key in ("minutes_if_start", "minutes_if_sub"):
        assert (out[key] >= 0).all(), f"{key} went negative"
        assert (out[key] <= 90).all(), f"{key} exceeded a full match"


def test_event_rates_are_non_negative():
    net = TwoStageNet(n_features=N_FEATURES)
    out = net(torch.randn(8, N_FEATURES) * 5, torch.ones(8, N_FEATURES))
    assert (out["event_rates"] >= 0).all()
    assert out["event_rates"].shape == (8, 6)


def test_a_masked_feature_cannot_change_the_output():
    """Masked means unavailable. Whatever sits behind the mask is not evidence.

    This is the load-bearing property of the whole masked-model design: if a
    value behind a zero mask could reach a weight, preseason predictions would
    silently depend on data that does not exist yet.
    """
    net = TwoStageNet(n_features=N_FEATURES).eval()
    mask = torch.ones(1, N_FEATURES)
    mask[0, 5] = 0.0

    quiet = torch.zeros(1, N_FEATURES)
    loud = torch.zeros(1, N_FEATURES)
    loud[0, 5] = 99.0

    with torch.no_grad():
        a = net(quiet, mask)
        b = net(loud, mask)
    for key in ("appearance_logits", "minutes_if_start", "event_rates"):
        assert torch.allclose(a[key], b[key], atol=1e-6), (
            f"{key} changed when a masked feature changed"
        )


def test_an_unmasked_feature_does_change_the_output():
    """The counterpart: the mask must not simply disable everything."""
    net = TwoStageNet(n_features=N_FEATURES).eval()
    mask = torch.ones(1, N_FEATURES)
    quiet = torch.zeros(1, N_FEATURES)
    loud = torch.zeros(1, N_FEATURES)
    loud[0, 5] = 99.0

    with torch.no_grad():
        a = net(quiet, mask)["appearance_logits"]
        b = net(loud, mask)["appearance_logits"]
    assert not torch.allclose(a, b, atol=1e-6)


def test_training_is_reproducible_from_the_seed(tiny_dataset):
    first = NetworkCandidate(seed=17, epochs=2)
    second = NetworkCandidate(seed=17, epochs=2)
    first.fit(tiny_dataset)
    second.fit(tiny_dataset)
    a = first.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    b = second.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    assert all(abs(x - y) < 1e-6 for x, y in zip(a, b))


def test_a_candidate_refuses_to_predict_before_it_is_fitted(tiny_dataset):
    with pytest.raises(RuntimeError, match="not fitted"):
        NetworkCandidate().predict_batch(tiny_dataset.x, tiny_dataset.mask)


def test_fitting_an_empty_dataset_is_refused(tiny_dataset):
    empty = tiny_dataset.slice_seasons(["1999/00"])
    with pytest.raises(ValueError, match="no training rows"):
        NetworkCandidate().fit(empty)


def test_distribution_is_ordered_floor_median_ceiling(tiny_dataset):
    model = NetworkCandidate(seed=17, epochs=2)
    model.fit(tiny_dataset)
    quantiles = model.predict_distribution(tiny_dataset.x, tiny_dataset.mask)
    assert len(quantiles) == len(tiny_dataset.y)
    for floor, median, ceiling in quantiles:
        assert floor <= median <= ceiling


def test_expected_minutes_and_start_probability_are_exposed(tiny_dataset):
    """These are first-class outputs, not by-products: the interface has
    columns for them and the readiness registry declares them as features."""
    model = NetworkCandidate(seed=17, epochs=2)
    model.fit(tiny_dataset)
    minutes, start_probability = model.predict_minutes(
        tiny_dataset.x, tiny_dataset.mask
    )
    assert len(minutes) == len(tiny_dataset.y)
    assert all(0.0 <= value <= 90.0 for value in minutes)
    assert all(0.0 <= value <= 1.0 for value in start_probability)


def test_predictions_are_never_negative(tiny_dataset):
    model = NetworkCandidate(seed=17, epochs=2)
    model.fit(tiny_dataset)
    assert all(
        value >= 0.0
        for value in model.predict_batch(tiny_dataset.x, tiny_dataset.mask)
    )


def test_the_candidate_has_a_stable_name():
    assert NetworkCandidate().name == "two_stage_network"


def test_dropout_is_active_in_training_and_inert_in_evaluation():
    """Dropout must never perturb a served prediction."""
    net = TwoStageNet(n_features=N_FEATURES, hidden=16, dropout=0.5)
    x, mask = torch.randn(4, N_FEATURES), torch.ones(4, N_FEATURES)

    net.eval()
    with torch.no_grad():
        first = net(x, mask)["appearance_logits"]
        second = net(x, mask)["appearance_logits"]
    assert torch.allclose(first, second), "eval-mode output is not deterministic"

    net.train()
    torch.manual_seed(1)
    a = net(x, mask)["appearance_logits"]
    torch.manual_seed(2)
    b = net(x, mask)["appearance_logits"]
    assert not torch.allclose(a, b), "dropout had no effect in training mode"


def test_the_default_network_is_small():
    """Capacity, not the loss function, was what decided ranking.

    A 40-iteration depth-2 tree outperformed a 200-iteration unlimited-depth
    one on identical data, so the network default should be constrained too.
    """
    model = NetworkCandidate()
    assert model.hidden <= 64
    assert model.dropout > 0.0
    assert model.weight_decay > 0.0


def test_early_stopping_halts_before_the_epoch_cap(tiny_dataset):
    model = NetworkCandidate(seed=17, epochs=200, patience=2, hidden=16)
    model.fit(tiny_dataset)
    assert model.epochs_run < 200, "training ignored early stopping"
    assert model.epochs_run > 0


def test_a_masked_feature_still_cannot_reach_a_weight_after_regularising():
    """The masked-feature guarantee must survive the architecture change."""
    net = TwoStageNet(n_features=N_FEATURES, hidden=16, dropout=0.3).eval()
    mask = torch.ones(1, N_FEATURES)
    mask[0, 5] = 0.0
    quiet, loud = torch.zeros(1, N_FEATURES), torch.zeros(1, N_FEATURES)
    loud[0, 5] = 99.0
    with torch.no_grad():
        assert torch.allclose(
            net(quiet, mask)["appearance_logits"],
            net(loud, mask)["appearance_logits"],
            atol=1e-6,
        )
