import hashlib
import json
import math
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime, timedelta

import pytest

from nilmbench._contracts import (
    HPO_SELECTION_PROTOCOL,
    HPO_TUNING_SEED,
    VALIDATION_PROTOCOL,
    canonical_digest,
)
from nilmbench.leaderboard import (
    LeaderboardError,
    build_leaderboard,
    write_leaderboard,
)
from nilmbench.config import (
    BenchmarkConfig,
    DatasetConfig,
    MetricPolicyConfig,
    TaskConfig,
    TrustedRuntimeConfig,
    WindowConfig,
)
from nilmbench.registry import MODELS


DATASET = DatasetConfig(
    id="UKDALE",
    path_env="NILMBENCH_TEST_UKDALE",
    default_path="/data/ukdale.h5",
    sha256="b" * 64,
    size_bytes=123,
    timezone="Europe/London",
    mains_ac_types=("active",),
    appliance_ac_types=("active",),
)
POLICY = MetricPolicyConfig(
    id="test-thresholds",
    description="Test policy",
    source_url="https://example.invalid/policy",
    thresholds={"fridge": 50.0},
)
TASK = TaskConfig(
    id="corrected-t2-ukdale",
    family="T2",
    profile="corrected",
    description="Synthetic trusted leaderboard contract",
    sample_period=60,
    appliances=("fridge",),
    metric_policy=POLICY.id,
    coverage_policy="strict",
    alignment_policy="per_appliance",
    shared_meter_policy="warn",
    target_data_access="not_applicable",
    train=(WindowConfig("UKDALE", 1, "2020-01-01", "2020-01-02"),),
    test=(WindowConfig("UKDALE", 2, "2020-01-02", "2020-01-03"),),
    minimum_aligned_fraction=0.5,
)
CONFIG = BenchmarkConfig(
    datasets={DATASET.id: DATASET},
    metric_policies={POLICY.id: POLICY},
    tasks={TASK.id: TASK},
    trusted_runtimes=(
        TrustedRuntimeConfig(
            id="test-cuda",
            nilmbench_git_sha="c" * 40,
            nilmtk_contrib_git_sha="d" * 40,
            container_image="ghcr.io/nilmtk/nilmbench:test-cuda",
            container_digest="sha256:" + "e" * 64,
            hardware="Test GPU",
        ),
        TrustedRuntimeConfig(
            id="test-cuda-second-image",
            nilmbench_git_sha="c" * 40,
            nilmtk_contrib_git_sha="d" * 40,
            container_image="ghcr.io/nilmtk/nilmbench:test-cuda",
            container_digest="sha256:" + "f" * 64,
            hardware="Test GPU",
        ),
    ),
)


def _build(root):
    return build_leaderboard(root, config=CONFIG)


def _reseal(path, result):
    result.pop("result_id", None)
    result["result_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(result), encoding="utf-8")


def _write_result(
    root,
    seed,
    *,
    scope="full",
    dirty=False,
    mae=None,
    sequence_length=99,
    max_samples=None,
):
    expected_samples = min(1440, max_samples) if max_samples is not None else 1440

    def window_evidence(window):
        actual_start = datetime.fromisoformat(window.start)
        actual_end = actual_start + timedelta(
            seconds=TASK.sample_period * (expected_samples - 1)
        )
        return {
            "requested": asdict(window),
            "samples": expected_samples,
            "expected_samples": expected_samples,
            "sample_limit": max_samples,
            "aligned_sample_fraction": 1.0,
            "actual_start": actual_start.isoformat(),
            "actual_end": actual_end.isoformat(),
        }

    model_params = {
        "sequence_length": sequence_length,
        "n_epochs": 1 if scope == "smoke" else 10,
        "batch_size": 128,
        "learning_rate": 1e-3,
    }
    model_entry = MODELS["PatchTST"]
    result = {
        "schema_version": "1.2",
        "created_at": f"2026-07-17T00:00:{seed:02d}+00:00",
        "task": asdict(TASK),
        "task_config_sha256": CONFIG.digest(TASK.id),
        "metric_policy": asdict(POLICY),
        "dataset_manifests": {"UKDALE": asdict(DATASET)},
        "dataset_identities": {
            "UKDALE": {
                "id": "UKDALE",
                "path": "/data/ukdale.h5",
                "sha256": "b" * 64,
                "size_bytes": 123,
            }
        },
        "model": "PatchTST",
        "model_spec": {
            "module": model_entry.module,
            "class_name": model_entry.class_name,
            "family": model_entry.family,
        },
        "model_params": model_params,
        "model_params_sha256": hashlib.sha256(
            json.dumps(model_params, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest(),
        "seed": seed,
        "sample_period": 60,
        "appliances": ["fridge"],
        "max_samples_per_window": max_samples,
        "run_scope": scope,
        "protocol_overrides": {
            "appliances": ["fridge"] if scope == "smoke" else None,
            "epochs": 1 if scope == "smoke" else None,
            "max_samples_per_window": max_samples,
            "sample_period": None,
            "sequence_length": sequence_length,
            "model_selection": None,
        },
        "study": None,
        "runtime": {
            "nilmbench_git_sha": "c" * 40,
            "nilmbench_git_dirty": dirty,
            "nilmtk_contrib_git_sha": "d" * 40,
            "nilmtk_contrib_git_dirty": False,
            "nilmtk_contrib_version": "1.0.0",
            "container_digest": "sha256:" + "e" * 64,
            "container_image": "ghcr.io/nilmtk/nilmbench:test-cuda",
            "cpu": None,
            "gpu": "Test GPU",
            "torch": "2.6.0",
            "cuda_runtime": "12.4",
            "cuda_available": True,
        },
        "run": {
            "objective_mae": float(seed if mae is None else mae),
            "params_by_alignment_group": {
                "fridge": {
                    **model_params,
                    "seed": seed,
                    "mains_mean": 100.0,
                    "mains_std": 20.0,
                }
            },
            "elapsed_seconds_by_alignment_group": {"fridge": float(seed)},
            "trainable_parameters": {"fridge": 1234},
            "peak_accelerator_memory_bytes": {"fridge": 4096},
            "train_windows": {"fridge": [window_evidence(TASK.train[0])]},
            "test_windows": {"fridge": [window_evidence(TASK.test[0])]},
            "metrics": {
                "fridge": {
                    "mae": float(seed if mae is None else mae),
                    "f1": 0.5,
                    "activation_threshold_watts": 50.0,
                }
            },
        },
    }
    result["result_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path = root / f"seed-{seed}-{scope}-seq{sequence_length}" / "result.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(result), encoding="utf-8")
    return path


def _rekey_study(result):
    study = result["study"]
    spec = study["study_spec"]
    digest = canonical_digest(spec)
    name = f"{TASK.id}--PatchTST--tune-seed{HPO_TUNING_SEED}--{digest}"
    study["study_digest"] = digest
    study["study_name"] = name
    study["coordination"]["storage"] = f"optuna/{name}.sqlite3"
    for record in study["trial_records"]:
        record["study_name"] = name
        record["study_identity_sha256"] = digest
        record["study_spec"] = spec
        payload = {key: value for key, value in record.items() if key != "record_id"}
        record["record_id"] = canonical_digest(payload)
    study["trial_record_files"] = [
        f"optuna/{name}/trials/trial-{record['trial_number']:06d}.json"
        for record in study["trial_records"]
    ]
    result["protocol_overrides"]["model_selection"]["study_identity_sha256"] = digest


def _reseal_trial_record(record):
    payload = {key: value for key, value in record.items() if key != "record_id"}
    record["record_id"] = canonical_digest(payload)


def _attach_valid_study(path):
    result = json.loads(path.read_text(encoding="utf-8"))
    params = result["model_params"]
    spec = {
        "identity_schema": "nilmbench.optuna-study.v2",
        "runner": {
            "git_sha": result["runtime"]["nilmbench_git_sha"],
            "git_dirty": result["runtime"]["nilmbench_git_dirty"],
        },
        "contrib": {
            "git_sha": result["runtime"]["nilmtk_contrib_git_sha"],
            "git_dirty": result["runtime"]["nilmtk_contrib_git_dirty"],
            "version": result["runtime"]["nilmtk_contrib_version"],
            "model_module": result["model_spec"]["module"],
            "model_class": result["model_spec"]["class_name"],
        },
        "container": {
            "image": result["runtime"]["container_image"],
            "digest": result["runtime"]["container_digest"],
        },
        "device": {
            "requested": "auto",
            "cpu": result["runtime"]["cpu"],
            "gpu": result["runtime"]["gpu"],
            "torch": result["runtime"]["torch"],
            "cuda_runtime": result["runtime"]["cuda_runtime"],
            "cuda_available": result["runtime"]["cuda_available"],
        },
        "protocol": {
            "task_id": TASK.id,
            "task_family": TASK.family,
            "task_profile": TASK.profile,
            "task_config_sha256": result["task_config_sha256"],
            "target_data_access": TASK.target_data_access,
            "model": "PatchTST",
            "selection_protocol": HPO_SELECTION_PROTOCOL,
            "tuning_seed": HPO_TUNING_SEED,
            "appliances": result["appliances"],
            "sample_period": result["sample_period"],
            "max_samples_per_window": result["max_samples_per_window"],
            "epochs_override": result["protocol_overrides"]["epochs"],
            "sequence_length_override": result["protocol_overrides"]["sequence_length"],
            "alignment_policy": TASK.alignment_policy,
            "metric_policy": TASK.metric_policy,
            "validation": VALIDATION_PROTOCOL,
            "optimization": {
                "library": "optuna",
                "version": "4.5.0",
                "direction": "minimize",
                "sampler": "TPESampler",
                "sampler_seed": HPO_TUNING_SEED,
            },
        },
        "source_dataset_identities": result["dataset_identities"],
    }
    source_window = result["run"]["train_windows"]["fridge"][0]
    source_samples = source_window["samples"]
    validation_samples = math.ceil(
        source_samples * VALIDATION_PROTOCOL["validation_fraction"]
    )
    training_samples = source_samples - validation_samples
    start = datetime.fromisoformat(source_window["actual_start"])
    training_end = start + timedelta(
        seconds=result["sample_period"] * (training_samples - 1)
    )
    validation_start = training_end + timedelta(seconds=result["sample_period"])
    record = {
        "schema_version": "1.0",
        "created_at": "2026-07-17T00:00:00+00:00",
        "state": "COMPLETE",
        "study_name": "pending",
        "study_identity_sha256": "pending",
        "study_spec": spec,
        "trial_number": 0,
        "parameters": {
            "suggested": dict(params),
            "effective": dict(params),
            "resolved_by_alignment_group": {
                "fridge": {
                    **params,
                    "seed": HPO_TUNING_SEED,
                    "mains_mean": 100.0,
                    "mains_std": 20.0,
                }
            },
        },
        "validation": {
            "protocol": VALIDATION_PROTOCOL,
            "partitions": {
                "fridge": [
                    {
                        "source_window": source_window,
                        "training": {
                            "samples": training_samples,
                            "actual_start": source_window["actual_start"],
                            "actual_end": training_end.isoformat(),
                        },
                        "validation": {
                            "samples": validation_samples,
                            "actual_start": validation_start.isoformat(),
                            "actual_end": source_window["actual_end"],
                        },
                    }
                ]
            },
            "metrics": {
                "fridge": {
                    "mae": 1.0,
                    "f1": 0.5,
                    "activation_threshold_watts": 50.0,
                }
            },
            "objective_mae": 1.0,
            "elapsed_seconds": 1.0,
            "elapsed_seconds_by_alignment_group": {"fridge": 1.0},
        },
    }
    result["study"] = {
        "study_name": "pending",
        "study_spec": spec,
        "study_digest": "pending",
        "selection_protocol": HPO_SELECTION_PROTOCOL,
        "tuning_seed": HPO_TUNING_SEED,
        "coordination": {
            "backend": "sqlite",
            "storage": "pending",
            "scientific_source_of_truth": False,
        },
        "completed_trials": 1,
        "best_value": 1.0,
        "best_params": dict(params),
        "optuna_best_suggestions": dict(params),
        "trial_record_files": [],
        "trial_records": [record],
    }
    result["protocol_overrides"]["model_selection"] = {
        "method": "optuna-tpe",
        "selection_protocol": HPO_SELECTION_PROTOCOL,
        "tuning_seed": HPO_TUNING_SEED,
        "study_identity_sha256": "pending",
        "completed_trials": 1,
        "validation_protocol": VALIDATION_PROTOCOL["id"],
        "selected_parameters": dict(params),
    }
    _rekey_study(result)
    _reseal(path, result)
    return result


def test_three_clean_full_seeds_are_verified(tmp_path):
    for seed in (10, 20, 42):
        _write_result(tmp_path, seed)

    leaderboard = _build(tmp_path)

    assert leaderboard["source_result_count"] == 3
    assert len(leaderboard["entries"]) == 1
    entry = leaderboard["entries"][0]
    assert entry["status"] == "full-verified"
    assert entry["seeds"] == [10, 20, 42]
    assert entry["mae_mean"] == pytest.approx(24.0)
    assert entry["mae_std"] > 0
    assert entry["elapsed_seconds_mean"] == pytest.approx(24.0)
    assert entry["trainable_parameters_mean"] == 1234
    assert entry["peak_accelerator_memory_bytes_mean"] == 4096


def test_smoke_and_dirty_runs_cannot_become_verified(tmp_path):
    _write_result(tmp_path, 42, scope="smoke", dirty=True)

    entry = _build(tmp_path)["entries"][0]

    assert entry["status"] == "smoke-unverified"
    assert entry["scope"] == "smoke"
    assert entry["verification_failures"]


def test_clean_smoke_requires_all_declared_seeds(tmp_path):
    _write_result(tmp_path, 42, scope="smoke", max_samples=1024)

    partial = _build(tmp_path)["entries"][0]

    assert partial["status"] == "smoke-partial"
    for seed in (10, 20):
        _write_result(tmp_path, seed, scope="smoke", max_samples=1024)

    verified = _build(tmp_path)["entries"][0]
    assert verified["status"] == "smoke-verified"
    assert verified["seeds"] == [10, 20, 42]


def test_tampered_result_is_rejected(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"]["metrics"]["fridge"]["mae"] = 0.0
    path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(LeaderboardError, match="result_id"):
        _build(tmp_path)


def test_duplicate_seed_for_same_revision_is_rejected(tmp_path):
    original = _write_result(tmp_path, 42)
    duplicate = tmp_path / "rerun" / "result.json"
    duplicate.parent.mkdir()
    duplicate.write_bytes(original.read_bytes())

    with pytest.raises(LeaderboardError, match="Duplicate"):
        _build(tmp_path)


def test_different_container_digests_are_never_aggregated(tmp_path):
    first = _write_result(tmp_path, 10)
    result = json.loads(first.read_text(encoding="utf-8"))
    result["seed"] = 20
    result["run"]["params_by_alignment_group"]["fridge"]["seed"] = 20
    result["runtime"]["container_digest"] = "sha256:" + "f" * 64
    result.pop("result_id")
    result["result_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    second = tmp_path / "different-image" / "result.json"
    second.parent.mkdir()
    second.write_text(json.dumps(result), encoding="utf-8")

    leaderboard = _build(tmp_path)

    assert len(leaderboard["entries"]) == 2
    assert {entry["container_digest"] for entry in leaderboard["entries"]} == {
        "sha256:" + "e" * 64,
        "sha256:" + "f" * 64,
    }
    assert (
        len({entry["comparison_protocol_sha256"] for entry in leaderboard["entries"]})
        == 2
    )
    assert (
        len({entry["ranking_protocol_sha256"] for entry in leaderboard["entries"]})
        == 1
    )


def test_smoke_protocol_overrides_are_never_aggregated(tmp_path):
    _write_result(
        tmp_path,
        42,
        scope="smoke",
        sequence_length=99,
        max_samples=512,
    )
    _write_result(
        tmp_path,
        42,
        scope="smoke",
        sequence_length=299,
        max_samples=1024,
    )

    entries = _build(tmp_path)["entries"]

    assert len(entries) == 2
    assert {entry["sequence_length"] for entry in entries} == {99, 299}
    assert len({entry["protocol_overrides_sha256"] for entry in entries}) == 2
    assert len({entry["comparison_protocol_sha256"] for entry in entries}) == 2


def test_model_sequence_length_does_not_reset_public_rank(tmp_path):
    for seed in (10, 20, 42):
        _write_result(
            tmp_path / "short-sequence",
            seed,
            scope="smoke",
            sequence_length=99,
            max_samples=1024,
            mae=40.0,
        )
        _write_result(
            tmp_path / "long-sequence",
            seed,
            scope="smoke",
            sequence_length=299,
            max_samples=1024,
            mae=50.0,
        )

    entries = _build(tmp_path)["entries"]

    assert [entry["sequence_length"] for entry in entries] == [99, 299]
    assert len({entry["comparison_protocol_sha256"] for entry in entries}) == 2
    assert len({entry["ranking_protocol_sha256"] for entry in entries}) == 1
    assert [entry["rank"] for entry in entries] == [1, 2]
    ranking_protocol = entries[0]["ranking_protocol"]
    assert ranking_protocol["max_samples_per_window"] == 1024
    assert "effective_sequence_length" not in ranking_protocol
    assert "runtime" not in ranking_protocol


def test_json_and_csv_artifacts_are_deterministic(tmp_path):
    for seed in (10, 20, 42):
        _write_result(tmp_path / "results", seed)
    leaderboard = _build(tmp_path / "results")
    json_path = tmp_path / "leaderboard.json"
    csv_path = tmp_path / "leaderboard.csv"

    write_leaderboard(leaderboard, json_path, csv_path)
    first_json = json_path.read_bytes()
    first_csv = csv_path.read_bytes()
    write_leaderboard(leaderboard, json_path, csv_path)

    assert json_path.read_bytes() == first_json
    assert csv_path.read_bytes() == first_csv
    assert b"full-verified" in first_json
    assert b"full-verified" in first_csv
    assert b"ranking_protocol_sha256" in first_csv
    assert b",rank," in first_csv
    payload = json.loads(first_json)
    assert payload["artifacts"]["csv_name"] == "leaderboard.csv"
    assert payload["artifacts"]["csv_sha256"] == hashlib.sha256(first_csv).hexdigest()


@pytest.mark.parametrize("tamper", ["task", "manifest", "model"])
def test_resealed_untrusted_scientific_contract_is_rejected(tmp_path, tamper):
    path = _write_result(tmp_path, 42, scope="smoke", max_samples=1024)
    result = json.loads(path.read_text(encoding="utf-8"))
    if tamper == "task":
        result["task"]["description"] = "resealed description"
    elif tamper == "manifest":
        result["dataset_manifests"] = {}
    else:
        result["model_spec"]["class_name"] = "UntrustedPatchTST"
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="configuration|manifests|trusted"):
        _build(tmp_path)


def test_nonhex_container_digest_cannot_become_verified(tmp_path):
    for seed in (10, 20, 42):
        path = _write_result(tmp_path, seed, scope="smoke", max_samples=1024)
        result = json.loads(path.read_text(encoding="utf-8"))
        result["runtime"]["container_digest"] = "sha256:" + "z" * 64
        _reseal(path, result)

    entry = _build(tmp_path)["entries"][0]

    assert entry["status"] == "smoke-unverified"
    assert "missing immutable container_digest" in entry["verification_failures"]


def test_effective_model_parameters_are_never_aggregated(tmp_path):
    first = _write_result(tmp_path, 10, scope="smoke", max_samples=1024)
    second = _write_result(tmp_path, 20, scope="smoke", max_samples=1024)
    result = json.loads(second.read_text(encoding="utf-8"))
    result["model_params"]["learning_rate"] = 5e-4
    result["run"]["params_by_alignment_group"]["fridge"]["learning_rate"] = 5e-4
    result["model_params_sha256"] = hashlib.sha256(
        json.dumps(
            result["model_params"], sort_keys=True, separators=(",", ":")
        ).encode()
    ).hexdigest()
    _reseal(second, result)

    entries = _build(tmp_path)["entries"]

    assert first.exists()
    assert len(entries) == 2
    assert len({entry["model_params_sha256"] for entry in entries}) == 2


def test_missing_efficiency_measurement_prevents_verification(tmp_path):
    for seed in (10, 20, 42):
        path = _write_result(tmp_path, seed, scope="smoke", max_samples=1024)
        if seed == 20:
            result = json.loads(path.read_text(encoding="utf-8"))
            result["run"]["peak_accelerator_memory_bytes"] = {"fridge": None}
            _reseal(path, result)

    entries = _build(tmp_path)["entries"]

    assert len(entries) == 1
    assert entries[0]["status"] == "smoke-unverified"
    assert "missing peak accelerator memory" in entries[0]["verification_failures"]


def test_sparse_aligned_windows_are_rejected_even_when_resealed(tmp_path):
    path = _write_result(tmp_path, 42, scope="smoke", max_samples=1024)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"]["test_windows"]["fridge"][0]["aligned_sample_fraction"] = 0.1
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="minimum aligned"):
        _build(tmp_path)


def test_resealed_model_selection_without_trial_audit_is_rejected(tmp_path):
    path = _write_result(tmp_path, 42, scope="smoke", max_samples=1024)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["protocol_overrides"]["model_selection"] = {
        "method": "optuna-tpe",
        "study_identity_sha256": "f" * 64,
    }
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="without a study"):
        _build(tmp_path)


@pytest.mark.parametrize(
    ("bad_value", "message"),
    [
        (float("nan"), "non-finite JSON"),
        (float("inf"), "non-finite JSON"),
        (-1.0, "out-of-range"),
    ],
)
def test_invalid_metrics_are_rejected(tmp_path, bad_value, message):
    _write_result(tmp_path, 42, mae=bad_value)

    with pytest.raises(LeaderboardError, match=message):
        _build(tmp_path)


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        (
            "elapsed_seconds_by_alignment_group",
            float("nan"),
            "non-finite JSON",
        ),
        ("trainable_parameters", False, "parameter count"),
        ("peak_accelerator_memory_bytes", -1, "accelerator memory"),
    ],
)
def test_invalid_efficiency_measurements_are_rejected(
    tmp_path, field, bad_value, message
):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"][field]["fridge"] = bad_value
    result.pop("result_id")
    result["result_id"] = hashlib.sha256(
        json.dumps(result, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(LeaderboardError, match=message):
        _build(tmp_path)


@pytest.mark.parametrize("budget", ["samples", "epochs"])
def test_full_scope_cannot_hide_observed_smoke_budget(tmp_path, budget):
    if budget == "samples":
        _write_result(tmp_path, 42, scope="full", max_samples=512)
    else:
        path = _write_result(tmp_path, 42, scope="full")
        result = json.loads(path.read_text(encoding="utf-8"))
        result["protocol_overrides"]["epochs"] = 10
        _reseal(path, result)

    with pytest.raises(LeaderboardError, match="run_scope"):
        _build(tmp_path)


def test_full_scope_cannot_hide_reduced_effective_epochs(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["model_params"]["n_epochs"] = 1
    result["run"]["params_by_alignment_group"]["fridge"]["n_epochs"] = 1
    result["model_params_sha256"] = canonical_digest(result["model_params"])
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="effective epochs"):
        _build(tmp_path)


def test_task_window_expected_count_cannot_be_shrunk_and_resealed(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    window = result["run"]["test_windows"]["fridge"][0]
    window["samples"] = 100
    window["expected_samples"] = 100
    window["aligned_sample_fraction"] = 1.0
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="minimum aligned"):
        _build(tmp_path)


@pytest.mark.parametrize(
    "tamper", ["model_param", "parameter_type", "extra_field", "seed", "group"]
)
def test_resolved_model_parameters_are_exactly_bound(tmp_path, tamper):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    resolved = result["run"]["params_by_alignment_group"]["fridge"]
    if tamper == "model_param":
        resolved["learning_rate"] = 0.5
    elif tamper == "parameter_type":
        resolved["sequence_length"] = 99.0
    elif tamper == "extra_field":
        resolved["undisclosed"] = True
    elif tamper == "seed":
        resolved["seed"] = 10
    else:
        result["run"]["params_by_alignment_group"] = {"hostile": resolved}
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="resolved model parameters"):
        _build(tmp_path)


def test_efficiency_alignment_groups_cannot_use_fallback_or_extra_keys(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"]["elapsed_seconds_by_alignment_group"]["hostile"] = 0.001
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="elapsed_seconds.*groups"):
        _build(tmp_path)


def test_fixed_hpo_study_aggregates_distinct_evaluation_seeds(tmp_path):
    for seed in (10, 20, 42):
        path = _write_result(tmp_path, seed)
        _attach_valid_study(path)

    entries = _build(tmp_path)["entries"]

    assert len(entries) == 1
    assert entries[0]["seeds"] == [10, 20, 42]
    assert entries[0]["status"] == "full-verified"
    assert entries[0]["tuning_study_digest"]
    comparison = entries[0]["comparison_protocol"]
    assert comparison["effective_sequence_length"] == 99
    assert comparison["effective_epochs"] == 10
    assert comparison["model_selection"] == {
        "method": "optuna-tpe",
        "selection_protocol": HPO_SELECTION_PROTOCOL,
        "tuning_seed": HPO_TUNING_SEED,
        "completed_trials": 1,
        "validation_protocol": VALIDATION_PROTOCOL["id"],
    }
    assert "study_identity_sha256" not in json.dumps(comparison)
    assert "selected_parameters" not in json.dumps(comparison)


def test_comparison_protocol_separates_tuning_budget_and_runtime(tmp_path):
    _write_result(tmp_path, 10)
    tuned_one = _write_result(tmp_path, 20)
    _attach_valid_study(tuned_one)

    tuned_two = _write_result(tmp_path, 42)
    result = _attach_valid_study(tuned_two)
    second_record = deepcopy(result["study"]["trial_records"][0])
    second_record["trial_number"] = 1
    result["study"]["trial_records"].append(second_record)
    result["study"]["completed_trials"] = 2
    result["protocol_overrides"]["model_selection"]["completed_trials"] = 2
    _rekey_study(result)
    _reseal(tuned_two, result)

    other_runtime = _write_result(tmp_path, 30)
    result = json.loads(other_runtime.read_text(encoding="utf-8"))
    result["runtime"]["container_digest"] = "sha256:" + "f" * 64
    _reseal(other_runtime, result)

    entries = _build(tmp_path)["entries"]

    assert len(entries) == 4
    assert len({entry["comparison_protocol_sha256"] for entry in entries}) == 4
    protocols = [entry["comparison_protocol"] for entry in entries]
    assert {
        protocol["model_selection"]["completed_trials"]
        for protocol in protocols
        if protocol["model_selection"]
    } == {1, 2}
    assert {protocol["runtime"]["container_digest"] for protocol in protocols} == {
        "sha256:" + "e" * 64,
        "sha256:" + "f" * 64,
    }


@pytest.mark.parametrize("tamper", ["task_id", "sample_period_type"])
def test_rekeyed_hpo_study_cannot_lie_about_bound_task(tmp_path, tamper):
    path = _write_result(tmp_path, 42)
    result = _attach_valid_study(path)
    protocol = result["study"]["study_spec"]["protocol"]
    if tamper == "task_id":
        protocol["task_id"] = "hostile-task"
    else:
        protocol["sample_period"] = float(protocol["sample_period"])
    _rekey_study(result)
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="(task_id|sample_period).*not bound"):
        _build(tmp_path)


def test_dirty_persistent_hpo_is_rejected_not_merely_marked_unverified(tmp_path):
    path = _write_result(tmp_path, 42)
    result = _attach_valid_study(path)
    result["runtime"]["nilmbench_git_dirty"] = True
    result["study"]["study_spec"]["runner"]["git_dirty"] = True
    _rekey_study(result)
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="unsafe persistent HPO provenance"):
        _build(tmp_path)


def test_trial_record_rejects_resealed_unknown_fields(tmp_path):
    path = _write_result(tmp_path, 42)
    result = _attach_valid_study(path)
    record = result["study"]["trial_records"][0]
    record["validation"]["undisclosed_target_score"] = 0.0
    payload = {key: value for key, value in record.items() if key != "record_id"}
    record["record_id"] = canonical_digest(payload)
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="invalid trial audit record"):
        _build(tmp_path)


@pytest.mark.parametrize(
    "tamper",
    [
        "count_conservation",
        "blocked_ratio",
        "chronology",
        "invalid_timestamp",
        "window_containment",
        "trusted_source_binding",
    ],
)
def test_trial_partition_is_scientifically_bound(tmp_path, tamper):
    path = _write_result(tmp_path, 42)
    result = _attach_valid_study(path)
    record = result["study"]["trial_records"][0]
    partition = record["validation"]["partitions"]["fridge"][0]
    source_samples = partition["source_window"]["samples"]
    if tamper == "count_conservation":
        partition["training"]["samples"] -= 1
    elif tamper == "blocked_ratio":
        partition["training"]["samples"] = source_samples - 1
        partition["validation"]["samples"] = 1
    elif tamper == "chronology":
        partition["validation"]["actual_start"] = partition["training"]["actual_end"]
    elif tamper == "invalid_timestamp":
        partition["training"]["actual_end"] = "not-a-timestamp"
    elif tamper == "window_containment":
        partition["training"]["actual_start"] = "2019-12-31T23:59:00"
    else:
        partition["source_window"] = deepcopy(partition["source_window"])
        partition["source_window"]["untrusted_note"] = "different source evidence"
    _reseal_trial_record(record)
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="invalid trial audit record|not bound"):
        _build(tmp_path)


def test_study_selection_must_match_the_best_immutable_trial(tmp_path):
    path = _write_result(tmp_path, 42)
    result = _attach_valid_study(path)
    result["study"]["optuna_best_suggestions"] = {"batch_size": 128}
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="not supported by the best trial"):
        _build(tmp_path)


def test_run_objective_must_equal_the_appliance_metric_mean(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"]["objective_mae"] = 0.0
    _reseal(path, result)

    with pytest.raises(LeaderboardError, match="objective is not supported"):
        _build(tmp_path)


def test_overflowing_json_number_is_rejected_before_result_id(tmp_path):
    path = _write_result(tmp_path, 42)
    text = path.read_text(encoding="utf-8")
    path.write_text(text.replace('"mae": 42.0', '"mae": 1e309'), encoding="utf-8")

    with pytest.raises(LeaderboardError, match="non-finite JSON number"):
        _build(tmp_path)
