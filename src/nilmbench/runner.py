"""Deterministic real-data benchmark execution."""

from __future__ import annotations

import json
import math
import os
import statistics
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nilmbench._contracts import (
    HPO_SELECTION_PROTOCOL,
    HPO_TUNING_SEED,
    VALIDATION_PROTOCOL,
    canonical_digest,
    strict_json_loads,
    validate_persistent_hpo_provenance,
    validate_trial_record,
)
from nilmbench._io import atomic_write_text, immutable_write_text
from nilmbench.config import BenchmarkConfig, TaskConfig
from nilmbench.data import LoadedSplit, load_split, verify_dataset
from nilmbench.provenance import runtime_provenance
from nilmbench.registry import get_model


_VALIDATION_PROTOCOL = VALIDATION_PROTOCOL


def _canonical_digest(payload: Any) -> str:
    return canonical_digest(payload)


def metrics(truth: Any, prediction: Any, threshold: float = 10.0) -> dict[str, float]:
    """Compute MAE and thresholded F1 without an sklearn dependency."""
    import numpy as np

    if isinstance(threshold, bool) or not isinstance(threshold, (int, float)):
        raise ValueError("Activation threshold must be a positive finite number")
    if not math.isfinite(threshold) or threshold <= 0:
        raise ValueError("Activation threshold must be a positive finite number")
    actual = np.asarray(truth, dtype=np.float64).reshape(-1)
    predicted = np.asarray(prediction, dtype=np.float64).reshape(-1)
    if actual.size == 0 or predicted.size != actual.size:
        raise ValueError("Truth and prediction must be non-empty and equally sized")
    if not np.isfinite(actual).all() or not np.isfinite(predicted).all():
        raise ValueError("Truth and prediction must contain only finite values")
    mae = float(np.mean(np.abs(actual - predicted)))
    actual_on = actual >= threshold
    predicted_on = predicted >= threshold
    tp = int(np.sum(actual_on & predicted_on))
    fp = int(np.sum(~actual_on & predicted_on))
    fn = int(np.sum(actual_on & ~predicted_on))
    denominator = 2 * tp + fp + fn
    f1 = float(2 * tp / denominator) if denominator else 0.0
    return {"mae": mae, "f1": f1, "activation_threshold_watts": threshold}


def _configure_determinism(seed: int) -> None:
    """Make repeated runs on one backend deterministic or fail explicitly."""
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    import numpy as np
    import torch

    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)
    torch.use_deterministic_algorithms(True)
    torch.backends.cudnn.benchmark = False


def _normalization(split: LoadedSplit) -> tuple[float, float]:
    import numpy as np

    values = np.concatenate([frame.to_numpy().reshape(-1) for frame in split.mains])
    if values.size == 0 or not np.isfinite(values).all():
        raise ValueError("Training mains must contain finite values")
    std = float(np.std(values))
    mean = float(np.mean(values))
    if not math.isfinite(mean) or not math.isfinite(std):
        raise ValueError("Training mains statistics must be finite")
    return mean, max(std, 1.0)


def _model_size(model: Any) -> int | None:
    parameters: dict[int, Any] = {}
    for attribute in ("models", "att_models"):
        networks = getattr(model, attribute, None)
        if not isinstance(networks, dict):
            continue
        for network in networks.values():
            for parameter in network.parameters():
                parameters[id(parameter)] = parameter
    if not parameters:
        return None
    return sum(parameter.numel() for parameter in parameters.values())


def _patchtst_flops(model: Any) -> int | None:
    """Estimate one forward pass using dense multiply-adds (two FLOPs each)."""
    models = getattr(model, "models", None)
    if not models:
        return None
    network = next(iter(models.values()))
    required = ("num_patches", "patch_length", "position_embedding", "encoder")
    if not all(hasattr(network, name) for name in required):
        return None
    tokens = network.num_patches
    d_model = network.position_embedding.shape[-1]
    layers = len(network.encoder.layers)
    d_ff = network.encoder.layers[0].linear1.out_features
    patch_projection = 2 * tokens * network.patch_length * d_model
    attention_and_projections = (
        8 * tokens * d_model * d_model + 4 * tokens * tokens * d_model
    )
    feed_forward = 4 * tokens * d_model * d_ff
    head = 2 * tokens * d_model
    return int(
        patch_projection + layers * (attention_and_projections + feed_forward) + head
    )


def _predict(
    model: Any, split: LoadedSplit, expected_appliances: tuple[str, ...]
) -> dict[str, Any]:
    import numpy as np
    import pandas as pd

    chunks = list(model.disaggregate_chunk(split.mains))
    if len(chunks) != len(split.mains):
        raise RuntimeError(
            "Model returned a different number of prediction chunks than input chunks"
        )
    expected_columns = set(expected_appliances)
    for index, (mains, chunk) in enumerate(zip(split.mains, chunks, strict=True)):
        if not isinstance(chunk, pd.DataFrame):
            raise RuntimeError(f"Prediction chunk {index} is not a pandas DataFrame")
        if chunk.columns.has_duplicates or set(chunk.columns) != expected_columns:
            raise RuntimeError(
                f"Prediction chunk {index} columns must exactly match "
                f"{sorted(expected_columns)}"
            )
        if len(chunk) != len(mains):
            raise RuntimeError(
                f"Prediction chunk {index} has {len(chunk)} rows; expected {len(mains)}"
            )
        if not chunk.index.equals(mains.index):
            raise RuntimeError(
                f"Prediction chunk {index} index must exactly match its input chunk"
            )
        if not np.isfinite(chunk.to_numpy(dtype=float)).all():
            raise RuntimeError(f"Prediction chunk {index} contains non-finite values")
    predictions = pd.concat(chunks, axis=0, ignore_index=True)
    return {name: predictions[name].to_numpy() for name in predictions.columns}


def _truth(split: LoadedSplit, appliance: str) -> Any:
    import numpy as np

    return np.concatenate(
        [frame.to_numpy().reshape(-1) for frame in split.appliances[appliance]]
    )


def _index_value(value: Any) -> str:
    formatter = getattr(value, "isoformat", None)
    return formatter() if formatter is not None else str(value)


def _blocked_validation_split(
    loaded: LoadedSplit,
    validation_fraction: float = _VALIDATION_PROTOCOL["validation_fraction"],
) -> tuple[LoadedSplit, LoadedSplit, list[dict[str, Any]]]:
    """Hold out the chronological tail of every source training window."""
    if not 0 < validation_fraction < 1:
        raise ValueError("validation_fraction must be strictly between zero and one")
    if not loaded.mains:
        raise ValueError("Source training split has no windows")
    if any(len(frames) != len(loaded.mains) for frames in loaded.appliances.values()):
        raise ValueError("Source training appliance chunks are not window-aligned")

    train_mains: list[Any] = []
    validation_mains: list[Any] = []
    train_appliances = {name: [] for name in loaded.appliances}
    validation_appliances = {name: [] for name in loaded.appliances}
    partitions: list[dict[str, Any]] = []
    source_metadata = loaded.metadata()

    for index, mains in enumerate(loaded.mains):
        samples = len(mains)
        validation_samples = max(1, math.ceil(samples * validation_fraction))
        train_samples = samples - validation_samples
        if train_samples < 1:
            raise ValueError(
                "Each task.train window needs at least two aligned samples for "
                "blocked validation"
            )
        train_main = mains.iloc[:train_samples].copy()
        validation_main = mains.iloc[train_samples:].copy()
        train_mains.append(train_main)
        validation_mains.append(validation_main)
        for name, frames in loaded.appliances.items():
            frame = frames[index]
            if len(frame) != samples or not frame.index.equals(mains.index):
                raise ValueError(
                    f"Source training {name} chunk {index} is not aligned with mains"
                )
            train_appliances[name].append(frame.iloc[:train_samples].copy())
            validation_appliances[name].append(frame.iloc[train_samples:].copy())
        partitions.append(
            {
                "source_window": (
                    source_metadata[index] if index < len(source_metadata) else None
                ),
                "training": {
                    "samples": train_samples,
                    "actual_start": _index_value(train_main.index[0]),
                    "actual_end": _index_value(train_main.index[-1]),
                },
                "validation": {
                    "samples": validation_samples,
                    "actual_start": _index_value(validation_main.index[0]),
                    "actual_end": _index_value(validation_main.index[-1]),
                },
            }
        )

    return (
        LoadedSplit(train_mains, train_appliances, []),
        LoadedSplit(validation_mains, validation_appliances, []),
        partitions,
    )


def _run_validation_once(
    config: BenchmarkConfig,
    task: TaskConfig,
    model_name: str,
    seed: int,
    params: dict[str, Any],
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None,
) -> dict[str, Any]:
    """Score one HPO trial without loading or inspecting ``task.test``."""
    started = time.perf_counter()
    _configure_determinism(seed)
    groups = (
        [(name,) for name in appliances]
        if task.alignment_policy == "per_appliance"
        else [appliances]
    )
    scores: dict[str, dict[str, float]] = {}
    params_by_group: dict[str, dict[str, Any]] = {}
    partitions_by_group: dict[str, list[dict[str, Any]]] = {}
    elapsed_by_group: dict[str, float] = {}
    metric_policy = config.metric_policy(task.metric_policy)
    for group in groups:
        group_started = time.perf_counter()
        label = group[0] if len(group) == 1 else "joint"
        source_train = load_split(
            config, task, task.train, group, sample_period, max_samples
        )
        train, validation, partitions = _blocked_validation_split(source_train)
        mains_mean, mains_std = _normalization(train)
        resolved_params = {
            **params,
            "seed": seed,
            "mains_mean": mains_mean,
            "mains_std": mains_std,
        }
        model = get_model(model_name).model_class()(resolved_params)
        model.partial_fit(
            train.mains,
            [(name, train.appliances[name]) for name in group],
        )
        predictions = _predict(model, validation, group)
        for name in group:
            scores[name] = metrics(
                _truth(validation, name),
                predictions[name],
                threshold=metric_policy.threshold(name),
            )
        params_by_group[label] = resolved_params
        partitions_by_group[label] = partitions
        elapsed_by_group[label] = time.perf_counter() - group_started

    return {
        "params_by_alignment_group": params_by_group,
        "metrics": scores,
        "objective_mae": statistics.fmean(item["mae"] for item in scores.values()),
        "validation_protocol": dict(_VALIDATION_PROTOCOL),
        "validation_partitions": partitions_by_group,
        "elapsed_seconds_by_alignment_group": elapsed_by_group,
        "elapsed_seconds": time.perf_counter() - started,
    }


class _FixedSuggestionTrial:
    """Remove command-line overrides from the search space for every trial."""

    def __init__(self, trial: Any, fixed: dict[str, Any]) -> None:
        self._trial = trial
        self._fixed = fixed

    def __getattr__(self, name: str) -> Any:
        return getattr(self._trial, name)

    def suggest_categorical(self, name: str, choices: Any) -> Any:
        if name in self._fixed:
            return self._fixed[name]
        return self._trial.suggest_categorical(name, choices)

    def suggest_int(self, name: str, low: int, high: int, **kwargs: Any) -> int:
        if name in self._fixed:
            return self._fixed[name]
        return self._trial.suggest_int(name, low, high, **kwargs)

    def suggest_float(self, name: str, low: float, high: float, **kwargs: Any) -> float:
        if name in self._fixed:
            return self._fixed[name]
        return self._trial.suggest_float(name, low, high, **kwargs)


def _trial_parameters(
    model_entry: Any,
    trial: Any,
    *,
    epochs: int | None,
    sequence_length: int | None,
    device: str | None,
) -> dict[str, Any]:
    fixed: dict[str, Any] = {}
    if epochs is not None:
        fixed["n_epochs"] = epochs
    if sequence_length is not None:
        fixed["sequence_length"] = sequence_length
    params = model_entry.search_space(_FixedSuggestionTrial(trial, fixed))
    params.update(fixed)
    if device:
        params["device"] = device
    return params


def _run_once(
    config: BenchmarkConfig,
    task: TaskConfig,
    model_name: str,
    seed: int,
    params: dict[str, Any],
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None,
) -> dict[str, Any]:
    started = time.perf_counter()
    _configure_determinism(seed)
    groups = (
        [(name,) for name in appliances]
        if task.alignment_policy == "per_appliance"
        else [appliances]
    )
    scores: dict[str, dict[str, float]] = {}
    params_by_group: dict[str, dict[str, Any]] = {}
    train_windows: dict[str, Any] = {}
    test_windows: dict[str, Any] = {}
    parameter_counts: dict[str, int | None] = {}
    flops: dict[str, int | None] = {}
    elapsed_by_group: dict[str, float] = {}
    peak_accelerator_memory: dict[str, int | None] = {}
    metric_policy = config.metric_policy(task.metric_policy)
    for group in groups:
        group_started = time.perf_counter()
        label = group[0] if len(group) == 1 else "joint"
        train = load_split(config, task, task.train, group, sample_period, max_samples)
        test = load_split(config, task, task.test, group, sample_period, max_samples)
        mains_mean, mains_std = _normalization(train)
        resolved_params = {
            **params,
            "seed": seed,
            "mains_mean": mains_mean,
            "mains_std": mains_std,
        }
        model = get_model(model_name).model_class()(resolved_params)
        tracks_cuda_memory = (
            getattr(getattr(model, "device", None), "type", None) == "cuda"
        )
        if tracks_cuda_memory:
            import torch

            torch.cuda.reset_peak_memory_stats(model.device)
        model.partial_fit(
            train.mains,
            [(name, train.appliances[name]) for name in group],
        )
        predictions = _predict(model, test, group)
        for name in group:
            scores[name] = metrics(
                _truth(test, name),
                predictions[name],
                threshold=metric_policy.threshold(name),
            )
        params_by_group[label] = resolved_params
        train_windows[label] = train.metadata()
        test_windows[label] = test.metadata()
        parameter_counts[label] = _model_size(model)
        flops[label] = _patchtst_flops(model)
        if tracks_cuda_memory:
            peak_accelerator_memory[label] = torch.cuda.max_memory_allocated(
                model.device
            )
        else:
            peak_accelerator_memory[label] = None
        elapsed_by_group[label] = time.perf_counter() - group_started

    return {
        "params_by_alignment_group": params_by_group,
        "metrics": scores,
        "objective_mae": statistics.fmean(item["mae"] for item in scores.values()),
        "trainable_parameters": parameter_counts,
        "inference_flops_estimate": flops,
        "flops_method": "dense multiply-add estimate; normalization and activations excluded",
        "elapsed_seconds_by_alignment_group": elapsed_by_group,
        "peak_accelerator_memory_bytes": peak_accelerator_memory,
        "train_windows": train_windows,
        "test_windows": test_windows,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _study_spec(
    config: BenchmarkConfig,
    task: TaskConfig,
    model_name: str,
    model_entry: Any,
    tuning_seed: int,
    appliances: tuple[str, ...],
    sample_period: int,
    max_samples: int | None,
    epochs: int | None,
    sequence_length: int | None,
    device: str | None,
    provenance: dict[str, Any],
    source_dataset_identities: dict[str, dict[str, Any]],
    optuna_version: str,
) -> dict[str, Any]:
    """Describe every scientific input that makes an Optuna study resumable."""
    return {
        "identity_schema": "nilmbench.optuna-study.v2",
        "runner": {
            "git_sha": provenance["nilmbench_git_sha"],
            "git_dirty": provenance["nilmbench_git_dirty"],
        },
        "contrib": {
            "git_sha": provenance["nilmtk_contrib_git_sha"],
            "git_dirty": provenance["nilmtk_contrib_git_dirty"],
            "version": provenance["nilmtk_contrib_version"],
            "model_module": model_entry.module,
            "model_class": model_entry.class_name,
        },
        "container": {
            "image": provenance["container_image"],
            "digest": provenance["container_digest"],
        },
        "device": {
            "requested": device or "auto",
            "cpu": provenance["cpu"],
            "gpu": provenance["gpu"],
            "torch": provenance["torch"],
            "cuda_runtime": provenance["cuda_runtime"],
            "cuda_available": provenance["cuda_available"],
        },
        "protocol": {
            "task_id": task.id,
            "task_family": task.family,
            "task_profile": task.profile,
            "task_config_sha256": config.digest(task.id),
            "target_data_access": task.target_data_access,
            "model": model_name,
            "selection_protocol": HPO_SELECTION_PROTOCOL,
            "tuning_seed": tuning_seed,
            "appliances": list(appliances),
            "sample_period": sample_period,
            "max_samples_per_window": max_samples,
            "epochs_override": epochs,
            "sequence_length_override": sequence_length,
            "alignment_policy": task.alignment_policy,
            "metric_policy": task.metric_policy,
            "validation": dict(_VALIDATION_PROTOCOL),
            "optimization": {
                "library": "optuna",
                "version": optuna_version,
                "direction": "minimize",
                "sampler": "TPESampler",
                "sampler_seed": tuning_seed,
            },
        },
        "source_dataset_identities": source_dataset_identities,
    }


def _study_identity(
    task_id: str, model_name: str, tuning_seed: int, spec: dict[str, Any]
) -> tuple[str, str]:
    digest = _canonical_digest(spec)
    return digest, f"{task_id}--{model_name}--tune-seed{tuning_seed}--{digest}"


def _assert_resume_compatible(study: Any, spec: dict[str, Any], digest: str) -> None:
    identity = {"sha256": digest, "spec": spec}
    stored = study.user_attrs.get("nilmbench_study_identity")
    if stored is None:
        if study.trials:
            raise RuntimeError(
                "Refusing to resume an Optuna study without a NILMbench v2 identity"
            )
        study.set_user_attr("nilmbench_study_identity", identity)
        return
    if stored != identity:
        raise RuntimeError(
            "Refusing to resume an Optuna study with an incompatible scientific "
            "identity"
        )


def _trial_record_path(audit_dir: Path, trial_number: int) -> Path:
    return audit_dir / f"trial-{trial_number:06d}.json"


def _trial_record(
    study_name: str,
    study_digest: str,
    study_spec: dict[str, Any],
    trial: Any,
    effective_params: dict[str, Any],
    validation_result: dict[str, Any],
) -> dict[str, Any]:
    payload = {
        "schema_version": "1.0",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "state": "COMPLETE",
        "study_name": study_name,
        "study_identity_sha256": study_digest,
        "study_spec": study_spec,
        "trial_number": trial.number,
        "parameters": {
            "suggested": dict(trial.params),
            "effective": effective_params,
            "resolved_by_alignment_group": validation_result[
                "params_by_alignment_group"
            ],
        },
        "validation": {
            "protocol": validation_result["validation_protocol"],
            "partitions": validation_result["validation_partitions"],
            "metrics": validation_result["metrics"],
            "objective_mae": validation_result["objective_mae"],
            "elapsed_seconds": validation_result["elapsed_seconds"],
            "elapsed_seconds_by_alignment_group": validation_result[
                "elapsed_seconds_by_alignment_group"
            ],
        },
    }
    return {**payload, "record_id": _canonical_digest(payload)}


def _write_trial_record(path: Path, record: dict[str, Any]) -> None:
    immutable_write_text(
        path,
        json.dumps(record, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def _load_completed_trial_records(
    study: Any,
    complete_state: Any,
    audit_dir: Path,
    study_digest: str,
    study_spec: dict[str, Any],
) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for trial in sorted(study.trials, key=lambda item: item.number):
        if trial.state != complete_state:
            continue
        path = _trial_record_path(audit_dir, trial.number)
        try:
            record = strict_json_loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError, ValueError) as exc:
            raise RuntimeError(
                f"Completed trial {trial.number} has no valid immutable JSON audit "
                f"record at {path}"
            ) from exc
        try:
            validate_trial_record(
                record,
                study_name=study.study_name,
                study_digest=study_digest,
                study_spec=study_spec,
                expected_trial_number=trial.number,
                expected_suggestions=trial.params,
                expected_objective=trial.value,
            )
            if trial.user_attrs.get("audit_record_id") != record["record_id"]:
                raise ValueError("persistent trial is not bound to its audit record")
        except (KeyError, TypeError, ValueError) as exc:
            raise RuntimeError(
                f"Completed trial {trial.number} has an incompatible or modified "
                "JSON audit record"
            ) from exc
        records.append(record)
    return records


def _write_result(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    csv_rows = ["appliance,mae,f1,activation_threshold_watts"]
    for appliance, values in result["run"]["metrics"].items():
        csv_rows.append(
            f"{appliance},{values['mae']},{values['f1']},"
            f"{values['activation_threshold_watts']}"
        )
    atomic_write_text(output_dir / "metrics.csv", "\n".join(csv_rows) + "\n")
    atomic_write_text(
        output_dir / "result.json",
        json.dumps(result, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )


def run_benchmark(
    config: BenchmarkConfig,
    task_id: str,
    model_name: str,
    seed: int,
    output_root: Path,
    *,
    trials: int = 0,
    appliances: tuple[str, ...] | None = None,
    sample_period: int | None = None,
    max_samples: int | None = None,
    epochs: int | None = None,
    device: str | None = None,
    sequence_length: int | None = None,
) -> Path:
    task = config.task(task_id)
    model_entry = get_model(model_name)
    chosen_appliances = appliances or task.appliances
    if len(set(chosen_appliances)) != len(chosen_appliances):
        raise ValueError("Appliances must not contain duplicates")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    unknown = set(chosen_appliances) - set(task.appliances)
    if unknown:
        raise ValueError(
            f"Task {task.id} does not include: {', '.join(sorted(unknown))}"
        )
    if sample_period is not None and (
        isinstance(sample_period, bool)
        or not isinstance(sample_period, int)
        or sample_period <= 0
    ):
        raise ValueError("sample_period must be a positive integer")
    resolved_period = sample_period if sample_period is not None else task.sample_period
    if max_samples is not None and (
        isinstance(max_samples, bool)
        or not isinstance(max_samples, int)
        or max_samples <= 0
    ):
        raise ValueError("max_samples must be a positive integer")
    if epochs is not None and (
        isinstance(epochs, bool) or not isinstance(epochs, int) or epochs <= 0
    ):
        raise ValueError("epochs must be a positive integer")
    if sequence_length is not None and (
        isinstance(sequence_length, bool)
        or not isinstance(sequence_length, int)
        or sequence_length <= 0
    ):
        raise ValueError("sequence_length must be a positive integer")
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 0:
        raise ValueError("trials must be a non-negative integer")
    if not model_entry.supports_training_overrides and (
        trials
        or epochs is not None
        or sequence_length is not None
        or device is not None
    ):
        raise ValueError(
            f"{model_name} does not accept trials, epochs, sequence length, or device"
        )
    if model_entry.supports_training_overrides:
        base_params: dict[str, Any] = {
            "sequence_length": sequence_length if sequence_length is not None else 99,
            "n_epochs": epochs if epochs is not None else 10,
            "batch_size": 128,
            "learning_rate": 1e-3,
        }
        if device:
            base_params["device"] = device
    else:
        base_params = {}

    root = Path(__file__).resolve().parents[2]
    provenance = runtime_provenance(root)
    if trials > 0:
        validate_persistent_hpo_provenance(provenance)
    all_dataset_names = sorted({w.dataset for w in (*task.train, *task.test)})
    source_dataset_names = sorted({window.dataset for window in task.train})
    dataset_identities: dict[str, dict[str, Any]] | None = None
    source_dataset_identities: dict[str, dict[str, Any]] = {}
    if trials > 0:
        source_dataset_identities = {
            name: asdict(verify_dataset(config.datasets[name]))
            for name in source_dataset_names
        }
    else:
        dataset_identities = {
            name: asdict(verify_dataset(config.datasets[name]))
            for name in all_dataset_names
        }
    timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
    output_dir = output_root / f"{task_id}--{model_name}--seed{seed}--{timestamp}"
    trial_metadata: dict[str, Any] | None = None
    if trials > 0:
        try:
            import optuna
        except ModuleNotFoundError as exc:
            raise RuntimeError("Optuna trials require nilmbench[benchmark]") from exc
        storage_dir = output_root / "optuna"
        storage_dir.mkdir(parents=True, exist_ok=True)
        study_spec = _study_spec(
            config,
            task,
            model_name,
            model_entry,
            HPO_TUNING_SEED,
            chosen_appliances,
            resolved_period,
            max_samples,
            epochs,
            sequence_length,
            device,
            provenance,
            source_dataset_identities,
            optuna.__version__,
        )
        study_digest, study_name = _study_identity(
            task_id, model_name, HPO_TUNING_SEED, study_spec
        )
        storage_path = storage_dir / f"{study_name}.sqlite3"
        storage = f"sqlite:///{storage_path.resolve()}"
        audit_dir = storage_dir / study_name / "trials"
        study = optuna.create_study(
            study_name=study_name,
            direction="minimize",
            sampler=optuna.samplers.TPESampler(seed=HPO_TUNING_SEED),
            storage=storage,
            load_if_exists=True,
        )
        _assert_resume_compatible(study, study_spec, study_digest)
        completed_records = _load_completed_trial_records(
            study,
            optuna.trial.TrialState.COMPLETE,
            audit_dir,
            study_digest,
            study_spec,
        )

        def objective(trial: Any) -> float:
            params = _trial_parameters(
                model_entry,
                trial,
                epochs=epochs,
                sequence_length=sequence_length,
                device=device,
            )
            trial_result = _run_validation_once(
                config,
                task,
                model_name,
                HPO_TUNING_SEED,
                params,
                chosen_appliances,
                resolved_period,
                max_samples,
            )
            record = _trial_record(
                study_name,
                study_digest,
                study_spec,
                trial,
                params,
                trial_result,
            )
            _write_trial_record(_trial_record_path(audit_dir, trial.number), record)
            trial.set_user_attr("audit_record_id", record["record_id"])
            return trial_result["objective_mae"]

        study.optimize(objective, n_trials=max(0, trials - len(completed_records)))
        completed_records = _load_completed_trial_records(
            study,
            optuna.trial.TrialState.COMPLETE,
            audit_dir,
            study_digest,
            study_spec,
        )
        base_params.update(study.best_params)
        if epochs is not None:
            base_params["n_epochs"] = epochs
        if sequence_length is not None:
            base_params["sequence_length"] = sequence_length
        if device:
            base_params["device"] = device
        trial_metadata = {
            "study_name": study.study_name,
            "study_spec": study_spec,
            "study_digest": study_digest,
            "selection_protocol": HPO_SELECTION_PROTOCOL,
            "tuning_seed": HPO_TUNING_SEED,
            "coordination": {
                "backend": "sqlite",
                "storage": storage_path.relative_to(output_root).as_posix(),
                "scientific_source_of_truth": False,
            },
            "completed_trials": len(completed_records),
            "best_value": study.best_value,
            "best_params": dict(base_params),
            "optuna_best_suggestions": study.best_params,
            "trial_record_files": [
                _trial_record_path(audit_dir, record["trial_number"])
                .relative_to(output_root)
                .as_posix()
                for record in completed_records
            ],
            "trial_records": completed_records,
        }

    if dataset_identities is None:
        dataset_identities = {
            name: asdict(verify_dataset(config.datasets[name]))
            for name in all_dataset_names
        }
    run = _run_once(
        config,
        task,
        model_name,
        seed,
        base_params,
        chosen_appliances,
        resolved_period,
        max_samples,
    )
    model_params_sha256 = _canonical_digest(base_params)
    result = {
        "schema_version": "1.2",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "task": asdict(task),
        "task_config_sha256": config.digest(task_id),
        "metric_policy": asdict(config.metric_policy(task.metric_policy)),
        "dataset_manifests": {
            name: asdict(config.datasets[name])
            for name in sorted({w.dataset for w in (*task.train, *task.test)})
        },
        "dataset_identities": dataset_identities,
        "model": model_name,
        "model_spec": {
            "module": model_entry.module,
            "class_name": model_entry.class_name,
            "family": model_entry.family,
        },
        "model_params": base_params,
        "model_params_sha256": model_params_sha256,
        "seed": seed,
        "sample_period": resolved_period,
        "appliances": chosen_appliances,
        "max_samples_per_window": max_samples,
        "run_scope": "smoke"
        if max_samples is not None
        or epochs is not None
        or chosen_appliances != task.appliances
        else "full",
        "protocol_overrides": {
            "max_samples_per_window": max_samples,
            "epochs": epochs,
            "appliances": None
            if chosen_appliances == task.appliances
            else chosen_appliances,
            "sample_period": None
            if resolved_period == task.sample_period
            else resolved_period,
            "sequence_length": sequence_length,
            "model_selection": None
            if trial_metadata is None
            else {
                "method": "optuna-tpe",
                "selection_protocol": HPO_SELECTION_PROTOCOL,
                "tuning_seed": HPO_TUNING_SEED,
                "study_identity_sha256": trial_metadata["study_digest"],
                "completed_trials": trial_metadata["completed_trials"],
                "validation_protocol": _VALIDATION_PROTOCOL["id"],
                "selected_parameters": trial_metadata["best_params"],
            },
        },
        "study": trial_metadata,
        "runtime": provenance,
        "run": run,
    }
    if not math.isfinite(run["objective_mae"]):
        raise RuntimeError("Benchmark produced a non-finite objective")
    result["result_id"] = _canonical_digest(result)
    _write_result(result, output_dir)
    return output_dir
