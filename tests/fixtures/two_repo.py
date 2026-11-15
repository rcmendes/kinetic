from __future__ import annotations

import json
import subprocess
from pathlib import Path

CONFIG = {
    "lib": {"verify": "true", "publish_snapshot": "true"},
    "svc": {"verify": "true", "pin": "true"},
}


def _git(path, *args):
    subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
                   check=True, capture_output=True, text=True)


def build(root: Path) -> dict:
    """Create real git repos `lib` and `svc` (one commit each) plus a project config file."""
    out = {}
    for name in CONFIG:
        path = Path(root) / name
        path.mkdir(parents=True)
        _git(path, "init", "-b", "main")
        _git(path, "config", "user.name", "t")
        _git(path, "config", "user.email", "t@example.com")
        (path / "README").write_text(name + "\n")
        _git(path, "add", "README")
        _git(path, "commit", "-m", "init")
        out[name] = path
    project_config = Path(root) / "config.json"
    project_config.write_text(json.dumps({"version": 1, "repos": CONFIG}))
    out["project_config"] = project_config
    return out
