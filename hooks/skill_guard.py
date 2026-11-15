from __future__ import annotations

import json
from pathlib import Path

import _common

_DEFAULT = Path(__file__).resolve().parent / "skill_guard.json"


def skill_name(inp: dict):
    ti = inp.get("tool_input")
    name = ti.get("skill") if isinstance(ti, dict) else None
    if not isinstance(name, str):
        return None
    return name[1:] if name.startswith("/") else name


def _string_list(value):
    return set(value) if isinstance(value, list) and all(isinstance(v, str) for v in value) else None


def deny_list(run_dir=None) -> set:
    """The project's skill_guard.deny for a run's project when it is a list of strings, else the default."""
    if run_dir is not None:
        try:
            proj = _common.project.find(run_dir)
            found = _string_list(((_common.project.load(proj).get("skill_guard") or {}).get("deny")))
            if found is not None:
                return found
        except Exception:
            pass
    return _string_list(json.loads(_DEFAULT.read_text()).get("deny")) or set()


def main(inp):
    name = skill_name(inp)
    if not name or name.startswith(("kinetic:", "kntc-")):
        return
    found = _common.active_run(inp, tickets=False)
    if found and found[1].get("phase") != "done":  # a done run is no run: fall through to the mark
        run_dir, st = found
        if name not in deny_list(run_dir):
            return
        nxt = ""
        try:  # the run is known: deny even when the next step cannot be worked out
            from kinetic import next_step
            nxt = f". Next: {next_step.suggest(_common.state.load(run_dir))['command']}"
        except Exception:
            pass
        _common.pre_tool("deny", f"kinetic run {Path(run_dir).name} is active (phase {st.get('phase')}){nxt}.")
    elif name in deny_list() and _common.has_mark(inp):
        _common.pre_tool("ask", f"This conversation was started with /kinetic:start. Allow {name}?")


if __name__ == "__main__":
    _common.run(main)
