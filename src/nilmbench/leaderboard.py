"""Generate a living leaderboard only from immutable benchmark result bundles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable

from nilmbench._io import atomic_write_text


class LeaderboardError(ValueError):
    """Raised when a result bundle is incomplete, duplicated, or inconsistent."""


DEFAULT_REQUIRED_SEEDS = (10, 20, 42)


def _expect(mapping: dict[str, Any], key: str, path: Path) -> Any:
    if key not in mapping:
        raise LeaderboardError(f"{path} is missing {key!r}")
    return mapping[key]


def _is_sha(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 40 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        character in "0123456789abcdef" for character in value.lower()
    )


def _verified_provenance(result: dict[str, Any]) -> tuple[bool, list[str]]:
    runtime = result["runtime"]
    failures = []
    for name in ("nilmbench_git_sha", "nilmtk_contrib_git_sha"):
        if not _is_sha(runtime.get(name)):
            failures.append(f"missing {name}")
    for name in ("nilmbench_git_dirty", "nilmtk_contrib_git_dirty"):
        if runtime.get(name) is not False:
            failures.append(f"{name} is not false")
    digest = runtime.get("container_digest")
    if not (
        isinstance(digest, str)
        and digest.startswith("sha256:")
        and len(digest) == 71
    ):
        failures.append("missing immutable container_digest")
    if not runtime.get("gpu") and not runtime.get("cpu"):
        failures.append("missing hardware identity")
    for name, expected in result["dataset_manifests"].items():
        observed = result["dataset_identities"].get(name)
        if not observed or observed.get("sha256") != expected.get("sha256"):
            failures.append(f"dataset {name} checksum is not verified")
        if not observed or observed.get("size_bytes") != expected.get("size_bytes"):
            failures.append(f"dataset {name} size is not verified")
    return not failures, failures


def _alignment_group_value(
    values: Any, appliance: str, path: Path, field: str
) -> Any:
    if values is None:
        return None
    if not isinstance(values, dict):
        raise LeaderboardError(f"{path} run field {field!r} must be an object")
    if appliance in values:
        return values[appliance]
    if "joint" in values:
        return values["joint"]
    if len(values) == 1:
        return next(iter(values.values()))
    raise LeaderboardError(
        f"{path} cannot resolve {field!r} for appliance {appliance!r}"
    )


def _load_result(path: Path) -> dict[str, Any]:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
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
        "seed",
        "sample_period",
        "run_scope",
        "protocol_overrides",
        "runtime",
        "run",
    ):
        _expect(result, key, path)
    if result["schema_version"] != "1.1":
        raise LeaderboardError(
            f"{path} uses unsupported result schema {result['schema_version']!r}"
        )
    for key in (
        "task",
        "dataset_manifests",
        "dataset_identities",
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
    computed_id = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    result["result_id"] = stored_id
    if stored_id != computed_id:
        raise LeaderboardError(f"{path} result_id does not match its contents")
    if result["run_scope"] not in {"smoke", "full"}:
        raise LeaderboardError(f"{path} has an invalid run_scope")
    if not isinstance(result["protocol_overrides"], dict):
        raise LeaderboardError(f"{path} field 'protocol_overrides' must be an object")
    return result


def _rows(result: dict[str, Any], path: Path) -> Iterable[dict[str, Any]]:
    verified, failures = _verified_provenance(result)
    task = result["task"]
    overrides = result["protocol_overrides"]
    overrides_sha256 = hashlib.sha256(
        json.dumps(overrides, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    sequence_length = overrides.get("sequence_length")
    if sequence_length is not None and (
        isinstance(sequence_length, bool)
        or not isinstance(sequence_length, int)
        or sequence_length <= 0
    ):
        raise LeaderboardError(f"{path} has an invalid sequence-length override")
    epochs = overrides.get("epochs")
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
            raise LeaderboardError(f"{path} has invalid metrics for {appliance}") from exc
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
        yield {
            "task": task["id"],
            "family": task["family"],
            "profile": task["profile"],
            "target_data_access": task.get("target_data_access", "unknown"),
            "task_config_sha256": result["task_config_sha256"],
            "model": result["model"],
            "model_git_sha": result["runtime"].get("nilmtk_contrib_git_sha"),
            "runner_git_sha": result["runtime"].get("nilmbench_git_sha"),
            "container_digest": result["runtime"].get("container_digest"),
            "hardware": hardware,
            "sample_period": result["sample_period"],
            "protocol_overrides": overrides,
            "protocol_overrides_sha256": overrides_sha256,
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
            "provenance_verified": verified,
            "verification_failures": failures,
        }


def build_leaderboard(
    results_root: Path, required_seeds: tuple[int, ...] = DEFAULT_REQUIRED_SEEDS
) -> dict[str, Any]:
    """Validate, group, and aggregate every result.json below ``results_root``."""
    if not required_seeds or len(set(required_seeds)) != len(required_seeds):
        raise LeaderboardError("Required seeds must be non-empty and unique")
    paths = sorted(results_root.rglob("result.json")) if results_root.exists() else []
    grouped: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    seen_runs: set[tuple[Any, ...]] = set()
    for path in paths:
        for row in _rows(_load_result(path), path):
            group_key = (
                row["task"],
                row["task_config_sha256"],
                row["model"],
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
        if scope == "full" and profile == "corrected" and required.issubset(seeds) and verified:
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
            row["elapsed_seconds"]
            for row in rows
            if row["elapsed_seconds"] is not None
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
                "model_git_sha": rows[0]["model_git_sha"],
                "runner_git_sha": rows[0]["runner_git_sha"],
                "container_digest": rows[0]["container_digest"],
                "hardware": rows[0]["hardware"],
                "sample_period": rows[0]["sample_period"],
                "protocol_overrides": rows[0]["protocol_overrides"],
                "protocol_overrides_sha256": rows[0][
                    "protocol_overrides_sha256"
                ],
                "sequence_length": rows[0]["sequence_length"],
                "epochs": rows[0]["epochs"],
                "max_samples_per_window": rows[0]["max_samples_per_window"],
                "appliance": rows[0]["appliance"],
                "scope": scope,
                "status": status,
                "provenance_verified": verified,
                "verification_failures": sorted(
                    {failure for row in rows for failure in row["verification_failures"]}
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
                    else 0.0 if elapsed_values else None
                ),
                "trainable_parameters_mean": (
                    statistics.fmean(parameter_values) if parameter_values else None
                ),
                "trainable_parameters_std": (
                    statistics.stdev(parameter_values)
                    if len(parameter_values) > 1
                    else 0.0 if parameter_values else None
                ),
                "peak_accelerator_memory_bytes_mean": (
                    statistics.fmean(peak_memory_values)
                    if peak_memory_values
                    else None
                ),
                "peak_accelerator_memory_bytes_std": (
                    statistics.stdev(peak_memory_values)
                    if len(peak_memory_values) > 1
                    else 0.0 if peak_memory_values else None
                ),
                "result_ids": sorted(row["result_id"] for row in rows),
                "result_file_sha256": sorted(
                    row["result_file_sha256"] for row in rows
                ),
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
    atomic_write_text(
        json_path,
        json.dumps(leaderboard, indent=2, sort_keys=True, allow_nan=False) + "\n",
    )
    if csv_path is None:
        return
    fields = [
        "task",
        "family",
        "profile",
        "model",
        "model_git_sha",
        "runner_git_sha",
        "container_digest",
        "hardware",
        "sample_period",
        "sequence_length",
        "epochs",
        "max_samples_per_window",
        "protocol_overrides_sha256",
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
    atomic_write_text(csv_path, output.getvalue())
