"""Runtime, source, dataset, and container provenance capture."""

from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path
from typing import Any


def _git_sha(path: Path) -> str | None:
    try:
        return subprocess.run(
            ["git", "-C", str(path), "rev-parse", "HEAD"],
            check=True,
            capture_output=True,
            text=True,
            timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None


def runtime_provenance(repo_root: Path) -> dict[str, Any]:
    result: dict[str, Any] = {
        "python": sys.version.split()[0],
        "platform": platform.platform(),
        "nilmbench_git_sha": os.environ.get("NILMBENCH_GIT_SHA")
        or _git_sha(repo_root),
        "nilmtk_contrib_git_sha": os.environ.get("NILMTK_CONTRIB_GIT_SHA"),
        "container_image": os.environ.get("NILMBENCH_IMAGE"),
        "container_digest": os.environ.get("NILMBENCH_IMAGE_DIGEST"),
    }
    try:
        import torch

        result["torch"] = torch.__version__
        result["cuda_runtime"] = torch.version.cuda
        result["cuda_available"] = torch.cuda.is_available()
        result["deterministic_algorithms"] = (
            torch.are_deterministic_algorithms_enabled()
        )
        result["gpu"] = (
            torch.cuda.get_device_name(0) if torch.cuda.is_available() else None
        )
    except ModuleNotFoundError:
        result.update(
            torch=None,
            cuda_runtime=None,
            cuda_available=False,
            deterministic_algorithms=None,
            gpu=None,
        )
    return result
