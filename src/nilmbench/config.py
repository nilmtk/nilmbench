"""Typed configuration loading and validation."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import tomllib
from dataclasses import asdict, dataclass
from datetime import datetime
from importlib.resources import files
from numbers import Real
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class ConfigError(ValueError):
    """Raised when a benchmark configuration is incomplete or inconsistent."""


_ID_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]*\Z")
_ENV_PATTERN = re.compile(r"[A-Z][A-Z0-9_]*\Z")
_SHA256_PATTERN = re.compile(r"[0-9a-fA-F]{64}\Z")


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
class TrustedRuntimeConfig:
    id: str
    nilmbench_git_sha: str
    nilmtk_contrib_git_sha: str
    container_image: str
    container_digest: str
    hardware: str


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
    target_data_access: str
    train: tuple[WindowConfig, ...]
    test: tuple[WindowConfig, ...]
    minimum_aligned_fraction: float = 0.0
    target_label_fraction: float | None = None


@dataclass(frozen=True)
class BenchmarkConfig:
    datasets: dict[str, DatasetConfig]
    metric_policies: dict[str, MetricPolicyConfig]
    tasks: dict[str, TaskConfig]
    trusted_runtimes: tuple[TrustedRuntimeConfig, ...] = ()

    def task(self, task_id: str) -> TaskConfig:
        try:
            return self.tasks[task_id]
        except KeyError as exc:
            available = ", ".join(sorted(self.tasks))
            raise ConfigError(
                f"Unknown task {task_id!r}. Available: {available}"
            ) from exc

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


def _table_entries(document: dict[str, Any], key: str, path: Path) -> list[Any]:
    entries = document.get(key)
    if not isinstance(entries, list) or not entries:
        raise ConfigError(f"{path} must define at least one [[{key}]] table")
    return entries


def _valid_id(value: Any) -> bool:
    return isinstance(value, str) and _ID_PATTERN.fullmatch(value) is not None


def _parse_window_time(value: Any, task_id: str) -> datetime:
    if not isinstance(value, str):
        raise ConfigError(f"Task {task_id} window timestamps must be strings")
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ConfigError(f"Task {task_id} has invalid timestamp {value!r}") from exc
    if parsed.tzinfo is not None:
        raise ConfigError(f"Task {task_id} window timestamps must be timezone-naive")
    return parsed


def load_config(config_dir: str | Path | None = None) -> BenchmarkConfig:
    """Load the built-in or user-supplied TOML benchmark configuration."""
    root = _config_root(config_dir)
    dataset_doc = _read_toml(root / "datasets.toml")
    metric_doc = _read_toml(root / "metrics.toml")
    task_doc = _read_toml(root / "tasks.toml")
    runtime_path = root / "runtimes.toml"
    runtime_doc = _read_toml(runtime_path) if runtime_path.is_file() else {}

    datasets: dict[str, DatasetConfig] = {}
    for source in _table_entries(dataset_doc, "dataset", root / "datasets.toml"):
        try:
            raw = dict(source)
            mains_ac_types = tuple(raw.pop("mains_ac_types"))
            appliance_ac_types = tuple(raw.pop("appliance_ac_types"))
            dataset = DatasetConfig(
                **raw,
                mains_ac_types=mains_ac_types,
                appliance_ac_types=appliance_ac_types,
            )
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"Invalid dataset entry: {exc}") from exc
        if not _valid_id(dataset.id):
            raise ConfigError(f"Dataset has an invalid id {dataset.id!r}")
        if _SHA256_PATTERN.fullmatch(dataset.sha256) is None:
            raise ConfigError(f"Dataset {dataset.id} has an invalid SHA-256 digest")
        if (
            isinstance(dataset.size_bytes, bool)
            or not isinstance(dataset.size_bytes, int)
            or dataset.size_bytes <= 0
        ):
            raise ConfigError(f"Dataset {dataset.id} has an invalid size_bytes")
        if _ENV_PATTERN.fullmatch(dataset.path_env) is None:
            raise ConfigError(f"Dataset {dataset.id} has an invalid path_env")
        if not dataset.default_path:
            raise ConfigError(f"Dataset {dataset.id} has no default_path")
        try:
            ZoneInfo(dataset.timezone)
        except (TypeError, ZoneInfoNotFoundError) as exc:
            raise ConfigError(f"Dataset {dataset.id} has an invalid timezone") from exc
        if dataset.id in datasets:
            raise ConfigError(f"Duplicate dataset id {dataset.id}")
        ac_types = (*dataset.mains_ac_types, *dataset.appliance_ac_types)
        if (
            not dataset.mains_ac_types
            or not dataset.appliance_ac_types
            or any(not isinstance(value, str) or not value for value in ac_types)
            or len(set(dataset.mains_ac_types)) != len(dataset.mains_ac_types)
            or len(set(dataset.appliance_ac_types)) != len(dataset.appliance_ac_types)
        ):
            raise ConfigError(f"Dataset {dataset.id} has no AC type preferences")
        datasets[dataset.id] = dataset

    metric_policies: dict[str, MetricPolicyConfig] = {}
    for raw in _table_entries(metric_doc, "metric_policy", root / "metrics.toml"):
        try:
            policy = MetricPolicyConfig(**raw)
        except TypeError as exc:
            raise ConfigError(f"Invalid metric policy entry: {exc}") from exc
        if (
            not _valid_id(policy.id)
            or not isinstance(policy.thresholds, dict)
            or not policy.thresholds
        ):
            raise ConfigError("Metric policies need an id and thresholds")
        for appliance, value in policy.thresholds.items():
            if not isinstance(appliance, str) or not appliance:
                raise ConfigError(f"Metric policy {policy.id} has an invalid appliance")
            if (
                isinstance(value, bool)
                or not isinstance(value, Real)
                or not math.isfinite(value)
                or value <= 0
            ):
                raise ConfigError(f"Metric policy {policy.id} has an invalid threshold")
        if policy.id in metric_policies:
            raise ConfigError(f"Duplicate metric policy id {policy.id}")
        metric_policies[policy.id] = policy

    tasks: dict[str, TaskConfig] = {}
    for source in _table_entries(task_doc, "task", root / "tasks.toml"):
        try:
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
        except (KeyError, TypeError) as exc:
            raise ConfigError(f"Invalid task entry: {exc}") from exc
        if not _valid_id(task.id):
            raise ConfigError(f"Task has an invalid id {task.id!r}")
        if task.id in tasks:
            raise ConfigError(f"Duplicate task id {task.id}")
        if task.family not in {"T1", "T2", "T3"}:
            raise ConfigError(f"Task {task.id} has unsupported family {task.family}")
        if task.coverage_policy not in {"warn", "strict"}:
            raise ConfigError(f"Task {task.id} has invalid coverage_policy")
        if (
            isinstance(task.minimum_aligned_fraction, bool)
            or not isinstance(task.minimum_aligned_fraction, Real)
            or not math.isfinite(task.minimum_aligned_fraction)
            or not 0 <= task.minimum_aligned_fraction <= 1
            or (task.profile == "corrected" and task.minimum_aligned_fraction == 0)
        ):
            raise ConfigError(f"Task {task.id} has invalid minimum_aligned_fraction")
        if task.alignment_policy not in {"joint", "per_appliance"}:
            raise ConfigError(f"Task {task.id} has invalid alignment_policy")
        if task.shared_meter_policy not in {"allow", "warn", "strict"}:
            raise ConfigError(f"Task {task.id} has invalid shared_meter_policy")
        if task.family == "T3":
            if task.target_data_access not in {
                "none",
                "unlabeled_target_mains",
                "labeled_target_appliances",
            }:
                raise ConfigError(f"Task {task.id} has invalid target_data_access")
        elif task.target_data_access != "not_applicable":
            raise ConfigError(
                f"Non-transfer task {task.id} cannot access target-domain data"
            )
        if task.target_data_access == "labeled_target_appliances":
            if task.target_label_fraction is None or not (
                0 < task.target_label_fraction <= 1
            ):
                raise ConfigError(
                    f"Task {task.id} needs a target_label_fraction in (0, 1]"
                )
        elif task.target_label_fraction is not None:
            raise ConfigError(
                f"Task {task.id} has a target_label_fraction without labeled access"
            )
        if task.metric_policy not in metric_policies:
            raise ConfigError(
                f"Task {task.id} references unknown metric policy {task.metric_policy}"
            )
        if (
            isinstance(task.sample_period, bool)
            or not isinstance(task.sample_period, int)
            or task.sample_period <= 0
            or not task.appliances
            or any(
                not isinstance(appliance, str) or not appliance
                for appliance in task.appliances
            )
            or len(set(task.appliances)) != len(task.appliances)
            or not task.train
            or not task.test
        ):
            raise ConfigError(f"Task {task.id} has invalid sampling or appliances")
        for window in (*task.train, *task.test):
            if (
                isinstance(window.building, bool)
                or not isinstance(window.building, int)
                or window.building <= 0
            ):
                raise ConfigError(f"Task {task.id} has an invalid building")
            if window.dataset not in datasets:
                raise ConfigError(
                    f"Task {task.id} references unknown dataset {window.dataset}"
                )
            if _parse_window_time(window.start, task.id) >= _parse_window_time(
                window.end, task.id
            ):
                raise ConfigError(f"Task {task.id} has a non-positive window")
        if len(set(task.train)) != len(task.train) or len(set(task.test)) != len(
            task.test
        ):
            raise ConfigError(f"Task {task.id} contains duplicate windows")
        policy = metric_policies[task.metric_policy]
        for appliance in task.appliances:
            policy.threshold(appliance)
        tasks[task.id] = task

    if not datasets or not metric_policies or not tasks:
        raise ConfigError("Datasets, metric policies, and tasks are required")
    raw_runtimes = runtime_doc.get("runtime", [])
    if not isinstance(raw_runtimes, list):
        raise ConfigError(f"{runtime_path} runtime entries must be a list")
    trusted_runtimes = []
    for raw in raw_runtimes:
        try:
            runtime = TrustedRuntimeConfig(**raw)
        except TypeError as exc:
            raise ConfigError(f"Invalid trusted runtime entry: {exc}") from exc
        if not _valid_id(runtime.id):
            raise ConfigError("Trusted runtime has an invalid id")
        if not re.fullmatch(r"[0-9a-f]{40}", runtime.nilmtk_contrib_git_sha):
            raise ConfigError(
                f"Trusted runtime {runtime.id} has an invalid contrib SHA"
            )
        if not re.fullmatch(r"[0-9a-f]{40}", runtime.nilmbench_git_sha):
            raise ConfigError(f"Trusted runtime {runtime.id} has an invalid runner SHA")
        if not re.fullmatch(r"sha256:[0-9a-f]{64}", runtime.container_digest):
            raise ConfigError(
                f"Trusted runtime {runtime.id} has an invalid image digest"
            )
        if not runtime.container_image or not runtime.hardware:
            raise ConfigError(f"Trusted runtime {runtime.id} is incomplete")
        if any(item.id == runtime.id for item in trusted_runtimes):
            raise ConfigError(f"Duplicate trusted runtime id {runtime.id}")
        trusted_runtimes.append(runtime)
    return BenchmarkConfig(
        datasets=datasets,
        metric_policies=metric_policies,
        tasks=tasks,
        trusted_runtimes=tuple(trusted_runtimes),
    )
