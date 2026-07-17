"""Deterministic real-data benchmark execution."""

from __future__ import annotations

import hashlib
import json
import math
import os
import statistics
import tempfile
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from nilmbench.config import BenchmarkConfig, TaskConfig
from nilmbench.data import LoadedSplit, load_split, verify_dataset
from nilmbench.provenance import runtime_provenance
from nilmbench.registry import get_model


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


def _model_size(model: Any) -> int:
    return sum(
        parameter.numel()
        for network in model.models.values()
        for parameter in network.parameters()
    )


def _patchtst_flops(model: Any) -> int | None:
    """Estimate one forward pass using dense multiply-adds (two FLOPs each)."""
    if not model.models:
        return None
    network = next(iter(model.models.values()))
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
        patch_projection
        + layers * (attention_and_projections + feed_forward)
        + head
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
        if not np.isfinite(chunk.to_numpy(dtype=float)).all():
            raise RuntimeError(f"Prediction chunk {index} contains non-finite values")
    predictions = pd.concat(chunks, axis=0, ignore_index=True)
    return {name: predictions[name].to_numpy() for name in predictions.columns}


def _truth(split: LoadedSplit, appliance: str) -> Any:
    import numpy as np

    return np.concatenate(
        [frame.to_numpy().reshape(-1) for frame in split.appliances[appliance]]
    )


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
    parameter_counts: dict[str, int] = {}
    flops: dict[str, int | None] = {}
    metric_policy = config.metric_policy(task.metric_policy)
    for group in groups:
        label = group[0] if len(group) == 1 else "joint"
        train = load_split(
            config, task, task.train, group, sample_period, max_samples
        )
        test = load_split(
            config, task, task.test, group, sample_period, max_samples
        )
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

    return {
        "params_by_alignment_group": params_by_group,
        "metrics": scores,
        "objective_mae": statistics.fmean(item["mae"] for item in scores.values()),
        "trainable_parameters": parameter_counts,
        "inference_flops_estimate": flops,
        "flops_method": "dense multiply-add estimate; normalization and activations excluded",
        "train_windows": train_windows,
        "test_windows": test_windows,
        "elapsed_seconds": time.perf_counter() - started,
    }


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


def _write_result(result: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=False)
    csv_rows = ["appliance,mae,f1,activation_threshold_watts"]
    for appliance, values in result["run"]["metrics"].items():
        csv_rows.append(
            f"{appliance},{values['mae']},{values['f1']},"
            f"{values['activation_threshold_watts']}"
        )
    _atomic_write_text(output_dir / "metrics.csv", "\n".join(csv_rows) + "\n")
    _atomic_write_text(
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
) -> Path:
    task = config.task(task_id)
    chosen_appliances = appliances or task.appliances
    if len(set(chosen_appliances)) != len(chosen_appliances):
        raise ValueError("Appliances must not contain duplicates")
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("seed must be an integer")
    unknown = set(chosen_appliances) - set(task.appliances)
    if unknown:
        raise ValueError(f"Task {task.id} does not include: {', '.join(sorted(unknown))}")
    resolved_period = sample_period or task.sample_period
    if resolved_period <= 0:
        raise ValueError("sample_period must be positive")
    if max_samples is not None and max_samples <= 0:
        raise ValueError("max_samples must be positive")
    if epochs is not None and epochs <= 0:
        raise ValueError("epochs must be positive")
    if trials < 0:
        raise ValueError("trials must be non-negative")
    base_params: dict[str, Any] = {
        "sequence_length": 99,
        "n_epochs": epochs if epochs is not None else 10,
        "batch_size": 128,
        "learning_rate": 1e-3,
    }
    if device:
        base_params["device"] = device

    root = Path(__file__).resolve().parents[2]
    provenance = runtime_provenance(root)
    dataset_identities = {
        name: asdict(verify_dataset(config.datasets[name]))
        for name in sorted({w.dataset for w in (*task.train, *task.test)})
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
        study_spec = {
            "task_config_sha256": config.digest(task_id),
            "model": model_name,
            "seed": seed,
            "appliances": chosen_appliances,
            "sample_period": resolved_period,
            "max_samples": max_samples,
            "epochs_override": epochs,
            "model_module": get_model(model_name).module,
            "model_class": get_model(model_name).class_name,
            "nilmtk_contrib_git_sha": provenance["nilmtk_contrib_git_sha"],
            "nilmtk_contrib_version": provenance["nilmtk_contrib_version"],
        }
        study_digest = hashlib.sha256(
            json.dumps(study_spec, sort_keys=True).encode()
        ).hexdigest()[:12]
        study_name = f"{task_id}--{model_name}--seed{seed}--{study_digest}"
        storage = f"sqlite:///{(storage_dir / f'{study_name}.sqlite3').resolve()}"
        study = optuna.create_study(
            study_name=study_name,
            direction="minimize",
            sampler=optuna.samplers.TPESampler(seed=seed),
            storage=storage,
            load_if_exists=True,
        )

        def objective(trial: Any) -> float:
            params = get_model(model_name).search_space(trial)
            if epochs is not None:
                params["n_epochs"] = epochs
            if device:
                params["device"] = device
            trial_result = _run_once(
                config,
                task,
                model_name,
                seed,
                params,
                chosen_appliances,
                resolved_period,
                max_samples,
            )
            trial.set_user_attr("metrics", trial_result["metrics"])
            return trial_result["objective_mae"]

        completed_before = sum(
            trial.state == optuna.trial.TrialState.COMPLETE for trial in study.trials
        )
        study.optimize(objective, n_trials=max(0, trials - completed_before))
        base_params.update(study.best_params)
        if epochs is not None:
            base_params["n_epochs"] = epochs
        if device:
            base_params["device"] = device
        trial_metadata = {
            "study_name": study.study_name,
            "study_spec": study_spec,
            "study_digest": study_digest,
            "storage": storage,
            "completed_trials": sum(
                trial.state == optuna.trial.TrialState.COMPLETE
                for trial in study.trials
            ),
            "best_value": study.best_value,
            "best_params": study.best_params,
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
    result = {
        "schema_version": "1.1",
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
        "seed": seed,
        "sample_period": resolved_period,
        "appliances": chosen_appliances,
        "max_samples_per_window": max_samples,
        "run_scope": "smoke" if max_samples is not None or epochs is not None else "full",
        "protocol_overrides": {
            "max_samples_per_window": max_samples,
            "epochs": epochs,
            "appliances": None if chosen_appliances == task.appliances else chosen_appliances,
            "sample_period": None if resolved_period == task.sample_period else resolved_period,
        },
        "study": trial_metadata,
        "runtime": provenance,
        "run": run,
    }
    if not math.isfinite(run["objective_mae"]):
        raise RuntimeError("Benchmark produced a non-finite objective")
    result["result_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    ).hexdigest()
    _write_result(result, output_dir)
    return output_dir
