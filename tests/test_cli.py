import json
from types import SimpleNamespace

import pytest

from nilmbench import cli
from nilmbench.cli import main


def test_dry_run_needs_no_ml_dependencies(capsys, tmp_path):
    exit_code = main(
        [
            "run",
            "--task",
            "corrected-t1-redd",
            "--model",
            "PatchTST",
            "--appliance",
            "fridge",
            "--epochs",
            "1",
            "--max-samples",
            "1024",
            "--sequence-length",
            "299",
            "--results",
            str(tmp_path),
            "--dry-run",
        ]
    )
    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["appliances"] == ["fridge"]
    assert payload["epochs"] == 1
    assert payload["max_samples"] == 1024
    assert payload["sequence_length"] == 299
    assert payload["metric_policy"]["thresholds"]["fridge"] == 50.0


def test_validate_without_data_access(capsys):
    assert main(["validate", "--task", "historical-t1-redd"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["family"] == "T1"
    assert payload["sample_period"] == 60
    assert "observed" not in payload


def test_validate_checks_data_at_requested_resolution(
    capsys, monkeypatch, tmp_path
):
    redd = tmp_path / "redd.h5"
    redd.touch()
    monkeypatch.setenv("NILMBENCH_REDD", str(redd))
    calls = []

    def fake_load_split(
        config, task, windows, appliances, sample_period, max_samples
    ):
        del config, task
        calls.append((windows, appliances, sample_period, max_samples))
        return SimpleNamespace(metadata=lambda: [{"samples": 12}])

    monkeypatch.setattr(cli, "load_split", fake_load_split)

    assert (
        main(
            [
                "validate",
                "--task",
                "corrected-t1-redd",
                "--check-data",
                "--sample-period",
                "900",
                "--max-samples",
                "3960",
            ]
        )
        == 0
    )
    payload = json.loads(capsys.readouterr().out)
    assert payload["sample_period"] == 900
    assert payload["observed"]["fridge"]["train"] == [{"samples": 12}]
    assert calls
    assert {call[2] for call in calls} == {900}
    assert {call[3] for call in calls} == {3960}


def test_invalid_run_limits_are_rejected(capsys):
    try:
        main(
            [
                "run",
                "--task",
                "corrected-t1-redd",
                "--max-samples",
                "0",
                "--dry-run",
            ]
        )
    except SystemExit as exc:
        assert exc.code == 2
    else:
        raise AssertionError("argparse should reject a zero sample limit")
    assert "positive integer" in capsys.readouterr().err


def test_nonpositive_sequence_length_is_rejected(capsys):
    with pytest.raises(SystemExit, match="2"):
        main(
            [
                "run",
                "--task",
                "corrected-t1-redd",
                "--sequence-length",
                "0",
                "--dry-run",
            ]
        )
    assert "positive integer" in capsys.readouterr().err


def test_leaderboard_command_generates_empty_artifacts(capsys, tmp_path):
    json_path = tmp_path / "leaderboard.json"
    csv_path = tmp_path / "leaderboard.csv"

    exit_code = main(
        [
            "leaderboard",
            "--results",
            str(tmp_path / "results"),
            "--output",
            str(json_path),
            "--csv",
            str(csv_path),
        ]
    )

    assert exit_code == 0
    assert json.loads(json_path.read_text(encoding="utf-8"))["entries"] == []
    assert csv_path.read_text(encoding="utf-8").startswith("task,family,profile")
    assert str(json_path) in capsys.readouterr().out


def test_leaderboard_command_forwards_custom_config_dir(monkeypatch, tmp_path):
    sentinel = object()
    observed = {}
    config_dir = tmp_path / "private-config"

    def fake_load_config(path):
        observed["config_dir"] = path
        return sentinel

    def fake_build(results, *, config):
        observed["results"] = results
        observed["config"] = config
        return {"entries": []}

    monkeypatch.setattr(cli, "load_config", fake_load_config)
    monkeypatch.setattr(cli, "build_leaderboard", fake_build)
    monkeypatch.setattr(cli, "write_leaderboard", lambda *args: None)

    assert (
        main(
            [
                "--config-dir",
                str(config_dir),
                "leaderboard",
                "--results",
                str(tmp_path / "results"),
                "--output",
                str(tmp_path / "leaderboard.json"),
            ]
        )
        == 0
    )
    assert observed == {
        "config_dir": config_dir,
        "results": tmp_path / "results",
        "config": sentinel,
    }
