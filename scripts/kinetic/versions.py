"""Where kinetic is out of date.

Three versions matter: the plugin a harness loaded, the checkout the `kinetic` launcher runs
from, and the latest GitHub release. Every lookup here fails quietly (returns None): a check
for updates must never block or break a session, offline or without `gh`.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
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


def latest_release(repo: str, cache, now: float | None = None, gh=None,
                   max_age: float = CACHE_TTL) -> str | None:
    """The newest release tag as x.y.z, asked of GitHub at most once a day per repository
    (`max_age=0` forces a fresh lookup, for a user who asked to update)."""
    now = time.time() if now is None else now
    cache = Path(cache)
    try:
        saved = json.loads(cache.read_text())
        if saved.get("repo") == repo and now - float(saved["checked_at"]) < max_age:
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


# -- the once-a-day line at session start ----------------------------------------------------

NOTICE_EVERY = 24 * 3600
REFRESH_EVERY = 6 * 3600


def _read(path) -> dict:
    try:
        data = json.loads(Path(path).read_text())
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _write(path, data) -> None:
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        Path(path).write_text(json.dumps(data))
    except OSError:
        pass


def refresh_in_background(repo: str, cache) -> None:
    """Ask GitHub for the latest release in a detached process, so a session never waits."""
    try:
        scripts = str(Path(__file__).resolve().parents[1])
        env = {**os.environ, "PYTHONPATH": scripts + os.pathsep + os.environ.get("PYTHONPATH", "")}
        subprocess.Popen([sys.executable, "-m", "kinetic.versions", "refresh", repo, str(cache)],
                         env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, start_new_session=True)
    except (OSError, ValueError):
        pass


def notice(home, cfg: dict, harness_id: str, now: float | None = None, spawn=None, git=None):
    """One line saying a newer release exists, at most once a day, or None.

    It reads only local files. When the cached answer is missing or older than a day it
    starts a background refresh (at most once every six hours) and uses the cached answer
    meanwhile, so the line appears on a later session. `update_check: false` turns it off."""
    try:
        if cfg.get("update_check", True) is False:
            return None
        clone = cfg.get("clone")
        here = checkout_version(clone) if isinstance(clone, str) and clone else None
        repo = repository(clone, git) if here else None
        if not repo:
            return None
        now = time.time() if now is None else now
        base = Path(home) / ".config" / "kinetic"
        cache, state_path = base / "update-check.json", base / "update-notice.json"
        saved, state = _read(cache), _read(state_path)
        fresh = saved.get("repo") == repo and now - float(saved.get("checked_at", 0)) < CACHE_TTL
        tried = state.get("refreshed_at")
        if not fresh and (tried is None or now - float(tried) >= REFRESH_EVERY):
            (spawn or refresh_in_background)(repo, cache)
            state["refreshed_at"] = now
            _write(state_path, state)
        latest = _clean(saved.get("latest")) if saved.get("repo") == repo else None
        if not newer(latest, here):
            return None
        shown = state.get("shown_at")
        if shown is not None and now - float(shown) < NOTICE_EVERY:
            return None
        state["shown_at"] = now
        _write(state_path, state)
        from kinetic import harness
        command = harness.skill_command(harness_id, "update")
        return f"kinetic {latest} is available (you have {here}): run {command}"
    except Exception:  # noqa: BLE001 - a notice must never break a session
        return None


if __name__ == "__main__":
    if len(sys.argv) == 4 and sys.argv[1] == "refresh":
        latest_release(sys.argv[2], sys.argv[3], max_age=0)
