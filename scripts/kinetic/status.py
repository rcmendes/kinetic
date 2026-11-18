from __future__ import annotations

import argparse
import sys

from kinetic import frontier, state, ticket_store


def render(st: dict) -> str:
    out = [f"Lane: {st['lane']} | Phase: {st['phase']}", "",
           "Waiting on you: " + (", ".join(frontier.waiting_on_you(st)) or "none"), "",
           "| id | kind | repo | status | blocked by |", "| --- | --- | --- | --- | --- |"]
    for i, t in sorted(st["tickets"].items()):
        blockers = ", ".join(f"{e['id']} ({e['type']})" for e in t["blocked_by"]) or "—"
        out.append(f"| {i} | {state.field(t, 'kind')} | {t.get('repo') or '—'} | {t['status']} | {blockers} |")
    stuck = sorted(i for i, t in st["tickets"].items() if t.get("liveness") == "stuck")
    if stuck:
        out += ["", "implementer may be stuck: " + ", ".join(stuck)]
    out += ["", "Ready: " + (", ".join(frontier.ready(st)) or "none"), "", "Gates:"]
    gates = st.get("gates") or {}
    out += [f"- {g}: {'yes' if v.get('approved') else 'no'}" for g, v in sorted(gates.items())]
    if not gates:
        out.append("- none recorded")
    return "\n".join(out) + "\n"


def summary(run_dir) -> str:
    """Lane, phase, progress counts and gates, without reading every ticket when the
    store can answer the counts itself (the `query` capability)."""
    st = state.load(run_dir, tickets=False)
    store = ticket_store.for_state(st)
    if ticket_store.has(store, "query"):
        c = store.rollup(run_dir, st)
    else:
        c = ticket_store.tickets_rollup(store.read(run_dir, st))
    out = [f"Lane: {st['lane']} | Phase: {st['phase']}", "",
           f"Progress: {c['done']} of {c['total']} done, {c['blocked']} blocked, {c['open']} open",
           "", "Gates:"]
    gates = st.get("gates") or {}
    out += [f"- {g}: {'yes' if v.get('approved') else 'no'}" for g, v in sorted(gates.items())]
    if not gates:
        out.append("- none recorded")
    return "\n".join(out) + "\n"


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.status")
    p.add_argument("--run", required=True)
    p.add_argument("--summary", action="store_true",
                   help="counts and gates only; one store call instead of reading every ticket")
    a = p.parse_args(argv)
    try:
        print(summary(a.run) if a.summary else render(state.load(a.run)), end="")
    except (ValueError, OSError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
