from __future__ import annotations

import argparse
import json
import sys

from kinetic import graph, state, ticket_store

DONE = {"done", "done_with_concerns", "merged", "released"}
MERGED = {"merged", "released"}
# the orchestrator keeps these tickets' implementer and worktree alive
OCCUPYING = {"running", "needs_decision", "blocked", "in_review"}


def _human(t: dict) -> bool:
    return state.field(t, "kind") == "human"


def _satisfied(edge: dict, blocker: dict) -> bool:
    st, typ = blocker["status"], edge["type"]
    if _human(blocker):
        return st in DONE  # a person finishing it is the only signal; edge types do not apply
    if typ == "snapshot":
        return st in DONE and bool(blocker.get("snapshot_published"))
    if typ == "merge":
        return st in MERGED
    if typ == "release":
        return st == "released"
    return False


def ready(st: dict, cap: int = 3) -> list[str]:
    tickets = st["tickets"]
    try:
        order = graph.topo_order(tickets)
    except ValueError:
        return []  # cycle: the graph validator reports it, never dispatch into it
    running = [t for t in tickets.values() if t["status"] in ("running", "in_review")]
    busy = {t["repo"] for t in tickets.values() if t["status"] in OCCUPYING}
    candidates = [i for i in order
                  if not _human(tickets[i]) and tickets[i]["status"] == "todo" and tickets[i].get("repo")
                  and _unblocked(tickets[i], tickets)]
    out = []
    for i in _hinted(st, candidates):
        if len(running) + len(out) >= cap:
            break
        if tickets[i]["repo"] in busy:
            continue
        out.append(i)
        busy.add(tickets[i]["repo"])
    return out


def _hinted(st: dict, candidates: list) -> list:
    """Candidates in the ticket store's preferred order, when it gives hints. A hint
    only reorders what kinetic already judged ready; a store failure changes nothing."""
    if len(candidates) < 2:
        return candidates
    store = ticket_store.for_state(st)
    if not ticket_store.has(store, "schedule-hints"):
        return candidates
    try:
        hinted = store.hints(st.get("run_dir"), st, list(candidates))
    except ticket_store.TicketStoreError:
        return candidates
    ranked = [i for i in dict.fromkeys(hinted) if i in candidates]
    return ranked + [i for i in candidates if i not in ranked]


def _unblocked(t: dict, tickets: dict) -> bool:
    return all(e["id"] in tickets and _satisfied(e, tickets[e["id"]]) for e in t["blocked_by"])


def waiting_on_you(st: dict) -> list[str]:
    tickets = st["tickets"]
    try:
        order = graph.topo_order(tickets)
    except ValueError:
        return []
    return [i for i in order if _human(tickets[i]) and tickets[i]["status"] == "todo"
            and _unblocked(tickets[i], tickets)]


def only_human_left(st: dict) -> bool:
    tickets = st["tickets"]
    # a person must actually be able to act now, else the stall is elsewhere
    if ready(st) or not waiting_on_you(st) or any(t["status"] in OCCUPYING for t in tickets.values()):
        return False
    todo = [i for i, t in tickets.items() if t["status"] == "todo"]
    if not todo:
        return False

    def human_todo(i: str) -> bool:
        return _human(tickets[i]) and tickets[i]["status"] == "todo"

    return all(human_todo(i) or any(u in tickets and human_todo(u) for u in graph._upstream(tickets, i))
               for i in todo)


def report(st: dict, cap: int = 3) -> dict:
    return {"ready": ready(st, cap), "waiting_on_you": waiting_on_you(st),
            "only_human_left": only_human_left(st)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.frontier")
    p.add_argument("--run", required=True)
    p.add_argument("--cap", type=int, default=3)
    a = p.parse_args(argv)
    try:
        print(json.dumps(report(state.load(a.run), a.cap)))
    except (ValueError, OSError, KeyError) as e:
        print(str(e), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
