import pytest

from nilmbench.runner import metrics


def test_metrics_match_expected_mae_and_f1():
    result = metrics([0, 20, 20, 0], [0, 20, 0, 20])
    assert result["mae"] == pytest.approx(10.0)
    assert result["f1"] == pytest.approx(0.5)
    assert result["activation_threshold_watts"] == 10.0


def test_metrics_reject_empty_values():
    with pytest.raises(ValueError):
        metrics([], [])


@pytest.mark.parametrize(
    ("truth", "prediction", "message"),
    [
        ([0, 1], [0, 1, 2], "equally sized"),
        ([0, float("nan")], [0, 1], "finite"),
        ([0, 1], [0, float("inf")], "finite"),
    ],
)
def test_metrics_reject_misaligned_or_nonfinite_values(truth, prediction, message):
    with pytest.raises(ValueError, match=message):
        metrics(truth, prediction)


@pytest.mark.parametrize("threshold", [0, -1, float("nan"), float("inf")])
def test_metrics_reject_invalid_thresholds(threshold):
    with pytest.raises(ValueError, match="threshold"):
        metrics([0, 1], [0, 1], threshold=threshold)
