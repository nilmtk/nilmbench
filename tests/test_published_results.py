import csv
import json
import math
from pathlib import Path

from nilmbench.leaderboard import build_leaderboard, write_leaderboard


ROOT = Path(__file__).resolve().parents[1]
PUBLISHED_RESULTS = ROOT / "results" / "published"
METRIC_FIELDS = ("mae", "f1", "activation_threshold_watts")


def test_published_metrics_csv_matches_immutable_result_json():
    bundles = sorted(path for path in PUBLISHED_RESULTS.iterdir() if path.is_dir())
    assert bundles

    for bundle in bundles:
        result = json.loads((bundle / "result.json").read_text(encoding="utf-8"))
        expected = result["run"]["metrics"]
        with (bundle / "metrics.csv").open(newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle)
            assert reader.fieldnames == ["appliance", *METRIC_FIELDS]
            rows = list(reader)

        actual = {row["appliance"]: row for row in rows}
        assert len(actual) == len(rows)
        assert set(actual) == set(expected)
        for appliance, metrics in expected.items():
            for field in METRIC_FIELDS:
                value = float(actual[appliance][field])
                assert math.isfinite(value)
                assert value == metrics[field]


def test_checked_in_leaderboard_exactly_regenerates(tmp_path):
    json_path = tmp_path / "leaderboard.json"
    csv_path = tmp_path / "leaderboard.csv"

    write_leaderboard(build_leaderboard(PUBLISHED_RESULTS), json_path, csv_path)

    assert json_path.read_bytes() == (ROOT / "leaderboard.json").read_bytes()
    assert csv_path.read_bytes() == (ROOT / "leaderboard.csv").read_bytes()


def test_torch_afhmm_publication_is_a_verified_three_seed_result():
    leaderboard = json.loads((ROOT / "leaderboard.json").read_text(encoding="utf-8"))
    entries = [
        entry for entry in leaderboard["entries"] if entry["model"] == "TorchAFHMM"
    ]

    assert len(entries) == 1
    entry = entries[0]
    assert entry["status"] == "smoke-verified"
    assert entry["provenance_verified"] is True
    assert entry["verification_failures"] == []
    assert entry["seeds"] == [10, 20, 42]
    assert entry["run_count"] == 3
    assert entry["mae_mean"] == 52.70154621194001
    assert entry["f1_mean"] == 0.6268836648583485
    assert entry["trainable_parameters_mean"] is None
    assert entry["peak_accelerator_memory_bytes_mean"] == 342016
    assert entry["result_ids"] == [
        "0a58b6d92f6ac2033a29b9822bc6db819abdd9829f27461924d3b046307c7ed1",
        "1b855b6f689b6ee71ce9364d1220c692da482f8cedfa34fdd68eb55a7a2fdba7",
        "7c406971a5f9160eb7a4216d758e12466735e58ee4b7022286811a28d33e1f5e",
    ]
