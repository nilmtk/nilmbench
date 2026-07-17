"""Generate a living leaderboard only from immutable benchmark result bundles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
from collections import defaultdict
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from nilmbench._contracts import (
    HPO_SELECTION_PROTOCOL,
    HPO_TUNING_SEED,
    VALIDATION_PROTOCOL,
    alignment_groups,
    canonical_digest,
    is_git_sha,
    is_sha256,
    strict_json_loads,
    validate_persistent_hpo_provenance,
    validate_resolved_parameters,
    validate_trial_record,
)
from nilmbench._io import atomic_write_text
from nilmbench.config import BenchmarkConfig, load_config
from nilmbench.registry import MODELS


class LeaderboardError(ValueError):
    """Raised when a result bundle is incomplete, duplicated, or inconsistent."""


DEFAULT_REQUIRED_SEEDS = (10, 20, 42)


def _expect(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise LeaderboardError(f"{path} is missing {key!r}")
    return mapping[key]


def _is_sha(value: Any) -> bool:
    return is_git_sha(value)


def _is_sha256(value: Any) -> bool:
    return is_sha256(value)


def _json_value(value: Any) -> Any:
    """Normalize dataclass tuples to their serialized JSON representation."""
    return json.loads(json.dumps(value, sort_keys=True))


def _expected_window_samples(window: Any, sample_period: int, limit: int | None) -> int:
    requested_seconds = (
        datetime.fromisoformat(window.end) - datetime.fromisoformat(window.start)
    ).total_seconds()
    expected = max(1, int(requested_seconds // sample_period))
    return min(expected, limit) if limit is not None else expected


def _comparison_protocol(result: dict[str, Any]) -> dict[str, Any]:
    """Return only evaluation controls under which models may be ranked together."""
    overrides = result["protocol_overrides"]
    task = result["task"]
    runtime = result["runtime"]
    selection = overrides["model_selection"]
    return {
        "schema": "nilmbench.comparison-protocol.v1",
        "task_id": task["id"],
        "task_config_sha256": result["task_config_sha256"],
        "sample_period": result["sample_period"],
        "appliances": sorted(result["appliances"]),
        "max_samples_per_window": overrides["max_samples_per_window"],
        "epochs_override": overrides["epochs"],
        "effective_epochs": result["model_params"].get("n_epochs"),
        "effective_sequence_length": result["model_params"].get("sequence_length"),
        "model_selection": None
        if selection is None
        else {
            "method": selection["method"],
            "selection_protocol": selection["selection_protocol"],
            "tuning_seed": selection["tuning_seed"],
            "completed_trials": selection["completed_trials"],
            "validation_protocol": selection["validation_protocol"],
        },
        "runtime": {
            "nilmbench_git_sha": runtime.get("nilmbench_git_sha"),
            "nilmtk_contrib_git_sha": runtime.get("nilmtk_contrib_git_sha"),
            "container_image": runtime.get("container_image"),
            "container_digest": runtime.get("container_digest"),
            "hardware": runtime.get("gpu") or runtime.get("cpu"),
            "torch": runtime.get("torch"),
            "cuda_runtime": runtime.get("cuda_runtime"),
        },
        "scope": result["run_scope"],
        "target_data_access": task["target_data_access"],
    }


def _validate_study_contract(
    result: dict[str, Any], path: Path, config: BenchmarkConfig, task: Any
) -> None:
    study = result["study"]
    overrides = result["protocol_overrides"]
    model_selection = overrides["model_selection"]
    if study is None:
        if model_selection is not None:
            raise LeaderboardError(f"{path} declares model selection without a study")
        return
    if not isinstance(study, dict):
        raise LeaderboardError(f"{path} study must be null or an object")
    expected_study_fields = {
        "study_name",
        "study_spec",
        "study_digest",
        "selection_protocol",
        "tuning_seed",
        "coordination",
        "completed_trials",
        "best_value",
        "best_params",
        "optuna_best_suggestions",
        "trial_record_files",
        "trial_records",
    }
    if set(study) != expected_study_fields:
        raise LeaderboardError(f"{path} has invalid study summary fields")
    try:
        validate_persistent_hpo_provenance(result["runtime"])
    except ValueError as exc:
        raise LeaderboardError(
            f"{path} has unsafe persistent HPO provenance: {exc}"
        ) from exc

    study_spec = study["study_spec"]
    study_digest = study["study_digest"]
    if not isinstance(study_spec, dict) or study_digest != canonical_digest(study_spec):
        raise LeaderboardError(f"{path} study identity does not match its spec")
    if (
        study["selection_protocol"] != HPO_SELECTION_PROTOCOL
        or study["tuning_seed"] != HPO_TUNING_SEED
    ):
        raise LeaderboardError(f"{path} uses an unsupported multi-seed HPO protocol")
    expected_study_name = (
        f"{task.id}--{result['model']}--tune-seed{HPO_TUNING_SEED}--{study_digest}"
    )
    if study["study_name"] != expected_study_name:
        raise LeaderboardError(f"{path} study name is not bound to its identity")

    runtime = result["runtime"]
    model_spec = result["model_spec"]
    expected_top_spec_keys = {
        "identity_schema",
        "runner",
        "contrib",
        "container",
        "device",
        "protocol",
        "source_dataset_identities",
    }
    if (
        set(study_spec) != expected_top_spec_keys
        or study_spec.get("identity_schema") != "nilmbench.optuna-study.v2"
    ):
        raise LeaderboardError(f"{path} has an invalid study specification schema")
    if study_spec["runner"] != {
        "git_sha": runtime.get("nilmbench_git_sha"),
        "git_dirty": runtime.get("nilmbench_git_dirty"),
    }:
        raise LeaderboardError(f"{path} study runner is not bound to result provenance")
    if study_spec["contrib"] != {
        "git_sha": runtime.get("nilmtk_contrib_git_sha"),
        "git_dirty": runtime.get("nilmtk_contrib_git_dirty"),
        "version": runtime.get("nilmtk_contrib_version"),
        "model_module": model_spec["module"],
        "model_class": model_spec["class_name"],
    }:
        raise LeaderboardError(f"{path} study model is not bound to result provenance")
    if study_spec["container"] != {
        "image": runtime.get("container_image"),
        "digest": runtime.get("container_digest"),
    }:
        raise LeaderboardError(f"{path} study container is not bound to provenance")
    expected_device = {
        "requested": result["model_params"].get("device", "auto"),
        "cpu": runtime.get("cpu"),
        "gpu": runtime.get("gpu"),
        "torch": runtime.get("torch"),
        "cuda_runtime": runtime.get("cuda_runtime"),
        "cuda_available": runtime.get("cuda_available"),
    }
    if study_spec["device"] != expected_device:
        raise LeaderboardError(f"{path} study device is not bound to provenance")

    protocol = study_spec["protocol"]
    if not isinstance(protocol, dict) or set(protocol) != {
        "task_id",
        "task_family",
        "task_profile",
        "task_config_sha256",
        "target_data_access",
        "model",
        "selection_protocol",
        "tuning_seed",
        "appliances",
        "sample_period",
        "max_samples_per_window",
        "epochs_override",
        "sequence_length_override",
        "alignment_policy",
        "metric_policy",
        "validation",
        "optimization",
    }:
        raise LeaderboardError(f"{path} study protocol has invalid fields")
    expected_protocol = {
        "task_id": task.id,
        "task_family": task.family,
        "task_profile": task.profile,
        "task_config_sha256": result["task_config_sha256"],
        "target_data_access": task.target_data_access,
        "model": result["model"],
        "selection_protocol": HPO_SELECTION_PROTOCOL,
        "tuning_seed": HPO_TUNING_SEED,
        "appliances": result["appliances"],
        "sample_period": result["sample_period"],
        "max_samples_per_window": overrides["max_samples_per_window"],
        "epochs_override": overrides["epochs"],
        "sequence_length_override": overrides["sequence_length"],
        "alignment_policy": task.alignment_policy,
        "metric_policy": task.metric_policy,
        "validation": VALIDATION_PROTOCOL,
    }
    for name, value in expected_protocol.items():
        if protocol.get(name) != value:
            raise LeaderboardError(f"{path} study protocol {name!r} is not bound")
    optimization = protocol["optimization"]
    if (
        not isinstance(optimization, dict)
        or set(optimization)
        != {
            "library",
            "version",
            "direction",
            "sampler",
            "sampler_seed",
        }
        or optimization.get("library") != "optuna"
        or not isinstance(optimization.get("version"), str)
        or not optimization["version"]
        or optimization.get("direction") != "minimize"
        or optimization.get("sampler") != "TPESampler"
        or optimization.get("sampler_seed") != HPO_TUNING_SEED
    ):
        raise LeaderboardError(f"{path} study optimization contract is invalid")
    source_names = {window.dataset for window in task.train}
    expected_sources = {
        name: result["dataset_identities"][name] for name in sorted(source_names)
    }
    if study_spec["source_dataset_identities"] != expected_sources:
        raise LeaderboardError(f"{path} study sources are not bound to task.train")

    coordination = study["coordination"]
    expected_storage = f"optuna/{study['study_name']}.sqlite3"
    if coordination != {
        "backend": "sqlite",
        "storage": expected_storage,
        "scientific_source_of_truth": False,
    }:
        raise LeaderboardError(f"{path} study coordination disclosure is invalid")
    completed = study["completed_trials"]
    records = study["trial_records"]
    files = study["trial_record_files"]
    if (
        isinstance(completed, bool)
        or not isinstance(completed, int)
        or completed <= 0
        or not isinstance(records, list)
        or len(records) != completed
        or not isinstance(files, list)
        or len(files) != completed
        or study["best_params"] != result["model_params"]
        or not isinstance(study["optuna_best_suggestions"], dict)
        or any(
            result["model_params"].get(name) != value
            for name, value in study["optuna_best_suggestions"].items()
        )
    ):
        raise LeaderboardError(f"{path} study summary is inconsistent")
    best_value = study["best_value"]
    if (
        isinstance(best_value, bool)
        or not isinstance(best_value, (int, float))
        or not math.isfinite(best_value)
        or best_value < 0
    ):
        raise LeaderboardError(f"{path} study has an invalid best value")

    numbers: set[int] = set()
    objectives: list[float] = []
    policy = config.metric_policy(task.metric_policy)
    for index, record in enumerate(records):
        try:
            validate_trial_record(
                record,
                study_name=study["study_name"],
                study_digest=study_digest,
                study_spec=study_spec,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaderboardError(
                f"{path} has an invalid trial audit record: {exc}"
            ) from exc
        number = record["trial_number"]
        if number in numbers:
            raise LeaderboardError(f"{path} has duplicate trial audit records")
        numbers.add(number)
        expected_file = f"optuna/{study['study_name']}/trials/trial-{number:06d}.json"
        if files[index] != expected_file:
            raise LeaderboardError(f"{path} trial record file disclosure is invalid")
        for appliance, metrics in record["validation"]["metrics"].items():
            if metrics["activation_threshold_watts"] != policy.threshold(appliance):
                raise LeaderboardError(f"{path} trial threshold is not trusted")
        partitions = record["validation"]["partitions"]
        for group_windows in partitions.values():
            if not isinstance(group_windows, list) or len(group_windows) != len(
                task.train
            ):
                raise LeaderboardError(f"{path} trial partitions are incomplete")
            for partition, configured in zip(group_windows, task.train, strict=True):
                if (
                    not isinstance(partition, dict)
                    or set(partition)
                    != {
                        "source_window",
                        "training",
                        "validation",
                    }
                    or not isinstance(partition["source_window"], dict)
                    or partition["source_window"].get("requested")
                    != _json_value(asdict(configured))
                ):
                    raise LeaderboardError(
                        f"{path} trial partition is not bound to task.train"
                    )
                for split in ("training", "validation"):
                    split_value = partition[split]
                    if (
                        not isinstance(split_value, dict)
                        or set(split_value)
                        != {
                            "samples",
                            "actual_start",
                            "actual_end",
                        }
                        or isinstance(split_value["samples"], bool)
                        or not isinstance(split_value["samples"], int)
                        or split_value["samples"] <= 0
                    ):
                        raise LeaderboardError(
                            f"{path} trial partition evidence is invalid"
                        )
        objectives.append(record["validation"]["objective_mae"])
    if not math.isclose(best_value, min(objectives), rel_tol=1e-12):
        raise LeaderboardError(f"{path} study best value is not supported by trials")
    best_suggestions = study["optuna_best_suggestions"]
    if not any(
        math.isclose(record["validation"]["objective_mae"], best_value, rel_tol=1e-12)
        and record["parameters"]["suggested"] == best_suggestions
        and record["parameters"]["effective"] == study["best_params"]
        for record in records
    ):
        raise LeaderboardError(
            f"{path} selected parameters are not supported by the best trial"
        )
    expected_selection = {
        "method": "optuna-tpe",
        "selection_protocol": HPO_SELECTION_PROTOCOL,
        "tuning_seed": HPO_TUNING_SEED,
        "study_identity_sha256": study_digest,
        "completed_trials": completed,
        "validation_protocol": VALIDATION_PROTOCOL["id"],
        "selected_parameters": study["best_params"],
    }
    if model_selection != expected_selection:
        raise LeaderboardError(f"{path} model-selection disclosure is inconsistent")


def _validate_trusted_contract(
    result: dict[str, Any], path: Path, config: BenchmarkConfig
) -> None:
    task_payload = result["task"]
    task_id = task_payload.get("id")
    if not isinstance(task_id, str) or task_id not in config.tasks:
        raise LeaderboardError(f"{path} references an unknown benchmark task")
    task = config.task(task_id)
    if task_payload != _json_value(asdict(task)):
        raise LeaderboardError(f"{path} task does not match trusted configuration")
    if result["task_config_sha256"] != config.digest(task_id):
        raise LeaderboardError(
            f"{path} task digest does not match trusted configuration"
        )
    expected_policy = _json_value(asdict(config.metric_policy(task.metric_policy)))
    if result["metric_policy"] != expected_policy:
        raise LeaderboardError(f"{path} metric policy does not match configuration")
    dataset_names = sorted({window.dataset for window in (*task.train, *task.test)})
    expected_manifests = {
        name: _json_value(asdict(config.datasets[name])) for name in dataset_names
    }
    if result["dataset_manifests"] != expected_manifests:
        raise LeaderboardError(f"{path} dataset manifests do not match configuration")
    if set(result["dataset_identities"]) != set(expected_manifests):
        raise LeaderboardError(f"{path} dataset identities are incomplete")
    for name, identity in result["dataset_identities"].items():
        if (
            not isinstance(identity, dict)
            or identity.get("id") != name
            or not isinstance(identity.get("path"), str)
            or not identity["path"]
        ):
            raise LeaderboardError(f"{path} dataset identity {name} is invalid")

    model_name = result["model"]
    if not isinstance(model_name, str) or model_name not in MODELS:
        raise LeaderboardError(f"{path} references an unknown benchmark model")
    entry = MODELS[model_name]
    expected_spec = {
        "module": entry.module,
        "class_name": entry.class_name,
        "family": entry.family,
    }
    if result["model_spec"] != expected_spec:
        raise LeaderboardError(f"{path} model specification is not trusted")
    model_params = result["model_params"]
    if not isinstance(model_params, dict):
        raise LeaderboardError(f"{path} model_params must be an object")
    params_digest = canonical_digest(model_params)
    if result["model_params_sha256"] != params_digest:
        raise LeaderboardError(f"{path} model parameter digest does not match")
    for override_name, parameter_name in (
        ("epochs", "n_epochs"),
        ("sequence_length", "sequence_length"),
    ):
        override = result["protocol_overrides"].get(override_name)
        if override is not None and model_params.get(parameter_name) != override:
            raise LeaderboardError(
                f"{path} {override_name} override does not match effective parameters"
            )

    overrides = result["protocol_overrides"]
    if set(overrides) != {
        "max_samples_per_window",
        "epochs",
        "appliances",
        "sample_period",
        "sequence_length",
        "model_selection",
    }:
        raise LeaderboardError(f"{path} has invalid protocol override fields")
    for name in (
        "max_samples_per_window",
        "epochs",
        "sample_period",
        "sequence_length",
    ):
        value = overrides[name]
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise LeaderboardError(f"{path} has an invalid {name} override")
    period_override = overrides.get("sample_period")
    expected_period = period_override or task.sample_period
    if result["sample_period"] != expected_period:
        raise LeaderboardError(f"{path} sample period does not match its protocol")
    max_samples = overrides["max_samples_per_window"]
    if result.get("max_samples_per_window") != max_samples:
        raise LeaderboardError(f"{path} sample limit does not match its protocol")

    _validate_study_contract(result, path, config, task)

    appliances = result["appliances"]
    if (
        not isinstance(appliances, list)
        or not appliances
        or len(set(appliances)) != len(appliances)
        or not set(appliances).issubset(task.appliances)
    ):
        raise LeaderboardError(f"{path} has invalid benchmark appliances")
    if result["run_scope"] == "full" and appliances != list(task.appliances):
        raise LeaderboardError(f"{path} full run omits configured appliances")
    appliance_override = overrides.get("appliances")
    if appliance_override is None:
        if appliances != list(task.appliances):
            raise LeaderboardError(f"{path} appliance subset is not disclosed")
    elif appliances != appliance_override:
        raise LeaderboardError(f"{path} appliance override does not match the run")
    metrics = result["run"].get("metrics")
    if not isinstance(metrics, dict) or set(metrics) != set(appliances):
        raise LeaderboardError(f"{path} metrics do not match benchmark appliances")
    metric_maes: list[float] = []
    for appliance, values in metrics.items():
        mae = values.get("mae") if isinstance(values, dict) else None
        if (
            not isinstance(values, dict)
            or isinstance(mae, bool)
            or not isinstance(mae, (int, float))
            or not math.isfinite(mae)
            or values.get("activation_threshold_watts")
            != config.metric_policy(task.metric_policy).threshold(appliance)
        ):
            raise LeaderboardError(f"{path} metrics are not trusted")
        metric_maes.append(float(mae))
    objective = result["run"].get("objective_mae")
    if (
        isinstance(objective, bool)
        or not isinstance(objective, (int, float))
        or not math.isfinite(objective)
        or not math.isclose(objective, statistics.fmean(metric_maes), rel_tol=1e-12)
    ):
        raise LeaderboardError(f"{path} run objective is not supported by metrics")

    expected_groups = alignment_groups(appliances, task.alignment_policy)
    for field in (
        "elapsed_seconds_by_alignment_group",
        "trainable_parameters",
        "inference_flops_estimate",
        "peak_accelerator_memory_bytes",
    ):
        values = result["run"].get(field)
        if values is not None and (
            not isinstance(values, dict) or set(values) != expected_groups
        ):
            raise LeaderboardError(f"{path} has invalid {field} groups")
    try:
        validate_resolved_parameters(
            result["run"].get("params_by_alignment_group"),
            model_params=model_params,
            expected_groups=expected_groups,
            expected_seed=result["seed"],
        )
    except ValueError as exc:
        raise LeaderboardError(
            f"{path} has invalid resolved model parameters: {exc}"
        ) from exc
    observed_limits: set[int | None] = set()
    for field, configured_windows in (
        ("train_windows", task.train),
        ("test_windows", task.test),
    ):
        groups = result["run"].get(field)
        if not isinstance(groups, dict) or set(groups) != expected_groups:
            raise LeaderboardError(f"{path} has invalid {field} groups")
        for windows in groups.values():
            if not isinstance(windows, list) or len(windows) != len(configured_windows):
                raise LeaderboardError(f"{path} has an incomplete {field} group")
            for window, configured in zip(windows, configured_windows, strict=True):
                if not isinstance(window, dict):
                    raise LeaderboardError(f"{path} has invalid {field} evidence")
                if window.get("requested") != _json_value(asdict(configured)):
                    raise LeaderboardError(f"{path} {field} window is not configured")
                samples = window.get("samples")
                expected_samples = window.get("expected_samples")
                sample_limit = window.get("sample_limit")
                observed_limits.add(sample_limit)
                fraction = window.get("aligned_sample_fraction")
                trusted_expected = _expected_window_samples(
                    configured, result["sample_period"], max_samples
                )
                if (
                    isinstance(samples, bool)
                    or not isinstance(samples, int)
                    or samples <= 0
                    or isinstance(expected_samples, bool)
                    or not isinstance(expected_samples, int)
                    or expected_samples <= 0
                    or expected_samples != trusted_expected
                    or sample_limit != max_samples
                    or isinstance(fraction, bool)
                    or not isinstance(fraction, (int, float))
                    or not math.isfinite(fraction)
                    or not task.minimum_aligned_fraction <= fraction <= 1
                    or not math.isclose(
                        fraction, samples / expected_samples, rel_tol=1e-12
                    )
                ):
                    raise LeaderboardError(
                        f"{path} {field} violates minimum aligned sample coverage"
                    )
    expected_scope = (
        "smoke"
        if max_samples is not None
        or overrides["epochs"] is not None
        or overrides["appliances"] is not None
        or any(limit is not None for limit in observed_limits)
        else "full"
    )
    if result["run_scope"] != expected_scope:
        raise LeaderboardError(
            f"{path} run_scope does not match observed sample and epoch budgets"
        )


def _verified_provenance(
    result: dict[str, Any], config: BenchmarkConfig
) -> tuple[bool, list[str]]:
    runtime = result["runtime"]
    failures = []
    for name in ("nilmbench_git_sha", "nilmtk_contrib_git_sha"):
        if not _is_sha(runtime.get(name)):
            failures.append(f"missing {name}")
    for name in ("nilmbench_git_dirty", "nilmtk_contrib_git_dirty"):
        if runtime.get(name) is not False:
            failures.append(f"{name} is not false")
    digest = runtime.get("container_digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or not _is_sha256(digest[7:])
    ):
        failures.append("missing immutable container_digest")
    if not isinstance(runtime.get("container_image"), str) or not runtime.get(
        "container_image"
    ):
        failures.append("missing container_image")
    if not runtime.get("gpu") and not runtime.get("cpu"):
        failures.append("missing hardware identity")
    if runtime.get("gpu") and runtime.get("cuda_available") is not True:
        failures.append("GPU identity without available CUDA runtime")
    observed_runtime = {
        "nilmbench_git_sha": runtime.get("nilmbench_git_sha"),
        "nilmtk_contrib_git_sha": runtime.get("nilmtk_contrib_git_sha"),
        "container_image": runtime.get("container_image"),
        "container_digest": runtime.get("container_digest"),
        "hardware": runtime.get("gpu") or runtime.get("cpu"),
    }
    if not any(
        observed_runtime
        == {
            "nilmbench_git_sha": item.nilmbench_git_sha,
            "nilmtk_contrib_git_sha": item.nilmtk_contrib_git_sha,
            "container_image": item.container_image,
            "container_digest": item.container_digest,
            "hardware": item.hardware,
        }
        for item in config.trusted_runtimes
    ):
        failures.append("runtime tuple is not approved for publication")
    for name, expected in result["dataset_manifests"].items():
        observed = result["dataset_identities"].get(name)
        if not observed or observed.get("sha256") != expected.get("sha256"):
            failures.append(f"dataset {name} checksum is not verified")
        if not observed or observed.get("size_bytes") != expected.get("size_bytes"):
            failures.append(f"dataset {name} size is not verified")
    return not failures, failures


def _alignment_group_value(values: Any, appliance: str, path: Path, field: str) -> Any:
    if values is None:
        return None
    if not isinstance(values, dict):
        raise LeaderboardError(f"{path} run field {field!r} must be an object")
    if appliance in values:
        return values[appliance]
    if "joint" in values:
        return values["joint"]
    raise LeaderboardError(
        f"{path} cannot resolve {field!r} for appliance {appliance!r}"
    )


def _load_result(path: Path) -> dict[str, Any]:
    try:
        result = strict_json_loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        raise LeaderboardError(f"Could not read {path}: {exc}") from exc
    if not isinstance(result, dict):
        raise LeaderboardError(f"{path} must contain a JSON object")
    for key in (
        "schema_version",
        "result_id",
        "task",
        "task_config_sha256",
        "dataset_manifests",
        "dataset_identities",
        "model",
        "model_spec",
        "model_params",
        "model_params_sha256",
        "seed",
        "sample_period",
        "appliances",
        "max_samples_per_window",
        "metric_policy",
        "run_scope",
        "protocol_overrides",
        "study",
        "runtime",
        "run",
    ):
        _expect(result, key, path)
    if result["schema_version"] != "1.2":
        raise LeaderboardError(
            f"{path} uses unsupported result schema {result['schema_version']!r}"
        )
    for key in (
        "task",
        "dataset_manifests",
        "dataset_identities",
        "metric_policy",
        "model_spec",
        "model_params",
        "runtime",
        "run",
    ):
        if not isinstance(result[key], dict):
            raise LeaderboardError(f"{path} field {key!r} must be an object")
    if not _is_sha256(result["task_config_sha256"]):
        raise LeaderboardError(f"{path} has an invalid task_config_sha256")
    if isinstance(result["seed"], bool) or not isinstance(result["seed"], int):
        raise LeaderboardError(f"{path} has an invalid seed")
    if (
        isinstance(result["sample_period"], bool)
        or not isinstance(result["sample_period"], int)
        or result["sample_period"] <= 0
    ):
        raise LeaderboardError(f"{path} has an invalid sample_period")
    stored_id = result.pop("result_id")
    computed_id = canonical_digest(result)
    result["result_id"] = stored_id
    if stored_id != computed_id:
        raise LeaderboardError(f"{path} result_id does not match its contents")
    if result["run_scope"] not in {"smoke", "full"}:
        raise LeaderboardError(f"{path} has an invalid run_scope")
    if not isinstance(result["protocol_overrides"], dict):
        raise LeaderboardError(f"{path} field 'protocol_overrides' must be an object")
    return result


def _rows(
    result: dict[str, Any], path: Path, config: BenchmarkConfig
) -> Iterable[dict[str, Any]]:
    verified, failures = _verified_provenance(result, config)
    task = result["task"]
    overrides = result["protocol_overrides"]
    overrides_sha256 = hashlib.sha256(
        json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    comparison_protocol = _comparison_protocol(result)
    comparison_protocol_sha256 = canonical_digest(comparison_protocol)
    model_params = result["model_params"]
    sequence_length = model_params.get("sequence_length")
    if sequence_length is not None and (
        isinstance(sequence_length, bool)
        or not isinstance(sequence_length, int)
        or sequence_length <= 0
    ):
        raise LeaderboardError(f"{path} has an invalid sequence-length override")
    epochs = model_params.get("n_epochs")
    max_samples = overrides.get("max_samples_per_window")
    for name, value in (("epochs", epochs), ("max_samples_per_window", max_samples)):
        if value is not None and (
            isinstance(value, bool) or not isinstance(value, int) or value <= 0
        ):
            raise LeaderboardError(f"{path} has an invalid {name} override")
    metrics = result["run"].get("metrics")
    if not isinstance(metrics, dict) or not metrics:
        raise LeaderboardError(f"{path} has no appliance metrics")
    for appliance, values in metrics.items():
        try:
            mae = float(values["mae"])
            f1 = float(values["f1"])
            threshold = float(values["activation_threshold_watts"])
        except (KeyError, TypeError, ValueError) as exc:
            raise LeaderboardError(
                f"{path} has invalid metrics for {appliance}"
            ) from exc
        if (
            not isinstance(appliance, str)
            or not appliance
            or not math.isfinite(mae)
            or mae < 0
            or not math.isfinite(f1)
            or not 0 <= f1 <= 1
            or not math.isfinite(threshold)
            or threshold <= 0
        ):
            raise LeaderboardError(f"{path} has out-of-range metrics for {appliance}")
        hardware = result["runtime"].get("gpu") or result["runtime"].get("cpu")
        elapsed = _alignment_group_value(
            result["run"].get("elapsed_seconds_by_alignment_group"),
            appliance,
            path,
            "elapsed_seconds_by_alignment_group",
        )
        if elapsed is None and len(metrics) == 1:
            elapsed = result["run"].get("elapsed_seconds")
        parameters = _alignment_group_value(
            result["run"].get("trainable_parameters"),
            appliance,
            path,
            "trainable_parameters",
        )
        peak_memory = _alignment_group_value(
            result["run"].get("peak_accelerator_memory_bytes"),
            appliance,
            path,
            "peak_accelerator_memory_bytes",
        )
        if elapsed is not None and (
            isinstance(elapsed, bool)
            or not isinstance(elapsed, (int, float))
            or not math.isfinite(elapsed)
            or elapsed <= 0
        ):
            raise LeaderboardError(f"{path} has invalid elapsed seconds")
        if parameters is not None and (
            isinstance(parameters, bool)
            or not isinstance(parameters, int)
            or parameters <= 0
        ):
            raise LeaderboardError(f"{path} has an invalid parameter count")
        if peak_memory is not None and (
            isinstance(peak_memory, bool)
            or not isinstance(peak_memory, int)
            or peak_memory < 0
        ):
            raise LeaderboardError(f"{path} has invalid peak accelerator memory")
        row_failures = list(failures)
        if elapsed is None:
            row_failures.append("missing elapsed efficiency measurement")
        is_neural = result["model_spec"]["family"] != "statistical-baseline"
        if is_neural and parameters is None:
            row_failures.append("missing trainable parameter count")
        if is_neural and result["runtime"].get("gpu") and peak_memory is None:
            row_failures.append("missing peak accelerator memory")
        tuning_study_digest = (
            result["study"].get("study_digest")
            if isinstance(result.get("study"), dict)
            else None
        )
        yield {
            "task": task["id"],
            "family": task["family"],
            "profile": task["profile"],
            "target_data_access": task.get("target_data_access", "unknown"),
            "task_config_sha256": result["task_config_sha256"],
            "model": result["model"],
            "model_family": result["model_spec"]["family"],
            "model_params": model_params,
            "model_params_sha256": result["model_params_sha256"],
            "tuning_study_digest": tuning_study_digest,
            "model_git_sha": result["runtime"].get("nilmtk_contrib_git_sha"),
            "runner_git_sha": result["runtime"].get("nilmbench_git_sha"),
            "container_digest": result["runtime"].get("container_digest"),
            "hardware": hardware,
            "sample_period": result["sample_period"],
            "protocol_overrides": overrides,
            "protocol_overrides_sha256": overrides_sha256,
            "comparison_protocol": comparison_protocol,
            "comparison_protocol_sha256": comparison_protocol_sha256,
            "sequence_length": sequence_length,
            "epochs": epochs,
            "max_samples_per_window": max_samples,
            "appliance": appliance,
            "scope": result["run_scope"],
            "seed": result["seed"],
            "mae": mae,
            "f1": f1,
            "elapsed_seconds": None if elapsed is None else float(elapsed),
            "trainable_parameters": parameters,
            "peak_accelerator_memory_bytes": peak_memory,
            "result_id": result["result_id"],
            "result_file_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "provenance_verified": verified and not row_failures,
            "verification_failures": row_failures,
        }


def build_leaderboard(
    results_root: Path,
    required_seeds: tuple[int, ...] = DEFAULT_REQUIRED_SEEDS,
    config: BenchmarkConfig | None = None,
) -> dict[str, Any]:
    """Validate, group, and aggregate every result.json below ``results_root``."""
    if not required_seeds or len(set(required_seeds)) != len(required_seeds):
        raise LeaderboardError("Required seeds must be non-empty and unique")
    trusted_config = config or load_config()
    paths = sorted(results_root.rglob("result.json")) if results_root.exists() else []
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    seen_runs: set[tuple[Any, ...]] = set()
    for path in paths:
        result = _load_result(path)
        _validate_trusted_contract(result, path, trusted_config)
        for row in _rows(result, path, trusted_config):
            group_key = (
                row["task"],
                row["task_config_sha256"],
                row["model"],
                row["model_params_sha256"],
                row["tuning_study_digest"],
                row["model_git_sha"],
                row["runner_git_sha"],
                row["container_digest"],
                row["hardware"],
                row["sample_period"],
                row["protocol_overrides_sha256"],
                row["appliance"],
                row["scope"],
                row["target_data_access"],
            )
            run_key = (*group_key, row["seed"])
            if run_key in seen_runs:
                raise LeaderboardError(
                    "Duplicate task/model/revision/appliance/scope/seed result: "
                    + "/".join(str(value) for value in run_key)
                )
            seen_runs.add(run_key)
            grouped[group_key].append(row)

    entries = []
    required = set(required_seeds)
    for key, rows in sorted(grouped.items(), key=lambda item: str(item[0])):
        seeds = sorted(row["seed"] for row in rows)
        verified = all(row["provenance_verified"] for row in rows)
        scope = rows[0]["scope"]
        profile = rows[0]["profile"]
        if (
            scope == "full"
            and profile == "corrected"
            and required.issubset(seeds)
            and verified
        ):
            status = "full-verified"
        elif scope == "smoke" and required.issubset(seeds) and verified:
            status = "smoke-verified"
        elif scope == "smoke" and verified:
            status = "smoke-partial"
        elif scope == "smoke":
            status = "smoke-unverified"
        else:
            status = "candidate"
        maes = [row["mae"] for row in rows]
        f1s = [row["f1"] for row in rows]
        elapsed_values = [
            row["elapsed_seconds"] for row in rows if row["elapsed_seconds"] is not None
        ]
        parameter_values = [
            row["trainable_parameters"]
            for row in rows
            if row["trainable_parameters"] is not None
        ]
        peak_memory_values = [
            row["peak_accelerator_memory_bytes"]
            for row in rows
            if row["peak_accelerator_memory_bytes"] is not None
        ]
        entries.append(
            {
                "task": rows[0]["task"],
                "family": rows[0]["family"],
                "profile": profile,
                "target_data_access": rows[0]["target_data_access"],
                "task_config_sha256": rows[0]["task_config_sha256"],
                "model": rows[0]["model"],
                "model_family": rows[0]["model_family"],
                "model_params": rows[0]["model_params"],
                "model_params_sha256": rows[0]["model_params_sha256"],
                "tuning_study_digest": rows[0]["tuning_study_digest"],
                "model_git_sha": rows[0]["model_git_sha"],
                "runner_git_sha": rows[0]["runner_git_sha"],
                "container_digest": rows[0]["container_digest"],
                "hardware": rows[0]["hardware"],
                "sample_period": rows[0]["sample_period"],
                "protocol_overrides": rows[0]["protocol_overrides"],
                "protocol_overrides_sha256": rows[0]["protocol_overrides_sha256"],
                "comparison_protocol": rows[0]["comparison_protocol"],
                "comparison_protocol_sha256": rows[0]["comparison_protocol_sha256"],
                "sequence_length": rows[0]["sequence_length"],
                "epochs": rows[0]["epochs"],
                "max_samples_per_window": rows[0]["max_samples_per_window"],
                "appliance": rows[0]["appliance"],
                "scope": scope,
                "status": status,
                "provenance_verified": verified,
                "verification_failures": sorted(
                    {
                        failure
                        for row in rows
                        for failure in row["verification_failures"]
                    }
                ),
                "seeds": seeds,
                "run_count": len(rows),
                "mae_mean": statistics.fmean(maes),
                "mae_std": statistics.stdev(maes) if len(maes) > 1 else 0.0,
                "f1_mean": statistics.fmean(f1s),
                "f1_std": statistics.stdev(f1s) if len(f1s) > 1 else 0.0,
                "elapsed_seconds_mean": (
                    statistics.fmean(elapsed_values) if elapsed_values else None
                ),
                "elapsed_seconds_std": (
                    statistics.stdev(elapsed_values)
                    if len(elapsed_values) > 1
                    else 0.0
                    if elapsed_values
                    else None
                ),
                "trainable_parameters_mean": (
                    statistics.fmean(parameter_values) if parameter_values else None
                ),
                "trainable_parameters_std": (
                    statistics.stdev(parameter_values)
                    if len(parameter_values) > 1
                    else 0.0
                    if parameter_values
                    else None
                ),
                "peak_accelerator_memory_bytes_mean": (
                    statistics.fmean(peak_memory_values) if peak_memory_values else None
                ),
                "peak_accelerator_memory_bytes_std": (
                    statistics.stdev(peak_memory_values)
                    if len(peak_memory_values) > 1
                    else 0.0
                    if peak_memory_values
                    else None
                ),
                "result_ids": sorted(row["result_id"] for row in rows),
                "result_file_sha256": sorted(row["result_file_sha256"] for row in rows),
            }
        )
    entries.sort(
        key=lambda item: (
            item["task"],
            item["sample_period"],
            item["appliance"],
            item["status"] != "full-verified",
            item["mae_mean"],
            item["model"],
        )
    )
    return {
        "schema_version": "1.0",
        "required_seeds": list(required_seeds),
        "source_result_count": len(paths),
        "entries": entries,
    }


def write_leaderboard(
    leaderboard: dict[str, Any], json_path: Path, csv_path: Path | None = None
) -> None:
    if csv_path is None:
        atomic_write_text(
            json_path,
            json.dumps(leaderboard, indent=2, sort_keys=True, allow_nan=False) + "\n",
        )
        return
    fields = [
        "task",
        "family",
        "profile",
        "model",
        "model_family",
        "model_params_sha256",
        "tuning_study_digest",
        "model_git_sha",
        "runner_git_sha",
        "container_digest",
        "hardware",
        "sample_period",
        "sequence_length",
        "epochs",
        "max_samples_per_window",
        "protocol_overrides_sha256",
        "comparison_protocol_sha256",
        "appliance",
        "target_data_access",
        "scope",
        "status",
        "seeds",
        "run_count",
        "mae_mean",
        "mae_std",
        "f1_mean",
        "f1_std",
        "elapsed_seconds_mean",
        "elapsed_seconds_std",
        "trainable_parameters_mean",
        "trainable_parameters_std",
        "peak_accelerator_memory_bytes_mean",
        "peak_accelerator_memory_bytes_std",
    ]
    output = io.StringIO()
    writer = csv.DictWriter(output, fieldnames=fields, lineterminator="\n")
    writer.writeheader()
    for entry in leaderboard["entries"]:
        writer.writerow(
            {
                key: ";".join(map(str, entry[key])) if key == "seeds" else entry[key]
                for key in fields
            }
        )
    csv_content = output.getvalue()
    payload = {
        **leaderboard,
        "artifacts": {
            "csv_name": csv_path.name,
            "csv_sha256": hashlib.sha256(csv_content.encode()).hexdigest(),
        },
    }
    # JSON is the commit marker: publish the referenced CSV first, then the JSON.
    atomic_write_text(csv_path, csv_content)
    atomic_write_text(
        json_path,
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
