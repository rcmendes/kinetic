from __future__ import annotations

import json

import _common

NO_PROJECT = ("kinetic: no project here. If this conversation becomes a project "
              "(a goal, the systems involved, references), offer /kinetic:start.")


def _one(s) -> str:
    return " ".join(str(s).split())


def _emit(text: str) -> None:
    print(json.dumps({"hookSpecificOutput": {"hookEventName": "SessionStart",
                                              "additionalContext": text}}))


def _update_notice():
    """The once-a-day line saying a newer release exists, or None. Local files only; the
    lookup itself runs in a detached process. Never raises."""
    try:
        from pathlib import Path

        from kinetic import config, harness, versions
        return versions.notice(Path.home(), config._load(harness.DEFAULT_SETTINGS), _common._HARNESS)
    except Exception:
        return None


def main(inp):
    # Always name the harness first: skills pass this id to `kinetic harness`, so a session
    # outside a project, or with no run, still gets its own harness's profile.
    head = f"kinetic harness: {_common._HARNESS}"
    notice = _update_notice()
    if notice:
        head += "\n" + notice
    try:  # marks also expire here, not only when another mark is written
        proj = _common.project.find(inp.get("cwd") or None) if _common.project else None
    except Exception:
        proj = None
    _common.sweep_sessions(proj)
    # no owner-scope check: orient every session, so a new one can resume
    run_dir = _common.run_dir_for(inp)
    if run_dir is None:
        if _common.project is None:
            _emit(head)
            return
        proj = _common.project.find(inp.get("cwd") or None)
        if proj is None:
            _emit(f"{head}\n{NO_PROJECT}")
        else:
            runs = _common.project.open_runs(proj)
            if runs:  # never pick one for the session; name them
                _emit(f"{head}\n" + _one("kinetic: open runs here: " + ", ".join(r["run_id"] for r in runs)
                                          + ". Resume one with /kinetic:run."))
            else:
                _emit(head)
        return
    from kinetic import next_step
    st = _common.state.load(run_dir)
    s = next_step.suggest(st)
    _emit("\n".join([head, *(_one(x) for x in (f"kinetic run {st['run_id']}", f"lane: {st['lane']}",
                                               f"phase: {st['phase']}", f"next: {s['command']}",
                                               f"why: {s['reason']}"))]))


if __name__ == "__main__":
    _common.run(main)
