from __future__ import annotations

import os
import shlex

import _common


def _run_arg(cmd: str):
    """The --run value of the first `kinetic project use-run` in cmd, or None."""
    try:
        words = shlex.split(cmd)
    except ValueError:
        return None
    for i in range(len(words) - 2):
        # the word may be a path (`python3 <plugin-root>/bin/kinetic project use-run`)
        if os.path.basename(words[i]) == "kinetic" and words[i + 1:i + 3] == ["project", "use-run"]:
            rest = words[i + 3:]
            for j, w in enumerate(rest):
                if w == "--run" and j + 1 < len(rest):
                    return rest[j + 1]
                if w.startswith("--run="):
                    return w[len("--run="):]
            return None
    return None


def main(inp):
    """After `kinetic project use-run --run DIR`, bind this session to that run in its project
    and record the session as the run's owner.
    Any shell command inside a ticket worktree also signals that ticket's liveness."""
    _common.touch_ticket(inp)
    cmd = str((inp.get("tool_input") or {}).get("command") or "")
    sid = inp.get("session_id")
    if not sid or "kinetic project use-run" not in cmd or _common.state is None:
        return
    arg = _run_arg(cmd)
    run = _common.project.run_for_path(_common.abs_path(arg, inp.get("cwd"))) if arg else None
    f = _common.session_file(_common.project.find(run), sid) if run else None
    if f is None:
        return
    _common.state.atomic_write(f, os.path.realpath(run))
    _common.state.set_owner(str(run), sid)


if __name__ == "__main__":
    _common.run(main)
