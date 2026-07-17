"""Typed configuration loading and validation."""

from __future__ import annotations

import hashlib
import json
import os
import tomllib
from dataclasses import asdict, dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any


class ConfigError(ValueError):
    """Raised when a benchmark configuration is incomplete or inconsistent."""


@dataclass(frozen=True)
class DatasetConfig:
    id: str
    path_env: str
    default_path: str
    sha256: str
    size_bytes: int
    timezone: str
    mains_ac_types: tuple[str, ...]
    appliance_ac_types: tuple[str, ...]

    @property
    def path(self) -> Path:
        return Path(os.environ.get(self.path_env, self.default_path)).expanduser()


@dataclass(frozen=True)
class WindowConfig:
    dataset: str
    building: int
    start: str
    end: str


@dataclass(frozen=True)
class MetricPolicyConfig:
    id: str
    description: str
    source_url: str
    thresholds: dict[str, float]

    def threshold(self, appliance: str) -> float:
        try:
            return self.thresholds[appliance]
        except KeyError as exc:
            raise ConfigError(
                f"Metric policy {self.id!r} has no threshold for {appliance!r}"
            ) from exc


@dataclass(frozen=True)
class TaskConfig:
    id: str
    family: str
    profile: str
    description: str
    sample_period: int
    appliances: tuple[str, ...]
    metric_policy: str
    coverage_policy: str
    alignment_policy: str
    shared_meter_policy: str
    train: tuple[WindowConfig, ...]
    test: tuple[WindowConfig, ...]


@dataclass(frozen=True)
class BenchmarkConfig:
    datasets: dict[str, DatasetConfig]
    metric_policies: dict[str, MetricPolicyConfig]
    tasks: dict[str, TaskConfig]

    def task(self, task_id: str) -> TaskConfig:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.tasks))
            raise ConfigError(f"Unknown task {task_id!r}. Available: {available}") from exc

    def metric_policy(self, policy_id: str) -> MetricPolicyConfig:
        try:
            return self.metric_policies[policy_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.metric_policies))
            raise ConfigError(
                f"Unknown metric policy {policy_id!r}. Available: {available}"
            ) from exc

    def digest(self, task_id: str) -> str:
        task = self.task(task_id)
        payload = {
            "task": asdict(task),
            "datasets": {
                name: asdict(self.datasets[name])
                for name in sorted({w.dataset for w in (*task.train, *task.test)})
            },
            "metric_policy": asdict(self.metric_policy(task.metric_policy)),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        return hashlib.sha256(encoded).hexdigest()


def _read_toml(path: Path) -> dict[str, Any]:
    try:
        with path.open("rb") as handle:
            return tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise ConfigError(f"Could not read {path}: {exc}") from exc


def _config_root(config_dir: str | Path | None) -> Path:
    if config_dir is not None:
        return Path(config_dir)
    packaged = Path(str(files("nilmbench").joinpath("configs")))
    if packaged.is_dir():
        return packaged
    return Path(__file__).resolve().parents[2] / "configs"


def load_config(config_dir: str | Path | None = None) -> BenchmarkConfig:
    """Load the built-in or user-supplied TOML benchmark configuration."""
    root = _config_root(config_dir)
    dataset_doc = _read_toml(root / "datasets.toml")
    metric_doc = _read_toml(root / "metrics.toml")
    task_doc = _read_toml(root / "tasks.toml")

    datasets: dict[str, DatasetConfig] = {}
    for source in dataset_doc.get("dataset", []):
        raw = dict(source)
        mains_ac_types = tuple(raw.pop("mains_ac_types"))
        appliance_ac_types = tuple(raw.pop("appliance_ac_types"))
        dataset = DatasetConfig(
            **raw,
            mains_ac_types=mains_ac_types,
            appliance_ac_types=appliance_ac_types,
        )
        if len(dataset.sha256) != 64:
            raise ConfigError(f"Dataset {dataset.id} has an invalid SHA-256 digest")
        if dataset.id in datasets:
            raise ConfigError(f"Duplicate dataset id {dataset.id}")
        if not dataset.mains_ac_types or not dataset.appliance_ac_types:
            raise ConfigError(f"Dataset {dataset.id} has no AC type preferences")
        datasets[dataset.id] = dataset

    metric_policies: dict[str, MetricPolicyConfig] = {}
    for raw in metric_doc.get("metric_policy", []):
        policy = MetricPolicyConfig(**raw)
        if not policy.id or not policy.thresholds:
            raise ConfigError("Metric policies need an id and thresholds")
        if any(value <= 0 for value in policy.thresholds.values()):
            raise ConfigError(f"Metric policy {policy.id} has a non-positive threshold")
        if policy.id in metric_policies:
            raise ConfigError(f"Duplicate metric policy id {policy.id}")
        metric_policies[policy.id] = policy

    tasks: dict[str, TaskConfig] = {}
    for source in task_doc.get("task", []):
        raw = dict(source)
        train = tuple(WindowConfig(**item) for item in raw.pop("train"))
        test = tuple(WindowConfig(**item) for item in raw.pop("test"))
        appliances = tuple(raw.pop("appliances"))
        task = TaskConfig(
            **raw,
            appliances=appliances,
            train=train,
            test=test,
        )
        if task.id in tasks:
            raise ConfigError(f"Duplicate task id {task.id}")
        if task.family not in {"T1", "T2", "T3"}:
            raise ConfigError(f"Task {task.id} has unsupported family {task.family}")
        if task.coverage_policy not in {"warn", "strict"}:
            raise ConfigError(f"Task {task.id} has invalid coverage_policy")
        if task.alignment_policy not in {"joint", "per_appliance"}:
            raise ConfigError(f"Task {task.id} has invalid alignment_policy")
        if task.shared_meter_policy not in {"allow", "warn", "strict"}:
            raise ConfigError(f"Task {task.id} has invalid shared_meter_policy")
        if task.metric_policy not in metric_policies:
            raise ConfigError(
                f"Task {task.id} references unknown metric policy {task.metric_policy}"
            )
        if task.sample_period <= 0 or not task.appliances:
            raise ConfigError(f"Task {task.id} has invalid sampling or appliances")
        for window in (*task.train, *task.test):
            if window.dataset not in datasets:
                raise ConfigError(
                    f"Task {task.id} references unknown dataset {window.dataset}"
                )
            if window.start >= window.end:
                raise ConfigError(f"Task {task.id} has a non-positive window")
        policy = metric_policies[task.metric_policy]
        for appliance in task.appliances:
            policy.threshold(appliance)
        tasks[task.id] = task

    if not datasets or not metric_policies or not tasks:
        raise ConfigError("Datasets, metric policies, and tasks are required")
    return BenchmarkConfig(
        datasets=datasets,
        metric_policies=metric_policies,
        tasks=tasks,
    )
