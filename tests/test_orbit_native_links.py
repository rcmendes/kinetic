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

    def test_touch_paths_publish_update_and_roundtrip_as_native_property(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            state.set_ticket(run, "a", repo="api", touches=["src/auth", "tests/test_auth.py"])
            tid = state.publish_tickets(run)["a"]
            shown = orbit_json(board, "show", tid)
            self.assertEqual(shown["properties"]["touches"],
                             {"paths": ["api/src/auth", "api/tests/test_auth.py"]})
            self.assertNotIn("touches", shown["properties"]["kinetic"])
            self.assertEqual(state.load(run)["tickets"][tid]["touches"],
                             ["src/auth", "tests/test_auth.py"])
            state.set_ticket(run, tid, touches=["src/session.py"])
            self.assertEqual(orbit_json(board, "show", tid)["properties"]["touches"],
                             {"paths": ["api/src/session.py"]})
            self.assertEqual(state.load(run)["tickets"][tid]["touches"], ["src/session.py"])
            state.set_ticket(run, tid, touches=[])
            self.assertEqual(orbit_json(board, "show", tid)["properties"]["touches"], {"paths": []})


class TouchPathScopeTest(unittest.TestCase):
    """Orbit compares touches across the whole Board, so each path carries its repo."""

    def paths(self, repo, touches):
        ops = orbit_store.ops_for("t", None, {"repo": repo, "touches": touches, "status": "todo"},
                                  "run", {"run": "kinetic-run-<run id>", "kind": {"human": ""}})
        return next(op["value"]["paths"] for op in ops if op.get("owner") == "touches")

    def test_the_same_path_in_two_repos_does_not_collide(self):
        self.assertNotEqual(self.paths("api", ["src/auth.py"]), self.paths("web", ["src/auth.py"]))

    def test_leading_dot_slash_goes_and_dot_directories_stay(self):
        self.assertEqual(self.paths("api", ["./src/a.py", ".github/ci.yml", "/lib"]),
                         ["api/src/a.py", "api/.github/ci.yml", "api/lib"])

    def test_reading_strips_only_the_tickets_own_repo(self):
        task = {"properties": {"kinetic": {"repo": "api", "kind": "agent"},
                               "touches": {"paths": ["api/src/a.py", "web/b.py"]}}}
        self.assertEqual(orbit_store.to_ticket(task)["touches"], ["src/a.py", "web/b.py"])
