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
    assert "comparisonKey(entry)" in source
    assert "Group rank" in source
    assert "entries.slice(0, 25)" not in source
    assert (
        "nilmbench[benchmark] @ git+https://github.com/sustainability-lab/nilmbench.git"
        in source
    )
    assert "--build-context contrib=../nilmtk-contrib" in source
    assert "nilmbench run --task corrected-t1-redd" in source
    assert "--results results/candidates" in source
    assert "nilmbench leaderboard --results results/published" in source
    assert (
        "nilmtk-contrib[torch] @ git+https://github.com/sustainability-lab/nilmbench.git"
        not in source
    )
    assert "ghcr.io/sustainability-lab/nilmtk-contrib" not in source
    assert "run_benchmark.py" not in source
