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
    family: str
    search_space: Callable[[Any], dict[str, Any]]

    def model_class(self) -> type:
        module = import_module(self.module)
        return getattr(module, self.class_name)


def _standard_space(trial: Any) -> dict[str, Any]:
    return {
        "sequence_length": trial.suggest_categorical(
            "sequence_length", [99, 299, 599]
        ),
        "n_epochs": trial.suggest_int("n_epochs", 10, 50),
        "batch_size": trial.suggest_categorical("batch_size", [128, 256, 512]),
        "learning_rate": trial.suggest_float(
            "learning_rate", 1e-5, 1e-2, log=True
        ),
}


def _entry(name: str, class_name: str, family: str) -> ModelEntry:
    return ModelEntry(
        name=name,
        module="nilmtk_contrib.torch",
        class_name=class_name,
        family=family,
        search_space=_standard_space,
    )


MODELS = {
    entry.name: entry
    for entry in (
        _entry("BERT", "BERT", "transformer"),
        _entry("ConvLSTM", "ConvLSTM", "hybrid"),
        _entry("DAE", "DAE", "autoencoder"),
        _entry("MSDC", "MSDC", "specialized"),
        _entry("NILMFormer", "NILMFormer", "transformer"),
        _entry("PatchTST", "PatchTST", "transformer"),
        _entry("Reformer", "Reformer", "transformer"),
        _entry("ResNet", "ResNet", "convolutional"),
        _entry(
            "ResNetClassification",
            "ResNet_classification",
            "convolutional-classification",
        ),
        _entry("RNN", "RNN", "recurrent"),
        _entry("RNNAttention", "RNN_attention", "recurrent-attention"),
        _entry(
            "RNNAttentionClassification",
            "RNN_attention_classification",
            "recurrent-attention-classification",
        ),
        _entry("Seq2Point", "Seq2PointTorch", "convolutional"),
        _entry("Seq2Seq", "Seq2Seq", "recurrent"),
        _entry("TCN", "TCN", "convolutional"),
        _entry("WindowGRU", "WindowGRU", "recurrent"),
    )
}


def get_model(name: str) -> ModelEntry:
    try:
        return MODELS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MODELS))
        raise ValueError(f"Unknown model {name!r}. Available: {available}") from exc
