from __future__ import annotations

import argparse
import json
import os
import sys

from kinetic import frontier, project, state

DONE = {"done", "done_with_concerns", "merged", "released"}


def _r(command: str, reason: str) -> dict:
    return {"command": command, "reason": reason}


def suggest(st: dict | None) -> dict:
    if st is None:
        return _r("/kinetic:run <ref>", "no active run")
    tickets = st["tickets"]
    deciding = sorted(i for i, t in tickets.items() if t["status"] == "needs_decision")
    if deciding:
        return _r("/kinetic:run", f"ticket {deciding[0]} needs a decision")
    stale = state.stale_running(st, state.internal(st["run_dir"]) / "reports")
    if stale:
        return _r("/kinetic:run", "recover " + ", ".join(stale))
    if st["phase"] == "build":
        ready = frontier.ready(st)
        if ready:
            return _r("/kinetic:run", f"{len(ready)} tickets ready")
        if tickets and all(t["status"] in DONE for t in tickets.values()):
            return _r("/kinetic:run", "review and ship")
    if st["phase"] == "ship" and st["gates"].get("ship", {}).get("approved"):
        return _r("/kinetic:run", "retro")
    return _r("/kinetic:status", "check the board")


def _active() -> dict:
    """The suggestion for the run of the cwd, else the project's only open run; outside a project, start."""
    proj = project.find()
    if proj is None:
        return _r("/kinetic:start", "no kinetic project here")
    run = project.run_for_path(os.getcwd())
    if run is None:
        runs = project.open_runs(proj)
        if len(runs) > 1:
            return _r("/kinetic:run", f"{len(runs)} open runs; open one from its folder")
        run = runs[0]["run_dir"] if runs else None
    return suggest(state.load(run) if run else None)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.next_step")
    p.add_argument("--run", default=None)
    a = p.parse_args(argv)
    try:
        print(json.dumps(suggest(state.load(a.run)) if a.run else _active()))
    except (ValueError, OSError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
