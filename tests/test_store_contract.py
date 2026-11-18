"""One contract every ticket store must meet, run against each store.

A store passes the core section, plus the section of each capability it declares in
capabilities(). A section for a capability the store does not declare is skipped, so a
new store (Linear, Jira, ...) needs to implement only what it declares.

To add a store: subclass StoreContract with unittest.TestCase and implement
`store_record()` (the run's ticket_store record, or None for the default) and
`prepare()` (anything the store needs before a run starts, e.g. a Board).
"""
from __future__ import annotations

import copy
import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import orbit_store, project, state, ticket_store, verify
from kinetic.tracker import ROOT, adapter_defaults


class FakeStore:
    """The smallest store: the core methods only, no capabilities() at all."""

    def read(self, run_dir, st):
        return st.setdefault("tickets", {})

    def write(self, run_dir, st, before, after):
        return None

    def stage_path(self, run_dir):
        return None

    def publish(self, run_dir, st):
        return {i: i for i in st.get("tickets", {})}

    def publish_spec(self, run_dir, st, title, body):
        return {"ref": "spec.md", "published": False}

    def sync_spec(self, run_dir, st, event):
        return None

    def touch(self, run_dir, st, ticket_id):
        return None

    def health(self, settings):
        return []


class StoreContract:
    """Mixin: the checks every store must pass. Subclass it with unittest.TestCase."""

    def store_record(self):
        return None

    def prepare(self, anchor: Path) -> None:
        return None

    def setUp(self):
        tmp = tempfile.mkdtemp()
        # Orbit may still be finishing a write under .git when the test ends.
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        env = {**os.environ, "GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}
        patcher = patch.dict(os.environ, env)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.anchor = project.create(Path(tmp), "contract")
        self.prepare(self.anchor)
        self.repo = self.anchor / "svc"
        self.repo.mkdir()
        (self.repo / ".kinetic.json").write_text(json.dumps({"verify": "true"}))
        self.run_dir = project.run_dir(self.anchor, "contract-run")
        state.init(self.run_dir, "feature", "x", ticket_store_record=self.store_record())
        state.set_repo(self.run_dir, "svc", str(self.repo), "integration")
        (self.run_dir / "spec.md").write_text("# Feature\n\nBuild it.")
        state.publish_spec(self.run_dir, "Feature")
        state.record_gate(self.run_dir, "spec", True)
        state.set_slices(self.run_dir, ["core"])
        state.set_ticket(self.run_dir, "a", repo="svc", slice="core")
        state.set_ticket(self.run_dir, "b", repo="svc", slice="core",
                         blocked_by=[{"id": "a", "type": "merge"}])
        state.set_ticket(self.run_dir, "h", repo="svc", slice="core", kind="human")
        ids = state.publish_tickets(self.run_dir)
        state.record_gate(self.run_dir, "breakdown", True)
        self.a, self.b, self.h = ids["a"], ids["b"], ids["h"]

    def store(self):
        return ticket_store.for_state(state.load(self.run_dir, tickets=False))

    def tickets(self):
        return state.load(self.run_dir)["tickets"]

    def native_placement(self, ticket_id):
        """(repository, component) as the store holds them natively, or None if the
        store cannot show that; the placement section is skipped without it."""
        return None

    def native_priority(self, ticket_id):
        """The ticket's priority as an index into the store's own ordered names (0 is highest),
        or None if the store cannot show that; the priority section is skipped without it."""
        return None

    def need(self, capability):
        if not ticket_store.has(self.store(), capability):
            self.skipTest(f"store does not declare {capability}")

    # -- core: every store -------------------------------------------------

    def test_published_tickets_read_back(self):
        t = self.tickets()
        self.assertEqual({self.a, self.b, self.h}, set(t))
        self.assertEqual((t[self.a]["repo"], t[self.a]["status"]), ("svc", "todo"))
        self.assertEqual(t[self.h]["kind"], "human")

    def test_a_status_change_persists(self):
        state.set_ticket(self.run_dir, self.a, status="running")
        self.assertEqual(self.tickets()[self.a]["status"], "running")

    def test_a_dependency_keeps_its_edge_type(self):
        edges = self.tickets()[self.b]["blocked_by"]
        self.assertEqual([(e["id"], e["type"]) for e in edges], [(self.a, "merge")])

    def test_publishing_again_changes_nothing(self):
        before = self.tickets()
        state.publish_tickets(self.run_dir)
        self.assertEqual(set(self.tickets()), set(before))

    def test_declared_capabilities_are_known_names(self):
        declared = getattr(self.store(), "capabilities", lambda: frozenset())()
        self.assertTrue(set(declared) <= ticket_store.CAPABILITIES, declared)

    # -- capability sections: run only for a store that declares them ------

    def test_checkpoint_round_trips(self):
        self.need("checkpoint")
        state.set_ticket(self.run_dir, self.a, status="running")
        state.record_checkpoint(self.run_dir, self.a, "skeleton written", "add tests")
        last = state.last_checkpoint(self.run_dir, self.a)
        self.assertEqual((last["progress"], last["next"]), ("skeleton written", "add tests"))

    def test_schedule_hints_only_reorder_the_ready_tickets(self):
        self.need("schedule-hints")
        st = state.load(self.run_dir)
        ready = [self.a, self.h]
        hinted = self.store().hints(self.run_dir, st, list(ready))
        self.assertEqual(sorted(hinted), sorted(ready))

    def test_query_rollup_counts_the_runs_tickets(self):
        self.need("query")
        counts = self.store().rollup(self.run_dir, state.load(self.run_dir))
        self.assertEqual(counts, {"done": 0, "blocked": 1, "open": 2, "cancelled": 0, "total": 3})
        state.human_done(self.run_dir, self.h)
        counts = self.store().rollup(self.run_dir, state.load(self.run_dir))
        self.assertEqual(counts, {"done": 1, "blocked": 1, "open": 1, "cancelled": 0, "total": 3})

    def test_query_changes_reports_what_changed_since_a_cursor(self):
        self.need("query")
        store = self.store()
        first = store.changes(self.run_dir, state.load(self.run_dir), None)
        self.assertEqual({self.a, self.b, self.h}, set(first["ids"]))
        quiet = store.changes(self.run_dir, state.load(self.run_dir), first["cursor"])
        self.assertEqual(quiet["ids"], [])
        state.set_ticket(self.run_dir, self.a, status="running")
        later = store.changes(self.run_dir, state.load(self.run_dir), quiet["cursor"])
        self.assertIn(self.a, later["ids"])
        self.assertTrue(set(later["ids"]) <= {self.a, self.b, self.h})

    def test_idempotent_write_repeats_safely(self):
        self.need("idempotent-write")
        st = state.load(self.run_dir)
        before = copy.deepcopy(st["tickets"])
        after = copy.deepcopy(before)
        after[self.a]["status"] = "running"
        store = self.store()
        store.write(self.run_dir, st, before, after)
        store.write(self.run_dir, st, before, after)
        self.assertEqual(self.tickets()[self.a]["status"], "running")

    def test_placement_is_held_natively(self):
        self.need("placement")
        placed = self.native_placement(self.a)
        if placed is None:
            self.skipTest("store gives no native view of placement")
        self.assertEqual(placed, ("svc", "core"))

    def test_priority_follows_the_critical_path(self):
        self.need("priority")
        first, waits = self.native_priority(self.a), self.native_priority(self.b)
        if first is None:
            self.skipTest("store gives no native view of priority")
        self.assertLess(first, waits)  # a blocks b, so a ranks higher

    def test_a_question_parks_the_ticket_and_an_answer_returns_it(self):
        self.need("human-question")
        state.set_ticket(self.run_dir, self.a, status="running")
        state.ask_question(self.run_dir, self.a, "Which seam?")
        self.assertEqual(self.tickets()[self.a]["status"], "needs_decision")
        open_ = self.store().open_questions(self.run_dir, state.load(self.run_dir))
        self.assertEqual([(q["id"], q["question"]) for q in open_], [(self.a, "Which seam?")])
        state.answer_question(self.run_dir, self.a, "Seam A")
        self.assertEqual(self.tickets()[self.a]["status"], "running")
        self.assertEqual(self.store().open_questions(self.run_dir, state.load(self.run_dir)), [])

    def test_answering_without_an_open_question_is_refused(self):
        self.need("human-question")
        with self.assertRaises(ValueError):
            state.answer_question(self.run_dir, self.a, "nothing was asked")

    def test_review_states_complete_a_verified_ticket(self):
        self.need("review-states")
        state.set_ticket(self.run_dir, self.a, status="running")
        cfg = self.anchor / ".kinetic" / "config.json"
        verify.run(self.run_dir, self.a, "svc", self.repo, cfg, scope="ticket")
        state.submit_review(self.run_dir, self.a)
        state.record_review(self.run_dir, self.a, "spec", "approve")
        state.record_review(self.run_dir, self.a, "standards", "approve")
        self.assertIn(self.tickets()[self.a]["status"], ("done", "merged"))


class LocalStoreContract(StoreContract, unittest.TestCase):
    pass


class FakeStoreContract(StoreContract, unittest.TestCase):
    """A store with the core methods only passes the core section."""

    def setUp(self):
        patcher = patch.object(ticket_store, "store_for", return_value=FakeStore())
        patcher.start()
        self.addCleanup(patcher.stop)
        super().setUp()

    def test_it_declares_no_capability(self):
        self.assertFalse(any(ticket_store.has(self.store(), c) for c in ticket_store.CAPABILITIES))


@unittest.skipUnless(shutil.which("orbit"), "orbit CLI not installed")
class OrbitStoreContract(StoreContract, unittest.TestCase):
    def store_record(self):
        return {"kind": "orbit", "config": {
            "anchor": str(self.anchor), "names": adapter_defaults(ROOT / "trackers/orbit.md")}}

    def native_placement(self, ticket_id):
        done = subprocess.run(["orbit", "--dir", str(self.anchor), "show", ticket_id, "--json"],
                              cwd=self.anchor, capture_output=True, text=True)
        issue = json.loads(done.stdout)["data"]
        return issue.get("repository"), issue.get("component")

    def native_priority(self, ticket_id):
        done = subprocess.run(["orbit", "--dir", str(self.anchor), "show", ticket_id, "--json"],
                              cwd=self.anchor, capture_output=True, text=True)
        names = ["highest", "high", "medium", "low"]
        return names.index(json.loads(done.stdout)["data"]["priority"])

    def prepare(self, anchor):
        for args in (["init", "."], ["setup", str(ROOT / "profiles" / "kinetic.toml")]):
            done = subprocess.run(["orbit", "--dir", str(anchor), *args, "--json"], cwd=anchor,
                                  capture_output=True, text=True)
            assert done.returncode == 0, done.stdout + done.stderr


if __name__ == "__main__":
    unittest.main()
