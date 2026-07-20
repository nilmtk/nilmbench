import subprocess
import sys
from types import SimpleNamespace

import pytest

from nilmbench import provenance


class _Completed:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_git_sha_uses_bounded_read_only_command(monkeypatch, tmp_path):
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return _Completed("a" * 40 + "\n")

    monkeypatch.setattr(provenance.subprocess, "run", run)

    assert provenance._git_sha(tmp_path) == "a" * 40
    assert calls == [
        (
            ["git", "-C", str(tmp_path), "rev-parse", "HEAD"],
            {"check": True, "capture_output": True, "text": True, "timeout": 5},
        )
    ]


@pytest.mark.parametrize(
    "error",
    [
        OSError("git unavailable"),
        subprocess.CalledProcessError(128, ["git"]),
        subprocess.TimeoutExpired(["git"], 5),
    ],
)
def test_git_sha_returns_none_when_git_cannot_be_read(monkeypatch, tmp_path, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(provenance.subprocess, "run", fail)

    assert provenance._git_sha(tmp_path) is None


@pytest.mark.parametrize(
    ("stdout", "expected"),
    [("", False), (" M README.md\n", True)],
)
def test_git_dirty_interprets_porcelain_output(monkeypatch, tmp_path, stdout, expected):
    monkeypatch.setattr(
        provenance.subprocess,
        "run",
        lambda *args, **kwargs: _Completed(stdout),
    )

    assert provenance._git_dirty(tmp_path) is expected


def test_git_dirty_returns_none_when_git_fails(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise subprocess.TimeoutExpired(["git"], 5)

    monkeypatch.setattr(provenance.subprocess, "run", fail)

    assert provenance._git_dirty(tmp_path) is None


def test_module_repo_root_finds_nearest_worktree_marker(monkeypatch, tmp_path):
    package = tmp_path / "checkout" / "src" / "package"
    package.mkdir(parents=True)
    (tmp_path / "checkout" / ".git").write_text("gitdir: elsewhere\n")
    module_file = package / "__init__.py"
    module_file.write_text("")
    monkeypatch.setattr(
        provenance,
        "import_module",
        lambda name: SimpleNamespace(__file__=str(module_file)),
    )

    assert provenance._module_repo_root("package") == tmp_path / "checkout"


@pytest.mark.parametrize(
    "module",
    [
        SimpleNamespace(__file__=None),
        SimpleNamespace(__file__="/not/a/repository/package.py"),
    ],
)
def test_module_repo_root_returns_none_without_a_repository(monkeypatch, module):
    monkeypatch.setattr(provenance, "import_module", lambda name: module)

    assert provenance._module_repo_root("package") is None


def test_module_repo_root_returns_none_when_import_fails(monkeypatch):
    def fail(name):
        raise ModuleNotFoundError(name)

    monkeypatch.setattr(provenance, "import_module", fail)

    assert provenance._module_repo_root("missing") is None


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (None, None),
        ("1", True),
        (" TRUE ", True),
        ("yes", True),
        ("0", False),
        ("false", False),
        (" NO ", False),
        ("unknown", None),
    ],
)
def test_environment_bool(monkeypatch, value, expected):
    if value is None:
        monkeypatch.delenv("FLAG", raising=False)
    else:
        monkeypatch.setenv("FLAG", value)

    assert provenance._environment_bool("FLAG") is expected


def test_package_version_returns_installed_version(monkeypatch):
    monkeypatch.setattr(provenance, "version", lambda name: "1.2.3")

    assert provenance._package_version("package") == "1.2.3"


def test_package_version_returns_none_when_distribution_is_absent(monkeypatch):
    def missing(name):
        raise provenance.PackageNotFoundError(name)

    monkeypatch.setattr(provenance, "version", missing)

    assert provenance._package_version("package") is None


def _fixed_host(monkeypatch):
    monkeypatch.setattr(provenance.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(provenance.platform, "processor", lambda: "test-cpu")
    monkeypatch.setattr(provenance.platform, "machine", lambda: "fallback-cpu")


def test_runtime_provenance_captures_cpu_only_environment(monkeypatch, tmp_path):
    _fixed_host(monkeypatch)
    contrib_root = tmp_path / "contrib"
    monkeypatch.setattr(provenance, "_module_repo_root", lambda name: contrib_root)
    monkeypatch.setattr(
        provenance,
        "_git_sha",
        lambda path: "a" * 40 if path == tmp_path else "b" * 40,
    )
    monkeypatch.setattr(
        provenance,
        "_git_dirty",
        lambda path: path == contrib_root,
    )
    monkeypatch.setattr(
        provenance,
        "_package_version",
        lambda name: {"nilmtk-contrib": "1", "nilmtk": "2", "nilm-metadata": "3"}[name],
    )
    monkeypatch.setitem(sys.modules, "torch", None)
    for name in (
        "NILMBENCH_GIT_SHA",
        "NILMBENCH_GIT_DIRTY",
        "NILMTK_CONTRIB_GIT_SHA",
        "NILMTK_CONTRIB_GIT_DIRTY",
        "NILMBENCH_IMAGE",
        "NILMBENCH_IMAGE_DIGEST",
    ):
        monkeypatch.delenv(name, raising=False)

    result = provenance.runtime_provenance(tmp_path)

    assert result == {
        "python": sys.version.split()[0],
        "platform": "test-platform",
        "nilmbench_git_sha": "a" * 40,
        "nilmbench_git_dirty": False,
        "nilmtk_contrib_git_sha": "b" * 40,
        "nilmtk_contrib_git_dirty": True,
        "container_image": None,
        "container_digest": None,
        "nilmtk_contrib_version": "1",
        "nilmtk_version": "2",
        "nilm_metadata_version": "3",
        "cpu": "test-cpu",
        "torch": None,
        "cuda_runtime": None,
        "cuda_available": False,
        "deterministic_algorithms": None,
        "gpu": None,
    }


@pytest.mark.parametrize("cuda_available", [False, True])
def test_runtime_provenance_prefers_container_environment(monkeypatch, tmp_path, cuda_available):
    _fixed_host(monkeypatch)
    monkeypatch.setattr(provenance, "_module_repo_root", lambda name: None)
    monkeypatch.setattr(provenance, "_package_version", lambda name: "versioned")
    monkeypatch.setenv("NILMBENCH_GIT_SHA", "c" * 40)
    monkeypatch.setenv("NILMBENCH_GIT_DIRTY", "false")
    monkeypatch.setenv("NILMTK_CONTRIB_GIT_SHA", "d" * 40)
    monkeypatch.setenv("NILMTK_CONTRIB_GIT_DIRTY", "true")
    monkeypatch.setenv("NILMBENCH_IMAGE", "nilmbench:test")
    monkeypatch.setenv("NILMBENCH_IMAGE_DIGEST", "sha256:" + "e" * 64)
    cuda = SimpleNamespace(
        is_available=lambda: cuda_available,
        get_device_name=lambda index: "Test GPU",
    )
    torch = SimpleNamespace(
        __version__="2.6.0",
        version=SimpleNamespace(cuda="12.4"),
        cuda=cuda,
        are_deterministic_algorithms_enabled=lambda: True,
    )
    monkeypatch.setitem(sys.modules, "torch", torch)

    result = provenance.runtime_provenance(tmp_path)

    assert result["nilmbench_git_sha"] == "c" * 40
    assert result["nilmbench_git_dirty"] is False
    assert result["nilmtk_contrib_git_sha"] == "d" * 40
    assert result["nilmtk_contrib_git_dirty"] is True
    assert result["container_image"] == "nilmbench:test"
    assert result["container_digest"] == "sha256:" + "e" * 64
    assert result["torch"] == "2.6.0"
    assert result["cuda_runtime"] == "12.4"
    assert result["cuda_available"] is cuda_available
    assert result["deterministic_algorithms"] is True
    assert result["gpu"] == ("Test GPU" if cuda_available else None)


def test_runtime_provenance_uses_machine_when_processor_is_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(provenance.platform, "platform", lambda: "test-platform")
    monkeypatch.setattr(provenance.platform, "processor", lambda: "")
    monkeypatch.setattr(provenance.platform, "machine", lambda: "arm64")
    monkeypatch.setattr(provenance, "_module_repo_root", lambda name: None)
    monkeypatch.setattr(provenance, "_git_sha", lambda path: None)
    monkeypatch.setattr(provenance, "_git_dirty", lambda path: None)
    monkeypatch.setattr(provenance, "_package_version", lambda name: None)
    monkeypatch.setitem(sys.modules, "torch", None)

    assert provenance.runtime_provenance(tmp_path)["cpu"] == "arm64"
