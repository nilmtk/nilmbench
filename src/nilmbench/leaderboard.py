"""Generate a living leaderboard only from immutable benchmark result bundles."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import math
import os
import statistics
import tempfile
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


class LeaderboardError(ValueError):
    """Raised when a result bundle is incomplete, duplicated, or inconsistent."""


DEFAULT_REQUIRED_SEEDS = (10, 20, 42)


def _atomic_write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w", encoding="utf-8", dir=path.parent, prefix=f".{path.name}.", delete=False
    ) as handle:
        handle.write(content)
        temporary = Path(handle.name)
    os.replace(temporary, path)


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
            "appliance": appliance,
            "scope": result["run_scope"],
            "seed": result["seed"],
            "mae": mae,
            "f1": f1,
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
        elif scope == "smoke" and verified:
            status = "smoke-verified"
        elif scope == "smoke":
            status = "smoke-unverified"
        else:
            status = "candidate"
        maes = [row["mae"] for row in rows]
        f1s = [row["f1"] for row in rows]
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
    _atomic_write_text(
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
    _atomic_write_text(csv_path, output.getvalue())
