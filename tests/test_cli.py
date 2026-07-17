import json

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
    assert payload["metric_policy"]["thresholds"]["fridge"] == 50.0


def test_validate_without_data_access(capsys):
    assert main(["validate", "--task", "historical-t1-redd"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["task"]["family"] == "T1"
    assert "observed" not in payload


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
