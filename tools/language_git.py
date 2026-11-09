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
            # Match Windows checkout semantics without changing repository config.
            env.update(GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="core.autocrlf", GIT_CONFIG_VALUE_0="true",
                       GIT_CONFIG_KEY_1="core.filemode", GIT_CONFIG_VALUE_1="false")
    return env
