from pathlib import Path


def test_site_loads_generated_leaderboard_and_discloses_smoke_protocol():
    source = (Path(__file__).parents[1] / "index.html").read_text(encoding="utf-8")

    assert "fetch('leaderboard.json'" in source
    assert "'smoke-verified'" in source
    assert "'smoke-partial'" in source
    assert "entry.sequence_length" in source
    assert "entry.epochs" in source
    assert "entry.max_samples_per_window" in source
    assert "entry.trainable_parameters_mean" in source
    assert "entry.elapsed_seconds_mean" in source
    assert "entry.peak_accelerator_memory_bytes_mean" in source
