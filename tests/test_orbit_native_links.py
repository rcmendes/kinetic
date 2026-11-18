from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from kinetic import orbit_store, state
from tests.test_orbit_store import make_orbit_run, orbit_available, orbit_json


@unittest.skipUnless(orbit_available(), "Orbit is not installed")
class NativeLinksTest(unittest.TestCase):
    def test_published_links_preserve_edges_without_duplicate_properties(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            state.set_slices(run, ["slice"])
            state.set_ticket(run, "a", repo="api", slice="slice", ships_with="release-1")
            state.set_ticket(run, "b", repo="web", slice="slice",
                             blocked_by=[{"id": "a", "type": "snapshot"}],
                             after=["a"], related=["a"], ships_with="release-1")
            ids = state.publish_tickets(run)
            shown = orbit_json(board, "show", ids["b"])
            self.assertEqual([{k: link[k] for k in ("type", "target", "blocking", "attributes")}
                              for link in shown["links"]], [
                {"type": "blocks", "target": ids["a"], "blocking": True,
                 "attributes": {"edge": "snapshot"}},
                {"type": "after", "target": ids["a"], "blocking": False,
                 "attributes": {}},
                {"type": "relates-to", "target": ids["a"], "blocking": False,
                 "attributes": {}},
                {"type": "ships-with", "target": {"system": "kinetic", "id":
                 f"{state.load(run)['run_id']}:ship:release-1"}, "blocking": False,
                 "attributes": {"group": "release-1"}},
            ])
            self.assertTrue(set(shown["properties"]["kinetic"]).isdisjoint(
                {"blocked_by", "after", "related", "ships_with", "status", "slice"}))
            loaded = state.load(run)["tickets"][ids["b"]]
            self.assertEqual(loaded["blocked_by"], [{"id": ids["a"], "type": "snapshot"}])
            self.assertEqual(loaded["after"], [ids["a"]])
            self.assertEqual(loaded["related"], [ids["a"]])
            self.assertEqual(loaded["ships_with"], "release-1")
            self.assertEqual(loaded["slice"], ids["slice"])

    def test_edge_replacement_and_removal_update_native_links(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            state.set_ticket(run, "a", repo="api")
            state.set_ticket(run, "b", repo="web", blocked_by=[{"id": "a", "type": "snapshot"}],
                             after=["a"], ships_with="solo")
            ids = state.publish_tickets(run)
            state.set_ticket(run, ids["b"], blocked_by=[{"id": ids["a"], "type": "release"}],
                             after=[], ships_with=None)
            shown = orbit_json(board, "show", ids["b"])
            self.assertEqual([(link["type"], link["target"], link["attributes"])
                              for link in shown["links"]],
                             [("blocks", ids["a"], {"edge": "release"})])
            self.assertEqual(state.load(run)["tickets"][ids["b"]]["blocked_by"],
                             [{"id": ids["a"], "type": "release"}])

    def test_nonblocking_links_can_reference_later_ticket(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            state.set_ticket(run, "a", repo="api", after=["z"], related=["z"], ships_with="solo")
            state.set_ticket(run, "z", repo="web")
            ids = state.publish_tickets(run)
            loaded = state.load(run)["tickets"][ids["a"]]
            self.assertEqual((loaded["after"], loaded["related"], loaded["ships_with"]),
                             ([ids["z"]], [ids["z"]], "solo"))

    def test_agent_cannot_complete_before_review(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            state.set_ticket(run, "a", repo="api")
            ids = state.publish_tickets(run)
            before = orbit_json(board, "show", ids["a"])["status"]
            with self.assertRaises(orbit_store.OrbitStoreError) as caught:
                state.set_ticket(run, ids["a"], status="done")
            self.assertEqual(caught.exception.code, "REVIEW_REQUIRED")
            self.assertEqual(orbit_json(board, "show", ids["a"])["status"], before)
