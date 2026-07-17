"""Shared fail-closed contracts for benchmark and tuning artifacts."""

from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime
from typing import Any


HPO_SELECTION_PROTOCOL = "tune-once-freeze-v1"
HPO_TUNING_SEED = 42
RESOLVED_RUNTIME_PARAMETERS = frozenset({"seed", "mains_mean", "mains_std"})
VALIDATION_PROTOCOL = {
    "id": "source-train-blocked-holdout-v1",
    "source": "task.train",
    "strategy": "last 20 percent of each source training window",
    "validation_fraction": 0.2,
    "task_test_access": "forbidden",
}


def canonical_digest(payload: Any) -> str:
    """Hash canonical standards-compliant JSON."""
    return hashlib.sha256(
        json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode()
    ).hexdigest()


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite JSON constant {value!r}")


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"duplicate JSON key {key!r}")
        result[key] = value
    return result


def _assert_finite_json(value: Any, location: str = "$") -> None:
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError(f"non-finite JSON number at {location}")
    if isinstance(value, dict):
        for key, item in value.items():
            _assert_finite_json(item, f"{location}.{key}")
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _assert_finite_json(item, f"{location}[{index}]")


def strict_json_loads(text: str) -> Any:
    """Load strict JSON, rejecting duplicate keys and every non-finite number."""
    value = json.loads(
        text,
        parse_constant=_reject_constant,
        object_pairs_hook=_reject_duplicate_keys,
    )
    # Python's JSON parser accepts finite-looking overflow such as ``1e309``.
    _assert_finite_json(value)
    return value


def is_git_sha(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value)
    )


def is_sha256(value: Any) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 64
        and all(character in "0123456789abcdef" for character in value)
    )


def validate_persistent_hpo_provenance(provenance: Any) -> None:
    """Reject unknown or mutable provenance before persistent HPO is created."""
    if not isinstance(provenance, dict):
        raise ValueError("Persistent HPO requires an object-valued runtime provenance")
    failures: list[str] = []
    for key in ("nilmbench_git_sha", "nilmtk_contrib_git_sha"):
        if not is_git_sha(provenance.get(key)):
            failures.append(f"valid {key}")
    for key in ("nilmbench_git_dirty", "nilmtk_contrib_git_dirty"):
        if provenance.get(key) is not False:
            failures.append(f"{key}=false")
    if (
        not isinstance(provenance.get("nilmtk_contrib_version"), str)
        or not provenance["nilmtk_contrib_version"].strip()
    ):
        failures.append("known nilmtk_contrib_version")
    if (
        not isinstance(provenance.get("container_image"), str)
        or not provenance["container_image"].strip()
    ):
        failures.append("known container_image")
    digest = provenance.get("container_digest")
    if (
        not isinstance(digest, str)
        or not digest.startswith("sha256:")
        or not is_sha256(digest[7:])
    ):
        failures.append("immutable container_digest")
    if not provenance.get("cpu") and not provenance.get("gpu"):
        failures.append("known CPU or GPU identity")
    if provenance.get("gpu") and provenance.get("cuda_available") is not True:
        failures.append("available CUDA runtime for GPU identity")
    if failures:
        raise ValueError(
            "Persistent HPO requires clean, immutable, known provenance: "
            + ", ".join(failures)
        )


def alignment_groups(appliances: list[str] | tuple[str, ...], policy: str) -> set[str]:
    return set(appliances) if policy == "per_appliance" else {"joint"}


def validate_resolved_parameters(
    values: Any,
    *,
    model_params: dict[str, Any],
    expected_groups: set[str],
    expected_seed: int,
) -> None:
    """Bind each model instance's resolved parameters to the declared model."""
    reserved = set(model_params) & RESOLVED_RUNTIME_PARAMETERS
    if reserved:
        raise ValueError(
            "model_params uses reserved runtime fields: " + ", ".join(sorted(reserved))
        )
    if not isinstance(values, dict) or set(values) != expected_groups:
        raise ValueError("resolved parameters have invalid alignment groups")
    expected_keys = set(model_params) | RESOLVED_RUNTIME_PARAMETERS
    for group, resolved in values.items():
        if not isinstance(resolved, dict) or set(resolved) != expected_keys:
            raise ValueError(f"resolved parameters for {group!r} have invalid fields")
        for name, expected in model_params.items():
            if resolved[name] != expected:
                raise ValueError(
                    f"resolved parameter {name!r} for {group!r} is not model_params"
                )
        seed = resolved["seed"]
        if isinstance(seed, bool) or not isinstance(seed, int) or seed != expected_seed:
            raise ValueError(f"resolved seed for {group!r} is not the declared seed")
        mean = resolved["mains_mean"]
        std = resolved["mains_std"]
        if (
            isinstance(mean, bool)
            or not isinstance(mean, (int, float))
            or not math.isfinite(mean)
            or isinstance(std, bool)
            or not isinstance(std, (int, float))
            or not math.isfinite(std)
            or std <= 0
        ):
            raise ValueError(f"resolved normalization for {group!r} is invalid")


def validate_trial_record(
    record: Any,
    *,
    study_name: str,
    study_digest: str,
    study_spec: dict[str, Any],
    expected_trial_number: int | None = None,
    expected_suggestions: dict[str, Any] | None = None,
    expected_objective: float | None = None,
) -> None:
    """Validate one immutable trial record down to its scientific evidence."""
    required = {
        "schema_version",
        "created_at",
        "state",
        "study_name",
        "study_identity_sha256",
        "study_spec",
        "trial_number",
        "parameters",
        "validation",
        "record_id",
    }
    if not isinstance(record, dict) or set(record) != required:
        raise ValueError("trial audit record has invalid top-level fields")
    payload = {key: value for key, value in record.items() if key != "record_id"}
    if record["record_id"] != canonical_digest(payload):
        raise ValueError("trial audit record_id does not match its contents")
    if (
        record["schema_version"] != "1.0"
        or record["state"] != "COMPLETE"
        or record["study_name"] != study_name
        or record["study_identity_sha256"] != study_digest
        or record["study_spec"] != study_spec
    ):
        raise ValueError("trial audit record is not bound to its study")
    try:
        created_at = datetime.fromisoformat(record["created_at"])
    except (TypeError, ValueError) as exc:
        raise ValueError("trial audit record has an invalid created_at") from exc
    if created_at.tzinfo is None:
        raise ValueError("trial audit record created_at must include a timezone")
    number = record["trial_number"]
    if isinstance(number, bool) or not isinstance(number, int) or number < 0:
        raise ValueError("trial audit record has an invalid trial_number")
    if expected_trial_number is not None and number != expected_trial_number:
        raise ValueError("trial audit record has the wrong trial_number")

    parameters = record["parameters"]
    if not isinstance(parameters, dict) or set(parameters) != {
        "suggested",
        "effective",
        "resolved_by_alignment_group",
    }:
        raise ValueError("trial audit parameters have invalid fields")
    suggested = parameters["suggested"]
    effective = parameters["effective"]
    if not isinstance(suggested, dict) or not isinstance(effective, dict):
        raise ValueError("trial audit parameters must be objects")
    if expected_suggestions is not None and suggested != expected_suggestions:
        raise ValueError("trial suggestions do not match persistent study state")
    if any(
        name not in effective or effective[name] != value
        for name, value in suggested.items()
    ):
        raise ValueError("trial suggestions are not present in effective parameters")
    protocol = study_spec.get("protocol", {})
    validate_resolved_parameters(
        parameters["resolved_by_alignment_group"],
        model_params=effective,
        expected_groups=alignment_groups(
            protocol.get("appliances", []), protocol.get("alignment_policy", "")
        ),
        expected_seed=protocol.get("tuning_seed"),
    )

    validation = record["validation"]
    if not isinstance(validation, dict) or set(validation) != {
        "protocol",
        "partitions",
        "metrics",
        "objective_mae",
        "elapsed_seconds",
        "elapsed_seconds_by_alignment_group",
    }:
        raise ValueError("trial audit validation has invalid fields")
    if validation["protocol"] != VALIDATION_PROTOCOL:
        raise ValueError("trial audit uses an unknown validation protocol")
    groups = alignment_groups(
        protocol.get("appliances", []), protocol.get("alignment_policy", "")
    )
    if (
        not isinstance(validation["partitions"], dict)
        or set(validation["partitions"]) != groups
    ):
        raise ValueError("trial audit partitions have invalid alignment groups")
    if (
        not isinstance(validation["elapsed_seconds_by_alignment_group"], dict)
        or set(validation["elapsed_seconds_by_alignment_group"]) != groups
    ):
        raise ValueError("trial audit elapsed times have invalid alignment groups")
    metrics = validation["metrics"]
    if not isinstance(metrics, dict) or set(metrics) != set(
        protocol.get("appliances", [])
    ):
        raise ValueError("trial audit metrics do not match study appliances")
    maes: list[float] = []
    for appliance, values in metrics.items():
        if not isinstance(values, dict) or set(values) != {
            "mae",
            "f1",
            "activation_threshold_watts",
        }:
            raise ValueError(
                f"trial audit metrics for {appliance!r} have invalid fields"
            )
        mae, f1, threshold = (
            values["mae"],
            values["f1"],
            values["activation_threshold_watts"],
        )
        if (
            any(
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                for value in (mae, f1, threshold)
            )
            or mae < 0
            or not 0 <= f1 <= 1
            or threshold <= 0
        ):
            raise ValueError(f"trial audit metrics for {appliance!r} are invalid")
        maes.append(float(mae))
    objective = validation["objective_mae"]
    if (
        isinstance(objective, bool)
        or not isinstance(objective, (int, float))
        or not math.isfinite(objective)
        or objective < 0
        or not math.isclose(objective, sum(maes) / len(maes), rel_tol=1e-12)
    ):
        raise ValueError("trial audit objective is invalid")
    if expected_objective is not None and not math.isclose(
        objective, expected_objective, rel_tol=1e-12
    ):
        raise ValueError("trial audit objective does not match persistent study state")
    elapsed_values = [
        validation["elapsed_seconds"],
        *validation["elapsed_seconds_by_alignment_group"].values(),
    ]
    if any(
        isinstance(value, bool)
        or not isinstance(value, (int, float))
        or not math.isfinite(value)
        or value <= 0
        for value in elapsed_values
    ):
        raise ValueError("trial audit elapsed time is invalid")
