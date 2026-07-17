import pandas as pd
import pytest

from nilmbench.data import LoadedSplit
from nilmbench.config import load_config
from nilmbench.runner import _model_size, _predict, run_benchmark


class _Model:
    def __init__(self, chunks):
        self.chunks = chunks

    def disaggregate_chunk(self, mains):
        del mains
        return self.chunks


class _Parameter:
    def __init__(self, count):
        self.count = count

    def numel(self):
        return self.count


class _Network:
    def __init__(self, *parameters):
        self._parameters = parameters

    def parameters(self):
        return iter(self._parameters)


def _split():
    return LoadedSplit(
        mains=[pd.DataFrame({"mains": [1.0, 2.0]}), pd.DataFrame({"mains": [3.0]})],
        appliances={},
        windows=[],
    )


def test_predict_preserves_final_partial_chunk():
    model = _Model(
        [pd.DataFrame({"fridge": [1.0, 2.0]}), pd.DataFrame({"fridge": [3.0]})]
    )

    prediction = _predict(model, _split(), ("fridge",))

    assert prediction["fridge"].tolist() == [1.0, 2.0, 3.0]


def test_model_size_includes_auxiliary_networks_without_double_counting():
    shared = _Parameter(10)
    main_only = _Parameter(20)
    attention_only = _Parameter(30)
    model = type(
        "Model",
        (),
        {
            "models": {"fridge": _Network(shared, main_only)},
            "att_models": {"fridge": _Network(shared, attention_only)},
        },
    )()

    assert _model_size(model) == 60


@pytest.mark.parametrize(
    ("chunks", "message"),
    [
        ([pd.DataFrame({"fridge": [1.0, 2.0]})], "number of prediction chunks"),
        (
            [
                pd.DataFrame({"fridge": [1.0]}),
                pd.DataFrame({"fridge": [3.0]}),
            ],
            "rows",
        ),
        (
            [
                pd.DataFrame({"wrong": [1.0, 2.0]}),
                pd.DataFrame({"wrong": [3.0]}),
            ],
            "columns",
        ),
        (
            [
                pd.DataFrame({"fridge": [1.0, float("nan")]}),
                pd.DataFrame({"fridge": [3.0]}),
            ],
            "non-finite",
        ),
    ],
)
def test_predict_rejects_dropped_or_corrupt_outputs(chunks, message):
    with pytest.raises(RuntimeError, match=message):
        _predict(_Model(chunks), _split(), ("fridge",))


@pytest.mark.parametrize("sequence_length", [False, 0, 1.5, "299"])
def test_run_rejects_invalid_sequence_length_before_data_access(
    sequence_length, tmp_path
):
    with pytest.raises(ValueError, match="positive integer"):
        run_benchmark(
            load_config(),
            "corrected-t1-redd",
            "PatchTST",
            42,
            tmp_path,
            sequence_length=sequence_length,
        )
