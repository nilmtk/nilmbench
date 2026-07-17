import hashlib
import json

import pytest

from nilmbench.leaderboard import (
    LeaderboardError,
    build_leaderboard,
    write_leaderboard,
)


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
    result = {
        "schema_version": "1.1",
        "created_at": f"2026-07-17T00:00:{seed:02d}+00:00",
        "task": {
            "id": "corrected-t2-ukdale",
            "family": "T2",
            "profile": "corrected",
        },
        "task_config_sha256": "a" * 64,
        "dataset_manifests": {
            "UKDALE": {"sha256": "b" * 64, "size_bytes": 123}
        },
        "dataset_identities": {
            "UKDALE": {"sha256": "b" * 64, "size_bytes": 123}
        },
        "model": "PatchTST",
        "seed": seed,
        "sample_period": 60,
        "run_scope": scope,
        "protocol_overrides": {
            "appliances": ["fridge"] if scope == "smoke" else None,
            "epochs": 1 if scope == "smoke" else None,
            "max_samples_per_window": max_samples,
            "sample_period": None,
            "sequence_length": sequence_length,
        },
        "runtime": {
            "nilmbench_git_sha": "c" * 40,
            "nilmbench_git_dirty": dirty,
            "nilmtk_contrib_git_sha": "d" * 40,
            "nilmtk_contrib_git_dirty": False,
            "container_digest": "sha256:" + "e" * 64,
            "gpu": "Test GPU",
        },
        "run": {
            "params_by_alignment_group": {
                "fridge": {"sequence_length": sequence_length}
            },
            "elapsed_seconds_by_alignment_group": {"fridge": float(seed)},
            "trainable_parameters": {"fridge": 1234},
            "peak_accelerator_memory_bytes": {"fridge": 4096},
            "metrics": {
                "fridge": {
                    "mae": float(seed if mae is None else mae),
                    "f1": 0.5,
                    "activation_threshold_watts": 50.0,
                }
            }
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

    leaderboard = build_leaderboard(tmp_path)

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

    entry = build_leaderboard(tmp_path)["entries"][0]

    assert entry["status"] == "smoke-unverified"
    assert entry["scope"] == "smoke"
    assert entry["verification_failures"]


def test_clean_smoke_requires_all_declared_seeds(tmp_path):
    _write_result(tmp_path, 42, scope="smoke", max_samples=1024)

    partial = build_leaderboard(tmp_path)["entries"][0]

    assert partial["status"] == "smoke-partial"
    for seed in (10, 20):
        _write_result(tmp_path, seed, scope="smoke", max_samples=1024)

    verified = build_leaderboard(tmp_path)["entries"][0]
    assert verified["status"] == "smoke-verified"
    assert verified["seeds"] == [10, 20, 42]


def test_tampered_result_is_rejected(tmp_path):
    path = _write_result(tmp_path, 42)
    result = json.loads(path.read_text(encoding="utf-8"))
    result["run"]["metrics"]["fridge"]["mae"] = 0.0
    path.write_text(json.dumps(result), encoding="utf-8")

    with pytest.raises(LeaderboardError, match="result_id"):
        build_leaderboard(tmp_path)


def test_duplicate_seed_for_same_revision_is_rejected(tmp_path):
    original = _write_result(tmp_path, 42)
    duplicate = tmp_path / "rerun" / "result.json"
    duplicate.parent.mkdir()
    duplicate.write_bytes(original.read_bytes())

    with pytest.raises(LeaderboardError, match="Duplicate"):
        build_leaderboard(tmp_path)


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

    leaderboard = build_leaderboard(tmp_path)

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

    entries = build_leaderboard(tmp_path)["entries"]

    assert len(entries) == 2
    assert {entry["sequence_length"] for entry in entries} == {99, 299}
    assert len({entry["protocol_overrides_sha256"] for entry in entries}) == 2


def test_json_and_csv_artifacts_are_deterministic(tmp_path):
    for seed in (10, 20, 42):
        _write_result(tmp_path / "results", seed)
    leaderboard = build_leaderboard(tmp_path / "results")
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


@pytest.mark.parametrize("bad_value", [float("nan"), float("inf"), -1.0])
def test_invalid_metrics_are_rejected(tmp_path, bad_value):
    _write_result(tmp_path, 42, mae=bad_value)

    with pytest.raises(LeaderboardError, match="out-of-range"):
        build_leaderboard(tmp_path)


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
        build_leaderboard(tmp_path)
