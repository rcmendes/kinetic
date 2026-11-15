"""spec -> slices -> tickets, read from run state; works for every ticket store."""
from __future__ import annotations

import argparse
import json
import sys

from kinetic import state


def build(st: dict, focus: str | None = None) -> dict:
    spec = st.get("spec")
    by_slice: dict = {s: [] for s in st.get("slices", [])}
    unsliced = []
    for tid, t in sorted(st["tickets"].items()):
        if t.get("slice"):
            by_slice.setdefault(t["slice"], []).append(tid)
        else:
            unsliced.append(tid)
    if focus is not None and focus != spec:
        if focus in st["tickets"]:
            owner = st["tickets"][focus].get("slice")
            by_slice = {owner: [focus]} if owner else {}
            unsliced = [] if owner else [focus]
        elif focus in by_slice:
            by_slice, unsliced = {focus: by_slice[focus]}, []
        else:
            raise ValueError(f"unknown id: {focus}")
    return {"spec": spec, "slices": [{"id": s, "tickets": v} for s, v in by_slice.items()],
            "unsliced": unsliced}


def render(tree: dict) -> str:
    groups = [(f"slice {s['id']}", s["tickets"]) for s in tree["slices"]]
    if tree["unsliced"]:
        groups.append(("no slice", tree["unsliced"]))
    out = [f"spec: {tree['spec'] or 'not recorded'}"]
    for g, (name, ids) in enumerate(groups):
        last = g == len(groups) - 1
        out.append(("└── " if last else "├── ") + name)
        pad = "    " if last else "│   "
        out += [f"{pad}{'└── ' if i == len(ids) - 1 else '├── '}{t}" for i, t in enumerate(ids)]
    return "\n".join(out)


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.trace")
    p.add_argument("--run", required=True)
    p.add_argument("--id", help="a spec, slice or ticket id: show only its branch")
    p.add_argument("--json", action="store_true")
    a = p.parse_args(argv)
    try:
        tree = build(state.load(a.run), a.id)
    except (ValueError, OSError, KeyError) as e:
        print(e.args[0] if isinstance(e, KeyError) and e.args else str(e), file=sys.stderr)
        return 1
    print(json.dumps(tree) if a.json else render(tree))
    return 0


if __name__ == "__main__":
    sys.exit(main())
