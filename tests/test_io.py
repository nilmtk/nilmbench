from pathlib import Path
import stat

import pytest

from nilmbench._io import atomic_write_text


def test_atomic_text_artifacts_are_replaced_and_host_readable(tmp_path):
    path = tmp_path / "nested" / "artifact.json"

    atomic_write_text(path, "first\n")
    path.chmod(0o600)
    atomic_write_text(path, "second\n")

    assert path.read_text(encoding="utf-8") == "second\n"
    assert stat.S_IMODE(path.stat().st_mode) == 0o644
    assert list(path.parent.glob(f".{path.name}.*")) == []


def test_atomic_text_write_cleans_temporary_file_on_replace_failure(
    tmp_path, monkeypatch
):
    path = tmp_path / "artifact.json"

    def fail_replace(source: Path, destination: Path) -> None:
        del source, destination
        raise OSError("injected replace failure")

    monkeypatch.setattr("nilmbench._io.os.replace", fail_replace)

    with pytest.raises(OSError, match="injected"):
        atomic_write_text(path, "content\n")

    assert not path.exists()
    assert list(tmp_path.iterdir()) == []
