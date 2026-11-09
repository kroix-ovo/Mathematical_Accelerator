"""Read Windows-created worktree metadata from WSL without changing it."""
from __future__ import annotations

import os
from pathlib import Path


def git_env(root: Path) -> dict[str, str]:
    env = dict(os.environ)
    marker = root / ".git"
    if os.name != "nt" and marker.is_file():
        target = marker.read_text().strip().removeprefix("gitdir: ")
        if len(target) > 2 and target[1] == ":":
            env["GIT_DIR"] = f"/mnt/{target[0].lower()}/{target[3:]}"
            env["GIT_WORK_TREE"] = str(root)
    return env
