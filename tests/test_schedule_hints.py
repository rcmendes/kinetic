"""The schedule-hints capability: a store may reorder kinetic's ready tickets, never extend them."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import frontier, orbit_store, ticket_store
from kinetic.tracker import adapter_defaults

ROOT = Path(__file__).resolve().parent.parent


class HintStore:
    """Declares schedule-hints and answers with a fixed order (or raises)."""

    def __init__(self, order=None, error=None):
        self.order, self.error, self.calls = order, error, []

    def capabilities(self):
        return frozenset({"schedule-hints"})

    def hints(self, run_dir, st, ready_ids):
        self.calls.append(list(ready_ids))
        if self.error:
            raise self.error
        return self.order


def tickets():
    base = {"status": "todo", "blocked_by": [], "snapshot_published": False}
    return {"a": dict(base, repo="r1"), "b": dict(base, repo="r2"), "c": dict(base, repo="r3")}


class FrontierHintsTest(unittest.TestCase):
    def ready(self, store, cap=3):
        with patch.object(ticket_store, "for_state", return_value=store):
            return frontier.ready({"tickets": tickets()}, cap)

    def test_store_order_decides_which_tickets_the_cap_keeps(self):
        self.assertEqual(self.ready(HintStore(["c", "b", "a"]), cap=2), ["c", "b"])

    def test_a_store_without_the_capability_leaves_the_order_alone(self):
        self.assertEqual(self.ready(ticket_store.LocalTicketStore()), ["a", "b", "c"])

    def test_a_ticket_the_store_adds_is_never_scheduled(self):
        self.assertEqual(self.ready(HintStore(["zzz", "c", "a", "b"])), ["c", "a", "b"])

    def test_tickets_the_store_leaves_out_keep_their_own_order_after_the_hinted_ones(self):
        self.assertEqual(self.ready(HintStore(["c"])), ["c", "a", "b"])

    def test_a_failing_store_falls_back_to_kinetics_order(self):
        self.assertEqual(self.ready(HintStore(error=ticket_store.TicketStoreError("down"))),
                         ["a", "b", "c"])

    def test_one_candidate_needs_no_hint(self):
        store = HintStore(["a"])
        with patch.object(ticket_store, "for_state", return_value=store):
            frontier.ready({"tickets": {"a": tickets()["a"]}})
        self.assertEqual(store.calls, [])

    def test_a_blocked_ticket_is_not_offered_to_the_store(self):
        t = tickets()
        t["b"]["blocked_by"] = [{"id": "a", "type": "merge"}]
        store = HintStore(["b", "c", "a"])
        with patch.object(ticket_store, "for_state", return_value=store):
            self.assertEqual(frontier.ready({"tickets": t}), ["c", "a"])
        self.assertEqual(store.calls, [["a", "c"]])


@unittest.skipUnless(shutil.which("orbit"), "orbit CLI not installed")
class OrbitHintsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.anchor = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="user.name",
                        GIT_CONFIG_VALUE_0="Test", GIT_CONFIG_KEY_1="user.email",
                        GIT_CONFIG_VALUE_1="test@example.com")
        self.orbit("init", "--agents", "none")
        self.orbit("setup", str(ROOT / "profiles" / "kinetic.toml"))
        label = "kinetic-run-hint-run"
        # created first, so Orbit's default order puts the lone ticket ahead of the chain head
        self.lone = self.add("Lone", label)
        self.head = self.add("Chain head", label)
        mid = self.add("Chain middle", label)
        self.add_dep(mid, self.head)
        tail = self.add("Chain tail", label)
        self.add_dep(tail, mid)
        self.st = {"run_id": "hint-run", "run_dir": str(self.anchor / "run")}
        self.store = orbit_store.make({"anchor": str(self.anchor),
                                            "names": adapter_defaults(ROOT / "trackers" / "orbit.md")})

    def orbit(self, *args):
        p = subprocess.run(["orbit", "--dir", str(self.anchor), *args, "--json"], cwd=self.anchor,
                           env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)["data"]

    def add(self, title, label):
        return self.orbit("add", title, "--label", label)["id"]

    def add_dep(self, waiting, on):
        self.orbit("depend", waiting, on)

    def test_store_declares_schedule_hints(self):
        self.assertTrue(ticket_store.has(self.store, "schedule-hints"))

    def test_critical_path_puts_the_chain_head_first(self):
        got = self.store.hints(None, self.st, [self.lone, self.head])
        self.assertEqual(got, [self.head, self.lone])

    def test_only_the_offered_tickets_come_back(self):
        self.assertEqual(self.store.hints(None, self.st, [self.lone]), [self.lone])

    def test_a_ticket_orbit_does_not_list_keeps_its_place_after_the_listed_ones(self):
        self.assertEqual(self.store.hints(None, self.st, ["01-not-published", self.head]),
                         [self.head, "01-not-published"])

    def test_touch_overlap_sorts_behind_safe_work_despite_longer_critical_path(self):
        running = self.add("Running", "kinetic-run-hint-run")
        self.orbit("property", "set", running, "touches", "--value", '{"paths":["src/shared"]}')
        self.orbit("claim", running, "--by", "implementer")
        self.orbit("property", "set", self.head, "touches", "--value", '{"paths":["src/shared/auth.py"]}')
        self.orbit("property", "set", self.lone, "touches", "--value", '{"paths":["src/other.py"]}')
        self.assertEqual(self.store.hints(None, self.st, [self.head, self.lone]),
                         [self.lone, self.head])
