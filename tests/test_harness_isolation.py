"""The harness must not let a candidate see the season it is scored on.

Every conclusion drawn from `evaluate` depends on this. If a trainable were
fitted on the test season, a memorising model would look excellent and the
ship gate would wave through something worthless. These tests check the
property directly rather than trusting that the slicing is right.
"""

from app.models.dataset import Dataset
from app.models.evaluation import evaluate_fold_from_dataset, walk_forward_folds
from app.models.features import FEATURE_NAMES


class _RecordingCandidate:
    """Records exactly which rows it was fitted on."""

    name = "recording"

    def __init__(self) -> None:
        self.fitted_seasons: set[str] = set()
        self.fitted_rows = 0
        self.predicted_rows = 0

    def fit(self, dataset: Dataset) -> None:
        self.fitted_seasons = set(dataset.season)
        self.fitted_rows = len(dataset.y)

    def predict_batch(self, x, mask):
        self.predicted_rows = len(x)
        return [0.0] * len(x)


class _LeakDetector:
    """Returns the true label if it ever saw the row during fitting.

    A perfect score from this model is proof of leakage, not of skill.
    """

    name = "leak_detector"

    def __init__(self) -> None:
        self._memorised: dict[tuple[float, ...], float] = {}

    def fit(self, dataset: Dataset) -> None:
        self._memorised = {
            tuple(row): label for row, label in zip(dataset.x, dataset.y)
        }

    def predict_batch(self, x, mask):
        return [self._memorised.get(tuple(row), 0.0) for row in x]


def _dataset(seasons_and_labels) -> Dataset:
    x, mask, y, season, gameweek, code, minutes, started = [], [], [], [], [], [], [], []
    for index, (s, label) in enumerate(seasons_and_labels):
        x.append([float(index)] + [0.0] * (len(FEATURE_NAMES) - 1))
        mask.append([1.0] * len(FEATURE_NAMES))
        y.append(float(label))
        season.append(s)
        gameweek.append(index % 38 + 1)
        code.append(1000 + index)
        minutes.append(90.0)
        started.append(True)
    return Dataset(
        x=x, mask=mask, y=y, minutes=minutes, started=started, season=season,
        gameweek=gameweek, player_code=code, information_state="in_season",
        feature_names=FEATURE_NAMES,
    )


def test_a_candidate_is_never_fitted_on_the_test_season():
    data = _dataset(
        [("2022/23", 1)] * 5 + [("2023/24", 2)] * 5 + [("2024/25", 3)] * 5
    )
    candidate = _RecordingCandidate()

    evaluate_fold_from_dataset(
        data, ["2022/23", "2023/24"], "2024/25", models=(), trainables=(candidate,)
    )

    assert candidate.fitted_seasons == {"2022/23", "2023/24"}
    assert "2024/25" not in candidate.fitted_seasons
    assert candidate.fitted_rows == 10
    assert candidate.predicted_rows == 5


def test_a_memorising_model_cannot_score_perfectly():
    """The strongest possible leak check: a model that returns the label for
    any row it has seen. If the harness is sound it has seen none of them."""
    data = _dataset(
        [("2022/23", 1)] * 5 + [("2023/24", 2)] * 5 + [("2024/25", 9)] * 5
    )
    detector = _LeakDetector()

    result = evaluate_fold_from_dataset(
        data, ["2022/23", "2023/24"], "2024/25", models=(), trainables=(detector,)
    )

    scores = result.scores["leak_detector"]
    # Every test label is 9 and the detector can only answer 0, so a sound
    # harness produces an error of exactly 9.
    assert scores["mae"] == 9.0, (
        f"leak detector scored MAE {scores['mae']}, meaning it recognised test "
        "rows from training: the harness is leaking"
    )


def test_fold_metadata_matches_what_was_actually_scored():
    data = _dataset([("2022/23", 1)] * 4 + [("2023/24", 2)] * 6)

    result = evaluate_fold_from_dataset(
        data, ["2022/23"], "2023/24", models=(), trainables=(_RecordingCandidate(),)
    )

    assert result.train_seasons == ["2022/23"]
    assert result.test_season == "2023/24"
    assert result.n_examples == 6


def test_every_generated_fold_trains_only_on_earlier_seasons():
    seasons = ["2020/21", "2021/22", "2022/23", "2023/24", "2024/25"]
    data = _dataset([(s, 1) for s in seasons for _ in range(3)])

    for train_seasons, test_season in walk_forward_folds(seasons):
        candidate = _RecordingCandidate()
        evaluate_fold_from_dataset(
            data, train_seasons, test_season, models=(), trainables=(candidate,)
        )
        assert test_season not in candidate.fitted_seasons
        assert all(s < test_season for s in candidate.fitted_seasons)
