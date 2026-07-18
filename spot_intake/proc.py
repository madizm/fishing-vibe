"""Shared subprocess helper for adapters that shell out to CLI tools."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def run(cmd: list[str], timeout: int = 120, cwd: Path | None = None) -> str:
    resolved = cmd[:]
    exe = shutil.which(resolved[0])
    if exe:
        resolved[0] = exe
    p = subprocess.run(resolved, cwd=cwd, text=True, capture_output=True, timeout=timeout)
    if p.returncode != 0:
        raise RuntimeError(f"command failed: {' '.join(cmd)}\nSTDOUT:\n{p.stdout}\nSTDERR:\n{p.stderr}")
    return p.stdout
