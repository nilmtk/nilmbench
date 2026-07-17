"""Model registry and model-specific search spaces."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import Any, Callable


@dataclass(frozen=True)
class ModelEntry:
    name: str
    module: str
    class_name: str
    search_space: Callable[[Any], dict[str, Any]]

    def model_class(self) -> type:
        module = import_module(self.module)
        return getattr(module, self.class_name)


def _patchtst_space(trial: Any) -> dict[str, Any]:
    return {
        "sequence_length": trial.suggest_int("sequence_length", 49, 499, step=2),
        "n_epochs": trial.suggest_int("n_epochs", 10, 50),
        "batch_size": trial.suggest_categorical("batch_size", [128, 256, 512]),
        "learning_rate": trial.suggest_float(
            "learning_rate", 1e-5, 1e-2, log=True
        ),
    }


MODELS = {
    "PatchTST": ModelEntry(
        name="PatchTST",
        module="nilmtk_contrib.torch",
        class_name="PatchTST",
        search_space=_patchtst_space,
    ),
}


def get_model(name: str) -> ModelEntry:
    try:
        return MODELS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MODELS))
        raise ValueError(f"Unknown model {name!r}. Available: {available}") from exc
