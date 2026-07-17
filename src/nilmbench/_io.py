"""Small, shared filesystem primitives for benchmark artifacts."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile


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
            temporary = Path(handle.name)
        os.replace(temporary, path)
        temporary = None
        path.chmod(0o644)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)
