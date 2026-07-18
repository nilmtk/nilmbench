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
    assert "ranking_protocol_sha256" in source
    assert "Number.isInteger(entry.rank)" in source
    assert "comparisonKey(entry)" not in source
    assert "const ranks = new Map()" not in source
    assert "entry.max_samples_per_window ?? 'full'" not in source
    assert "<th>Rank</th>" in source
    assert "entries.slice(0, 25)" not in source
    assert (
        "nilmbench[benchmark] @ git+https://github.com/nilmtk/nilmbench.git"
        in source
    )
    assert "--build-context contrib=../nilmtk-contrib" in source
    assert "nilmbench run --task corrected-t1-redd" in source
    assert "--results results/candidates" in source
    assert "nilmbench leaderboard --results results/published" in source
    assert (
        "nilmtk-contrib[torch] @ git+https://github.com/nilmtk/nilmbench.git"
        not in source
    )
    assert "ghcr.io/sustainability-lab/nilmtk-contrib" not in source
    assert "run_benchmark.py" not in source


def test_readme_protocol_audit_link_survives_package_rendering():
    source = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert (
        "https://github.com/nilmtk/nilmbench/blob/main/"
        "docs/protocol-audit.md"
    ) in source
    assert "](docs/protocol-audit.md)" not in source


def test_transferred_repository_uses_nilmtk_namespaces():
    root = Path(__file__).parents[1]
    public_files = [
        root / "README.md",
        root / "index.html",
        root / "docker" / "Dockerfile.cpu",
        root / "docker" / "Dockerfile.cuda",
        root / "slides" / "nilmbench2026.md",
        root / "slides" / "nilmbench2026.html",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in public_files)

    assert "https://github.com/nilmtk/nilmbench" in source
    assert "https://nilmtk.github.io/nilmbench/" in source
    assert "ghcr.io/nilmtk/nilmbench:cpu" in source
    assert "ghcr.io/nilmtk/nilmbench:cuda" in source
    assert "github.com/sustainability-lab/nilmbench" not in source
    assert "sustainability-lab.github.io/nilmbench" not in source
    assert "ghcr.io/sustainability-lab/nilmbench" not in source
