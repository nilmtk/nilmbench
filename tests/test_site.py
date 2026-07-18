from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit


ROOT = Path(__file__).parents[1]


class _AssetParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.targets = []

    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        target = attributes.get("href") or attributes.get("src")
        if target:
            self.targets.append(target)


def test_site_separates_fixed_paper_from_living_leaderboard():
    landing = (ROOT / "index.html").read_text(encoding="utf-8")
    paper = (ROOT / "paper.html").read_text(encoding="utf-8")
    board = (ROOT / "leaderboard.html").read_text(encoding="utf-8")
    renderer = (ROOT / "static" / "leaderboard.js").read_text(encoding="utf-8")

    assert 'href="paper.html"' in landing
    assert 'href="leaderboard.html"' in landing
    assert "The paper is fixed." in landing
    assert "The benchmark keeps moving." in landing

    assert "The fixed NILMBench2026 BuildSys paper" in paper
    assert "fetch('leaderboard.json'" not in paper
    assert 'href="leaderboard.html"' in paper

    assert "T0 smoke leaderboard — not a full benchmark claim." in renderer
    assert "One cohort at a time" in board
    assert '<th scope="col">Rank</th>' in board
    assert 'src="static/leaderboard.js"' in board

    assert 'fetch("leaderboard.json"' in renderer
    assert '"smoke-verified"' in renderer
    assert '"smoke-partial"' in renderer
    assert "entry.sequence_length" in renderer
    assert "entry.epochs" in renderer
    assert "entry.max_samples_per_window" in renderer
    assert "entry.trainable_parameters_mean" in renderer
    assert "entry.elapsed_seconds_mean" in renderer
    assert "entry.peak_accelerator_memory_bytes_mean" in renderer
    assert "ranking_protocol_sha256" in renderer
    assert "Number.isInteger(entry.rank)" in renderer
    assert "comparisonKey(entry)" not in renderer
    assert "const ranks = new Map()" not in renderer
    assert "entries.slice(0, 25)" not in renderer
    assert (
        "nilmbench[benchmark] @ git+https://github.com/nilmtk/nilmbench.git"
        in paper
    )
    assert "--build-context contrib=../nilmtk-contrib" in paper
    assert "nilmbench run --task corrected-t1-redd" in paper
    assert "--results results/candidates" in paper
    assert "nilmbench leaderboard --results results/published" in paper
    assert (
        "nilmtk-contrib[torch] @ git+https://github.com/nilmtk/nilmbench.git"
        not in paper
    )
    assert "ghcr.io/sustainability-lab/nilmtk-contrib" not in paper
    assert "run_benchmark.py" not in paper


def test_local_site_links_resolve_to_tracked_files():
    for page in ("index.html", "paper.html", "leaderboard.html"):
        parser = _AssetParser()
        parser.feed((ROOT / page).read_text(encoding="utf-8"))
        for target in parser.targets:
            parts = urlsplit(target)
            if parts.scheme or target.startswith(("#", "//", "mailto:")):
                continue
            path = "index.html" if parts.path in ("", "./") else parts.path
            assert (ROOT / path).is_file(), f"{page}: missing {target}"


def test_readme_protocol_audit_link_survives_package_rendering():
    source = (Path(__file__).parents[1] / "README.md").read_text(encoding="utf-8")

    assert (
        "https://github.com/nilmtk/nilmbench/blob/main/"
        "docs/protocol-audit.md"
    ) in source
    assert "](docs/protocol-audit.md)" not in source


def test_transferred_repository_uses_nilmtk_namespaces():
    public_files = [
        ROOT / "README.md",
        ROOT / "index.html",
        ROOT / "paper.html",
        ROOT / "leaderboard.html",
        ROOT / "docker" / "Dockerfile.cpu",
        ROOT / "docker" / "Dockerfile.cuda",
        ROOT / "slides" / "nilmbench2026.md",
        ROOT / "slides" / "nilmbench2026.html",
    ]
    source = "\n".join(path.read_text(encoding="utf-8") for path in public_files)

    assert "https://github.com/nilmtk/nilmbench" in source
    assert "https://nilmtk.github.io/nilmbench/" in source
    assert "ghcr.io/nilmtk/nilmbench:cpu" in source
    assert "ghcr.io/nilmtk/nilmbench:cuda" in source
    assert "github.com/sustainability-lab/nilmbench" not in source
    assert "sustainability-lab.github.io/nilmbench" not in source
    assert "ghcr.io/sustainability-lab/nilmbench" not in source
