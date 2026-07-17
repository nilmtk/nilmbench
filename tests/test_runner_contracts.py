import pandas as pd
import pytest

from nilmbench.data import LoadedSplit
from nilmbench.runner import _predict


class _Model:
    def __init__(self, chunks):
        self.chunks = chunks

    def disaggregate_chunk(self, mains):
        del mains
        return self.chunks


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
