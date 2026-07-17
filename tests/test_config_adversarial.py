import shutil
from pathlib import Path

import pytest

from nilmbench.config import ConfigError, load_config


CONFIGS = Path(__file__).resolve().parents[1] / "configs"


def _copy_configs(tmp_path):
    destination = tmp_path / "configs"
    shutil.copytree(CONFIGS, destination)
    return destination


def _replace(path, old, new):
    content = path.read_text(encoding="utf-8")
    assert old in content
    path.write_text(content.replace(old, new, 1), encoding="utf-8")


def test_non_hex_dataset_digest_is_rejected(tmp_path):
    root = _copy_configs(tmp_path)
    datasets = root / "datasets.toml"
    _replace(datasets, 'sha256 = "', 'sha256 = "zz')
    _replace(datasets, 'zz757e', 'zz57e')

    with pytest.raises(ConfigError, match="SHA-256"):
        load_config(root)


def test_non_finite_metric_threshold_is_rejected(tmp_path):
    root = _copy_configs(tmp_path)
    metrics = root / "metrics.toml"
    _replace(metrics, 'fridge = 10.0', 'fridge = nan')

    with pytest.raises(ConfigError, match="threshold"):
        load_config(root)


def test_duplicate_appliance_is_rejected(tmp_path):
    root = _copy_configs(tmp_path)
    tasks = root / "tasks.toml"
    _replace(
        tasks,
        'appliances = ["fridge", "washing machine", "microwave", "dish washer"]',
        'appliances = ["fridge", "fridge"]',
    )

    with pytest.raises(ConfigError, match="sampling or appliances"):
        load_config(root)


def test_invalid_window_timestamp_is_a_config_error(tmp_path):
    root = _copy_configs(tmp_path)
    tasks = root / "tasks.toml"
    _replace(tasks, 'start = "2011-04-01"', 'start = "not-a-date"')

    with pytest.raises(ConfigError, match="timestamp"):
        load_config(root)


def test_missing_required_task_field_is_a_config_error(tmp_path):
    root = _copy_configs(tmp_path)
    tasks = root / "tasks.toml"
    _replace(tasks, 'sample_period = 60\n', '')

    with pytest.raises(ConfigError, match="Invalid task entry"):
        load_config(root)
