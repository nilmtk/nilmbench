import hashlib
import json
from dataclasses import asdict

import pytest

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
        "run_scope": scope,
        "protocol_overrides": {
            "appliances": ["fridge"] if scope == "smoke" else None,
            "epochs": 1 if scope == "smoke" else None,
            "max_samples_per_window": max_samples,
            "sample_period": None,
            "sequence_length": sequence_length,
        },
        "study": None,
        "runtime": {
            "nilmbench_git_sha": "c" * 40,
            "nilmbench_git_dirty": dirty,
            "nilmtk_contrib_git_sha": "d" * 40,
            "nilmtk_contrib_git_dirty": False,
            "container_digest": "sha256:" + "e" * 64,
            "container_image": "ghcr.io/nilmtk/nilmbench:test-cuda",
            "gpu": "Test GPU",
            "cuda_available": True,
        },
        "run": {
            "params_by_alignment_group": {
                "fridge": {"sequence_length": sequence_length}
            },
            "elapsed_seconds_by_alignment_group": {"fridge": float(seed)},
            "trainable_parameters": {"fridge": 1234},
            "peak_accelerator_memory_bytes": {"fridge": 4096},
            "train_windows": {
                "fridge": [
                    {
                        "requested": asdict(TASK.train[0]),
                        "samples": 100,
                        "expected_samples": 100,
                        "aligned_sample_fraction": 1.0,
                    }
                ]
            },
            "test_windows": {
                "fridge": [
                    {
                        "requested": asdict(TASK.test[0]),
                        "samples": 100,
                        "expected_samples": 100,
                        "aligned_sample_fraction": 1.0,
                    }
                ]
            },
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


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1.0])
def test_invalid_metrics_are_rejected(tmp_path, bad_value):
    _write_result(tmp_path, 42, mae=bad_value)

    with pytest.raises(LeaderboardError, match="out-of-range"):
        _build(tmp_path)


@pytest.mark.parametrize(
    ("field", "bad_value", "message"),
    [
        ("elapsed_seconds_by_alignment_group", float("nan"), "elapsed"),
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
