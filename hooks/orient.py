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


def main(inp):
    # no owner-scope check: orient every session, so a new one can resume
    run_dir = _common.run_dir_for(inp)
    if run_dir is None:
        if _common.project is not None and _common.project.find(inp.get("cwd") or None) is None:
            _emit(NO_PROJECT)
        return
    from kinetic import next_step
    st = _common.state.load(run_dir)
    s = next_step.suggest(st)
    _emit("\n".join(_one(x) for x in (f"kinetic run {st['run_id']}", f"lane: {st['lane']}",
                                      f"phase: {st['phase']}", f"next: {s['command']}",
                                      f"why: {s['reason']}")))


if __name__ == "__main__":
    _common.run(main)
