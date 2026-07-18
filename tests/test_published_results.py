import csv
import json
import math
from pathlib import Path


PUBLISHED_RESULTS = Path(__file__).resolve().parents[1] / "results" / "published"
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
