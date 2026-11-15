from __future__ import annotations

import os
import shlex
from datetime import datetime, timezone

import _common


def _kinetic_args(cmd: str, sub: str):
    """The words after the first `kinetic project <sub>` in cmd, or None."""
    if "kinetic" not in cmd:
        return None
    try:  # split on ; && | too, so `start-session;` is still the word `start-session`
        s = shlex.shlex(cmd, posix=True, punctuation_chars=True)
        s.whitespace_split = True
        words = list(s)
    except ValueError:
        return None
    for i in range(len(words) - 2):
        # the word may be a path (`python3 <plugin-root>/bin/kinetic project use-run`)
        if os.path.basename(words[i]) == "kinetic" and words[i + 1:i + 3] == ["project", sub]:
            return words[i + 3:]
    return None


def _run_arg(cmd: str):
    """The --run value of the first `kinetic project use-run` in cmd, or None."""
    rest = _kinetic_args(cmd, "use-run") or []
    for j, w in enumerate(rest):
        if w == "--run" and j + 1 < len(rest):
            return rest[j + 1]
        if w.startswith("--run="):
            return w[len("--run="):]
    return None


def _start_mark(inp, sid):
    """Mark the session as opened with the start skill, unless it sits in a project."""
    f = _common.mark_file(sid)
    cwd, launch = inp.get("cwd") or os.getcwd(), _common._launch_dir(inp)
    if f is None or _common.project is None or _common.project.find(cwd) or (
            launch and _common.project.find(launch)):
        return
    _common.state.atomic_write(f, datetime.now(timezone.utc).isoformat())
    _common.sweep_sessions()


def main(inp):
    """After `kinetic project use-run --run DIR`, bind this session to that run in its project
    and record the session as the run's owner.
    Any shell command inside a ticket worktree also signals that ticket's liveness."""
    _common.touch_ticket(inp)
    cmd = str((inp.get("tool_input") or {}).get("command") or "")
    sid = inp.get("session_id")
    if not sid or _common.state is None:
        return
    if _kinetic_args(cmd, "start-session") is not None:
        _start_mark(inp, sid)
    if _kinetic_args(cmd, "use-run") is None:
        return
    arg = _run_arg(cmd)
    run = _common.project.run_for_path(_common.abs_path(arg, inp.get("cwd"))) if arg else None
    proj = _common.project.find(run) if run else None
    f = _common.session_file(proj, sid) if proj else None
    if f is None:
        return
    _common.state.atomic_write(f, os.path.realpath(run))
    _common.sweep_sessions(proj)
    _common.state.set_owner(str(run), sid)


if __name__ == "__main__":
    _common.run(main)
