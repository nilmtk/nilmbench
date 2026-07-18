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
    supports_training_overrides: bool = True

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


def _no_search_space(trial: Any) -> dict[str, Any]:
    del trial
    return {}


def _entry(
    name: str,
    class_name: str,
    family: str,
    *,
    module: str = "nilmtk_contrib.torch",
    search_space: Callable[[Any], dict[str, Any]] = _standard_space,
    supports_training_overrides: bool = True,
) -> ModelEntry:
    return ModelEntry(
        name=name,
        module=module,
        class_name=class_name,
        family=family,
        search_space=search_space,
        supports_training_overrides=supports_training_overrides,
    )


MODELS = {
    entry.name: entry
    for entry in (
        _entry("BERT", "BERT", "transformer"),
        _entry("ConvLSTM", "ConvLSTM", "hybrid"),
        _entry("DAE", "DAE", "autoencoder"),
        _entry("DLinear", "DLinear", "decomposition-linear"),
        _entry("MSDC", "MSDC", "specialized"),
        _entry("ModernTCN", "ModernTCN", "convolutional"),
        _entry("NILMFormer", "NILMFormer", "transformer"),
        _entry("NILMMoE", "NILMMoE", "mixture-of-experts"),
        _entry("PatchTST", "PatchTST", "transformer"),
        _entry("Reformer", "Reformer", "transformer"),
        _entry("ResidualMoE", "ResidualMoE", "residual-mixture-of-experts"),
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
        _entry("SGN", "SGN", "subtask-gated"),
        _entry("TCN", "TCN", "convolutional"),
        _entry("TSMixer", "TSMixer", "mlp-mixer"),
        _entry("TimesNet", "TimesNet", "periodic-2d"),
        _entry("WindowGRU", "WindowGRU", "recurrent"),
        _entry(
            "Mean",
            "Mean",
            "statistical-baseline",
            module="nilmtk.disaggregate",
            search_space=_no_search_space,
            supports_training_overrides=False,
        ),
    )
}


def get_model(name: str) -> ModelEntry:
    try:
        return MODELS[name]
    except KeyError as exc:
        available = ", ".join(sorted(MODELS))
        raise ValueError(f"Unknown model {name!r}. Available: {available}") from exc
