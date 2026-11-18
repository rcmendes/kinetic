"""Ticket store: where a run's tickets live, behind one interface.

The local ticket store keeps tickets in the run's state.json (kinetic's
behavior before ticket stores existed). Any other store is a module named
`kinetic.<kind>_store` exposing `make(config) -> TicketStore`; this module
never names one, so kinetic's core stays independent of every store.
"""
from __future__ import annotations

import importlib
import importlib.util
from pathlib import Path
from typing import Protocol

LOCAL = {"kind": "local"}

# Optional abilities a store may declare in capabilities(). Kinetic calls a
# capability's methods only after has(store, name); without it, kinetic does
# what it did before the capability existed.
#   review-states    record_verify, submit_review, record_review
#   checkpoint       checkpoint, last_checkpoint
#   schedule-hints   hints
# Named for the stores that will declare them; each one's methods are defined by
# the task that implements it (see docs/specs/kinetic-portable-capabilities-design.md).
#   query            changes and rollup reads
#   idempotent-write a repeated write is safe and may carry a version guard
#   placement        a ticket's repo and slice round-trip as native filterable fields
#   priority         a ticket's priority rank round-trips
#   human-question   ask, answer, open questions
#   external-ref     a ticket's and the spec's refs round-trip
# Words here are kinetic's: a store maps them to its own fields.
CAPABILITIES = frozenset({
    "review-states", "checkpoint", "schedule-hints",
    "query", "idempotent-write", "placement", "priority", "human-question", "external-ref",
})


class TicketStoreError(Exception):
    """A ticket store could not read or write the run's tickets."""


class TicketStore(Protocol):
    def read(self, run_dir, st: dict) -> dict:
        """Every ticket of the run, staged ones included, keyed by ticket id."""

    def write(self, run_dir, st: dict, before: dict, after: dict) -> None:
        """Persist the change from `before` to `after`, fully or not at all."""

    def stage_path(self, run_dir) -> Path | None:
        """Where staged (not yet published) tickets live, or None."""

    def publish(self, run_dir, st: dict) -> dict:
        """Publish staged tickets; returns provisional id -> final id."""

    def publish_spec(self, run_dir, st: dict, title: str, body: str) -> dict:
        """Publish the run's spec; returns {"ref": <spec reference>, "published": bool}."""

    def sync_spec(self, run_dir, st: dict, event: str) -> None:
        """Mirror a spec lifecycle event on the store: "gate-approved",
        "gate-rejected" or "building" (breakdown approved)."""

    def capabilities(self) -> frozenset:
        """Names from CAPABILITIES this store provides; empty for a core-only store."""

    def touch(self, run_dir, st: dict, ticket_id: str) -> None:
        """Signal that a running ticket's implementer is alive."""

    def health(self, settings: Path) -> list:
        """Doctor rows ({"name", "status", "fix"}) this store needs."""


class ReviewStates(TicketStore, Protocol):
    """The `review-states` capability: reviews and verify evidence live in the store."""
    def record_verify(self, run_dir, st: dict, ticket_id: str, evidence: dict) -> None:
        """Persist verify evidence without marking the ticket complete."""

    def submit_review(self, run_dir, st: dict, ticket_id: str, evidence: list,
                      scope: str = "ticket") -> None:
        """Submit verified work for ticket, fix or integration review."""

    def record_review(self, run_dir, st: dict, ticket_id: str, axis: str,
                      verdict: str, reason: str = "") -> None:
        """Record an independent review and apply its native verdict."""


class Checkpoints(TicketStore, Protocol):
    """The `checkpoint` capability: a ticket's handoff state lives in the store."""

    def checkpoint(self, run_dir, st: dict, ticket_id: str, progress: str,
                   next: str | None = None, blocker: str | None = None) -> None:
        """Record the ticket's latest handoff; omitted fields keep their last value."""

    def last_checkpoint(self, run_dir, st: dict, ticket_id: str) -> dict | None:
        """The latest handoff ({"progress", "next", "blocker", "by", "at"}) or None."""


class Query(TicketStore, Protocol):
    """The `query` capability: cheap reads over the run's tickets."""

    def rollup(self, run_dir, st: dict) -> dict:
        """{"done", "blocked", "open", "cancelled", "total"} over the run's tickets (slices and
        the spec are not counted). Blocked: not done and waiting on an unfinished ticket."""

    def changes(self, run_dir, st: dict, cursor: str | None) -> dict:
        """{"cursor", "ids"}: ids of tickets that may have changed since `cursor`, and the
        cursor to pass next time. `cursor=None` returns every ticket. A store that cannot
        tell what changed may return every id; it must never leave a changed ticket out."""


DONE_STATUSES = ("done", "done_with_concerns", "merged", "released")


def tickets_rollup(tickets: dict) -> dict:
    """The rollup counts for a ticket map; shared by stores that count tickets themselves."""
    def finished(tid):
        return (tickets.get(tid) or {}).get("status") in DONE_STATUSES
    out = {"done": 0, "blocked": 0, "open": 0, "cancelled": 0, "total": len(tickets)}
    for t in tickets.values():
        if t.get("status") in DONE_STATUSES:
            out["done"] += 1
        elif t.get("status") == "cancelled":
            out["cancelled"] += 1
        elif any(not finished(e["id"]) for e in t.get("blocked_by") or []):
            out["blocked"] += 1
        else:
            out["open"] += 1
    return out


class HumanQuestion(TicketStore, Protocol):
    """The `human-question` capability: a question parked on a person lives in the store."""

    def ask(self, run_dir, st: dict, ticket_id: str, question: str) -> None:
        """Record an open question on the ticket and move it to `needs_decision`."""

    def answer(self, run_dir, st: dict, ticket_id: str, answer: str) -> None:
        """Record the answer, close the question and return the ticket to the status it
        had before it was asked. Refused when no question is open on the ticket."""

    def open_questions(self, run_dir, st: dict) -> list:
        """[{"id", "question", "at"}] for every unanswered question of the run."""


ASKABLE = ("todo", "running", "in_review", "blocked")


class ScheduleHints(TicketStore, Protocol):
    """The `schedule-hints` capability: the store may suggest an order for ready tickets."""

    def hints(self, run_dir, st: dict, ready_ids: list) -> list:
        """`ready_ids` in the order the store prefers. A hint only reorders: kinetic
        ignores any id it did not offer and keeps the ones left out, in its own order."""


class LocalTicketStore:
    """Tickets stay in state.json under "tickets"; state.save persists them."""

    def read(self, run_dir, st: dict) -> dict:
        return st.setdefault("tickets", {})

    def write(self, run_dir, st: dict, before: dict, after: dict) -> None:
        return None

    def stage_path(self, run_dir) -> Path | None:
        return None

    def publish(self, run_dir, st: dict) -> dict:
        return {i: i for i in st.get("tickets", {})}

    def publish_spec(self, run_dir, st: dict, title: str, body: str) -> dict:
        # spec.md in the run directory is already the published spec
        return {"ref": "spec.md", "published": False}

    def sync_spec(self, run_dir, st: dict, event: str) -> None:
        return None

    def capabilities(self) -> frozenset:
        return frozenset({"query", "human-question", "external-ref"})

    # The ask/answer machine, kept in state.json: state.save persists it.
    def ask(self, run_dir, st: dict, ticket_id: str, question: str) -> None:
        from datetime import datetime, timezone
        ticket = st["tickets"][ticket_id]
        if ticket["status"] not in ASKABLE:
            raise ValueError(f"cannot ask on {ticket_id}: it is {ticket['status']}")
        st.setdefault("questions", []).append({
            "id": ticket_id, "question": question, "return_to": ticket["status"],
            "at": datetime.now(timezone.utc).isoformat(timespec="seconds")})
        ticket["status"] = "needs_decision"

    def answer(self, run_dir, st: dict, ticket_id: str, answer: str) -> None:
        from datetime import datetime, timezone
        asked = next((q for q in st.get("questions", [])
                      if q["id"] == ticket_id and "answer" not in q), None)
        if asked is None:
            raise ValueError(f"no open question on {ticket_id}")
        asked["answer"] = answer
        asked["answered_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
        st["tickets"][ticket_id]["status"] = asked["return_to"]

    def open_questions(self, run_dir, st: dict) -> list:
        return [{"id": q["id"], "question": q["question"], "at": q["at"]}
                for q in st.get("questions", []) if "answer" not in q]

    def rollup(self, run_dir, st: dict) -> dict:
        return tickets_rollup(st.get("tickets", {}))

    def changes(self, run_dir, st: dict, cursor: str | None) -> dict:
        # No history: the cursor is a digest of the tickets, so an unchanged run reports none.
        import hashlib
        import json
        tickets = st.get("tickets", {})
        digest = hashlib.sha256(json.dumps(tickets, sort_keys=True).encode()).hexdigest()
        return {"cursor": digest, "ids": [] if cursor == digest else list(tickets)}

    def touch(self, run_dir, st: dict, ticket_id: str) -> None:
        return None

    def health(self, settings: Path) -> list:
        return []


def has(store, capability: str) -> bool:
    """Whether `store` declares `capability`; a store that predates capabilities has none."""
    if capability not in CAPABILITIES:
        raise ValueError(f"unknown capability '{capability}'")
    declared = getattr(store, "capabilities", None)
    return bool(declared) and capability in declared()


def _module(kind: str) -> str:
    return f"kinetic.{kind}_store"


def store_for(record: dict | None) -> TicketStore:
    record = record or LOCAL
    kind = record.get("kind", "local")
    if kind == "local":
        return LocalTicketStore()
    # The store mutates this same dict (first-use settings), so save() persists it.
    return importlib.import_module(_module(kind)).make(record.setdefault("config", {}))


def for_state(st: dict) -> TicketStore:
    return store_for(st.get("ticket_store"))


def is_local(st: dict) -> bool:
    return (st.get("ticket_store") or LOCAL).get("kind", "local") == "local"


def for_tracker(tracker: str) -> dict:
    """The ticket_store record for a new run on this tracker."""
    if tracker and tracker != "local" and importlib.util.find_spec(_module(tracker)) is not None:
        return {"kind": tracker, "config": {}}
    return dict(LOCAL)


def installation_files(clone: Path, home: Path) -> dict:
    """Optional files supplied by packaged ticket stores for the shared installer."""
    files = {}
    for path in sorted(Path(__file__).parent.glob("*_store.py")):
        if path.stem == "ticket_store":
            continue
        module = importlib.import_module(f"kinetic.{path.stem}")
        provider = getattr(module, "installation_files", None)
        if provider:
            files.update(provider(clone, home))
    return files
