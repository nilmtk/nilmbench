import pytest

from nilmbench.runner import metrics


def test_metrics_match_expected_mae_and_f1():
    result = metrics([0, 20, 20, 0], [0, 20, 0, 20])
    assert result["mae"] == pytest.approx(10.0)
    assert result["f1"] == pytest.approx(0.5)


def test_metrics_reject_empty_values():
    with pytest.raises(ValueError):
        metrics([], [])
