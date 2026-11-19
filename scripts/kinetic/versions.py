"""Where kinetic is out of date.

Three versions matter: the plugin a harness loaded, the checkout the `kinetic` launcher runs
from, and the latest GitHub release. Every lookup here fails quietly (returns None): a check
for updates must never block or break a session, offline or without `gh`.
"""
from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path

CACHE_TTL = 24 * 3600
_VERSION = re.compile(r"v?(\d+)\.(\d+)\.(\d+)")
_REMOTE = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def _parse(version):
    m = _VERSION.fullmatch(str(version or "").strip())
    return tuple(int(x) for x in m.groups()) if m else None


def newer(candidate, current) -> bool:
    """True when `candidate` is a higher x.y.z than `current`; an unreadable one never is."""
    a, b = _parse(candidate), _parse(current)
    return a is not None and b is not None and a > b


def _clean(version):
    parsed = _parse(version)
    return ".".join(str(x) for x in parsed) if parsed else None


def checkout_version(clone) -> str | None:
    """The version in the checkout's .claude-plugin/plugin.json."""
    try:
        return _clean(json.loads((Path(clone) / ".claude-plugin" / "plugin.json").read_text())["version"])
    except (OSError, ValueError, KeyError, TypeError):
        return None


def plugin_version(harness_id: str, home) -> str | None:
    """The plugin version the harness has installed. Only Claude Code records it in a file
    kinetic can read; the other harnesses load the checkout directly, so they have none."""
    if harness_id != "claude":
        return None
    try:
        record = json.loads((Path(home) / ".claude" / "plugins" / "installed_plugins.json").read_text())
        return _clean(record["plugins"]["kinetic@kinetic"][0]["version"])
    except (OSError, ValueError, KeyError, IndexError, TypeError):
        return None


def _git(clone):
    def run(args):
        try:
            done = subprocess.run(["git", "-C", str(clone), *args], capture_output=True, text=True, timeout=5)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout if done.returncode == 0 else None
    return run


def repository(clone, git=None) -> str | None:
    """`owner/name` of the clone's origin when it is on GitHub."""
    out = (git or _git(clone))(["remote", "get-url", "origin"])
    m = _REMOTE.search((out or "").strip())
    return m.group(1) if m else None


def _gh_latest(repo):
    try:
        done = subprocess.run(["gh", "api", f"repos/{repo}/releases/latest", "--jq", ".tag_name"],
                              capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.SubprocessError):
        return None
    return done.stdout.strip() if done.returncode == 0 else None


def latest_release(repo: str, cache, now: float | None = None, gh=None) -> str | None:
    """The newest release tag as x.y.z, asked of GitHub at most once a day per repository."""
    now = time.time() if now is None else now
    cache = Path(cache)
    try:
        saved = json.loads(cache.read_text())
        if saved.get("repo") == repo and now - float(saved["checked_at"]) < CACHE_TTL:
            return _clean(saved.get("latest"))
    except (OSError, ValueError, KeyError, TypeError):
        pass
    try:
        latest = _clean((gh or _gh_latest)(repo))
    except Exception:  # noqa: BLE001 - a lookup must never break the caller
        latest = None
    if latest is None:
        return None  # not cached: the next call tries again
    try:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps({"repo": repo, "checked_at": now, "latest": latest}))
    except OSError:
        pass
    return latest
