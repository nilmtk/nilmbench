import hashlib

import pytest

from nilmbench.config import (
    BenchmarkConfig,
    DatasetConfig,
    MetricPolicyConfig,
    TaskConfig,
    WindowConfig,
)
from nilmbench.data import (
    DataError,
    DatasetIdentity,
    LoadedWindow,
    load_split,
    verify_dataset,
)


def _dataset(path, *, size=None, sha256=None):
    content = path.read_bytes()
    return DatasetConfig(
        id="TEST",
        path_env="NILMBENCH_TEST_UNUSED",
        default_path=str(path),
        sha256=sha256 or hashlib.sha256(content).hexdigest(),
        size_bytes=len(content) if size is None else size,
        timezone="UTC",
        mains_ac_types=("active",),
        appliance_ac_types=("active",),
    )


def test_dataset_identity_records_verified_file(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"real benchmark bytes")

    identity = verify_dataset(_dataset(path))

    assert identity.path == str(path.resolve())
    assert identity.size_bytes == path.stat().st_size
    assert identity.sha256 == hashlib.sha256(path.read_bytes()).hexdigest()


def test_dataset_identity_rejects_wrong_size_before_training(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"wrong size")

    with pytest.raises(DataError, match="bytes"):
        verify_dataset(_dataset(path, size=999))


def test_dataset_identity_rejects_wrong_checksum_before_training(tmp_path):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"wrong checksum")

    with pytest.raises(DataError, match="SHA-256"):
        verify_dataset(_dataset(path, sha256="0" * 64))


def _coverage_config(path, policy):
    dataset = _dataset(path)
    window = WindowConfig("TEST", 1, "2020-01-01", "2020-01-02")
    metric_policy = MetricPolicyConfig(
        id="test",
        description="test",
        source_url="https://example.invalid",
        thresholds={"fridge": 1.0},
    )
    task = TaskConfig(
        id="coverage-test",
        family="T1",
        profile="corrected",
        description="test",
        sample_period=60,
        appliances=("fridge",),
        metric_policy=metric_policy.id,
        coverage_policy=policy,
        alignment_policy="per_appliance",
        shared_meter_policy="warn",
        target_data_access="not_applicable",
        train=(window,),
        test=(window,),
        minimum_aligned_fraction=0.5,
    )
    return (
        BenchmarkConfig(
            datasets={dataset.id: dataset},
            metric_policies={metric_policy.id: metric_policy},
            tasks={task.id: task},
        ),
        task,
        window,
    )


@pytest.mark.parametrize("policy", ["strict", "warn"])
def test_aligned_sample_coverage_is_enforced_or_warned(tmp_path, monkeypatch, policy):
    path = tmp_path / "dataset.h5"
    path.write_bytes(b"data")
    config, task, window = _coverage_config(path, policy)
    loaded = LoadedWindow(
        requested=window,
        effective_start=window.start,
        effective_end=window.end,
        available_start=window.start,
        available_end=window.end,
        actual_start=window.start,
        actual_end=window.end,
        samples=10,
        expected_samples=100,
        sample_limit=None,
        aligned_sample_fraction=0.1,
        resolved_appliances={"fridge": ("fridge#1",)},
        resolved_meters={"fridge": ("meter#1",)},
        shared_meter_appliances={"fridge": ()},
        resolved_mains_ac_type="active",
        resolved_appliance_ac_types={"fridge": "active"},
    )
    monkeypatch.setattr(
        "nilmbench.data.verify_dataset",
        lambda dataset: DatasetIdentity(dataset.id, str(path), 4, dataset.sha256),
    )
    monkeypatch.setattr(
        "nilmbench.data._load_one",
        lambda *args, **kwargs: (object(), {"fridge": object()}, loaded),
    )

    if policy == "strict":
        with pytest.raises(DataError, match="aligned sample fraction"):
            load_split(config, task, task.train, ("fridge",), 60)
    else:
        with pytest.warns(UserWarning, match="aligned sample fraction"):
            split = load_split(config, task, task.train, ("fridge",), 60)
        assert split.windows == [loaded]
