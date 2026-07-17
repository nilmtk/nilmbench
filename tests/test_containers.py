from pathlib import Path


ROOT = Path(__file__).parents[1]
NILMTK_REVISION = "0768b1b8457eef9de76d123a94e2de8af22a45d0"
NILM_METADATA_REVISION = "59c9990de4836d77c0dcd807bd4293e39e0cc314"


def test_cpu_and_cuda_images_pin_the_same_core_revisions():
    for name in ("Dockerfile.cpu", "Dockerfile.cuda"):
        source = (ROOT / "docker" / name).read_text(encoding="utf-8")

        assert f"ARG NILMTK_COMMIT={NILMTK_REVISION}" in source
        assert f"ARG NILM_METADATA_COMMIT={NILM_METADATA_REVISION}" in source
        assert "USER benchmark" in source


def test_public_images_are_released_as_one_cpu_cuda_family():
    workflow = (ROOT / ".github" / "workflows" / "publish-images.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert 'tags: ["v*"]' in workflow
    assert "target: [cpu, cuda]" in workflow
    assert "pull_request:" not in workflow
    assert "type=semver,pattern={{version}},suffix=-${{ matrix.target }}" in workflow
