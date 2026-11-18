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
#   review-states  record_verify, submit_review, record_review
#   checkpoint     checkpoint, last_checkpoint
CAPABILITIES = frozenset({"review-states", "checkpoint"})


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
        return frozenset()

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
