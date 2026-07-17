"""Small, shared filesystem primitives for benchmark artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


def _fsync_directory(path: Path) -> None:
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    descriptor = os.open(path, flags)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def atomic_write_text(path: Path, content: str) -> None:
    """Replace a UTF-8 text artifact and keep container output host-readable."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o644)
        os.replace(temporary, path)
        temporary = None
        _fsync_directory(path.parent)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def immutable_write_text(path: Path, content: str) -> None:
    """Create a UTF-8 artifact once; permit only byte-identical retries.

    Linking a fully flushed temporary file makes publication atomic without ever
    replacing an existing audit record.  The read-only mode is an additional
    guard against accidental mutation after publication.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.chmod(0o444)
        try:
            os.link(temporary, path)
            _fsync_directory(path.parent)
        except FileExistsError:
            try:
                existing = path.read_text(encoding="utf-8")
            except OSError as exc:
                raise RuntimeError(
                    f"Could not verify existing immutable artifact {path}: {exc}"
                ) from exc
            if existing != content:
                raise RuntimeError(
                    f"Refusing to replace immutable artifact with different content: "
                    f"{path}"
                )
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
