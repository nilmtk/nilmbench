from pathlib import Path


ROOT = Path(__file__).parents[1]
NILMTK_REVISION = "0768b1b8457eef9de76d123a94e2de8af22a45d0"
NILM_METADATA_REVISION = "59c9990de4836d77c0dcd807bd4293e39e0cc314"
NILMTK_CONTRIB_REVISION = "d267f8668fc6c84556b15d80426a1f7f2f41e1b4"


def test_cpu_and_cuda_images_pin_the_same_core_revisions():
    for name in ("Dockerfile.cpu", "Dockerfile.cuda"):
        source = (ROOT / "docker" / name).read_text(encoding="utf-8")

        assert f"ARG NILMTK_COMMIT={NILMTK_REVISION}" in source
        assert f"ARG NILM_METADATA_COMMIT={NILM_METADATA_REVISION}" in source
        assert "COPY pyproject.toml uv.lock README.md ./" in source
        assert 'grep -F "${NILMTK_COMMIT}" pyproject.toml' in source
        assert 'grep -F "${NILM_METADATA_COMMIT}" pyproject.toml' in source
        assert "uv sync \\\n      --frozen" in source
        assert "--no-dev" in source
        assert "--extra runtime" in source
        assert "--extra build-runtime" in source
        assert "--no-install-project" in source
        assert "--active" in source
        assert "--no-build-isolation" in source
        assert ". /src/nilmtk-contrib" in source
        assert '".[runtime]"' not in source
        assert "uv pip install" in source
        assert "git+https://github.com/nilmtk/" not in source
        assert "USER benchmark" in source
        assert "NILMBENCH_GIT_DIRTY=false" in source
        assert "NILMTK_CONTRIB_GIT_DIRTY=false" in source


def test_dependency_layer_precedes_all_local_source_contexts():
    for name in ("Dockerfile.cpu", "Dockerfile.cuda"):
        source = (ROOT / "docker" / name).read_text(encoding="utf-8")
        sync_position = source.index("uv sync")
        config_position = source.index("COPY configs/ configs/")
        source_position = source.index("COPY src/ src/")
        contrib_position = source.index("COPY --from=contrib")
        local_install_position = source.rindex("RUN uv pip install")

        assert sync_position < config_position < local_install_position
        assert sync_position < source_position < local_install_position
        assert sync_position < contrib_position < local_install_position
        assert source.count(
            "--mount=type=cache,target=/root/.cache/uv,sharing=locked"
        ) == 1


def test_runtime_os_setup_is_independent_of_revision_labels():
    for name in ("Dockerfile.cpu", "Dockerfile.cuda"):
        source = (ROOT / "docker" / name).read_text(encoding="utf-8")
        runtime_stage_position = source.rindex("\nFROM ")
        runtime_setup_position = source.index("\nRUN ", runtime_stage_position)
        revision_position = source.index("\nARG SOURCE_REVISION", runtime_stage_position)

        assert runtime_stage_position < runtime_setup_position < revision_position


def test_locked_runtime_selects_exact_core_sources_and_cpu_torch_dependencies():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    cpu = (ROOT / "docker" / "Dockerfile.cpu").read_text(encoding="utf-8")
    cuda = (ROOT / "docker" / "Dockerfile.cuda").read_text(encoding="utf-8")

    for revision in (NILMTK_REVISION, NILM_METADATA_REVISION):
        assert revision in pyproject
        assert revision in lock
    assert "--extra torch-runtime" in cpu
    assert "--torch-backend=cpu" in cpu
    assert '--no-deps \\\n      "torch==2.6.0"' in cpu
    assert "--extra torch-runtime" not in cuda
    for source in (cpu, cuda):
        installs = source.split("uv pip install")[1:]
        assert installs
        assert all("--no-deps" in command.split("&&", 1)[0] for command in installs)
        source_install = installs[-1].split("&&", 1)[0]
        assert "--no-build-isolation" in source_install


def test_public_images_are_released_as_one_cpu_cuda_family():
    workflow = (ROOT / ".github" / "workflows" / "publish-images.yml").read_text(
        encoding="utf-8"
    )

    assert "workflow_dispatch:" in workflow
    assert 'tags: ["v*"]' in workflow
    assert "target: [cpu, cuda]" in workflow
    assert "pull_request:" not in workflow
    assert "type=semver,pattern={{version}},suffix=-${{ matrix.target }}" in workflow
    assert (
        "contrib=https://github.com/nilmtk/nilmtk-contrib.git#"
        f"{NILMTK_CONTRIB_REVISION}"
    ) in workflow
    assert f"NILMTK_CONTRIB_REVISION={NILMTK_CONTRIB_REVISION}" in workflow


def test_benchmark_dependency_and_lock_use_the_image_contrib_revision():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")

    dependency = (
        f"git+https://github.com/nilmtk/nilmtk-contrib.git@{NILMTK_CONTRIB_REVISION}"
    )
    assert dependency in pyproject
    assert NILMTK_CONTRIB_REVISION in lock
