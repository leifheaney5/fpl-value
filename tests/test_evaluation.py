from app.models.evaluation import season_order, walk_forward_folds


def test_seasons_sort_chronologically_not_lexically():
    assert season_order(["2024/25", "2016/17", "2020/21"]) == [
        "2016/17", "2020/21", "2024/25",
    ]


def test_season_order_deduplicates():
    assert season_order(["2024/25", "2024/25", "2016/17"]) == ["2016/17", "2024/25"]


def test_folds_never_train_on_the_future():
    """The guarantee that makes a backtest honest."""
    seasons = ["2021/22", "2022/23", "2023/24", "2024/25"]
    folds = walk_forward_folds(seasons)
    assert folds

    for train, test in folds:
        assert train, "a fold must have training seasons"
        for season in train:
            assert season_order([season, test]) == [season, test], (
                f"fold trains on {season} to predict {test}: future leakage"
            )


def test_the_first_season_is_never_a_test_season():
    folds = walk_forward_folds(["2021/22", "2022/23", "2023/24"])
    assert "2021/22" not in [test for _, test in folds]


def test_the_training_window_expands():
    folds = walk_forward_folds(["2021/22", "2022/23", "2023/24", "2024/25"])
    assert [len(train) for train, _ in folds] == [1, 2, 3]
    assert [test for _, test in folds] == ["2022/23", "2023/24", "2024/25"]


def test_a_single_season_yields_no_folds():
    assert walk_forward_folds(["2024/25"]) == []
    assert walk_forward_folds([]) == []


def test_minimum_training_seasons_is_respected():
    folds = walk_forward_folds(
        ["2021/22", "2022/23", "2023/24", "2024/25"], min_train_seasons=2
    )
    assert all(len(train) >= 2 for train, _ in folds)
    assert [test for _, test in folds] == ["2023/24", "2024/25"]
