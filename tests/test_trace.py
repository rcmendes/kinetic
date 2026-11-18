from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kinetic import state, trace
from tests.test_orbit_store import (SKIP_REASON, deep_board, make_orbit_run,
                                    orbit_available, orbit_json, stage_two)

SCRIPTS = str(Path(__file__).resolve().parents[1] / "scripts")


def mk(tmp, spec="spec.md"):
    run = Path(tmp) / "run"
    state.init(run, "feature", "ABC-1")
    if spec:
        state.set_spec(run, spec)
    state.set_slices(run, ["01-core", "02-ui"])
    state.set_ticket(run, "01-api-a", repo="api", slice="01-core")
    state.set_ticket(run, "02-web-b", repo="web", slice="01-core")
    state.set_ticket(run, "03-web-c", repo="web", slice="02-ui")
    state.set_ticket(run, "04-loose", repo="web")
    return run


class TraceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run = mk(self._tmp.name)

    def tree(self, focus=None):
        return trace.build(state.load(self.run), focus)

    def test_full_tree(self):
        t = self.tree()
        self.assertEqual(t["spec"], "spec.md")
        self.assertEqual(t["slices"], [
            {"id": "01-core", "tickets": ["01-api-a", "02-web-b"]},
            {"id": "02-ui", "tickets": ["03-web-c"]}])
        self.assertEqual(t["unsliced"], ["04-loose"])

    def test_focus_ticket_keeps_its_ancestors_only(self):
        t = self.tree("02-web-b")
        self.assertEqual(t["spec"], "spec.md")
        self.assertEqual(t["slices"], [{"id": "01-core", "tickets": ["02-web-b"]}])
        self.assertEqual(t["unsliced"], [])

    def test_focus_slice_keeps_all_its_tickets(self):
        self.assertEqual(self.tree("01-core")["slices"],
                         [{"id": "01-core", "tickets": ["01-api-a", "02-web-b"]}])

    def test_focus_spec_is_the_full_tree(self):
        self.assertEqual(self.tree("spec.md"), self.tree())

    def test_focus_unsliced_ticket(self):
        t = self.tree("04-loose")
        self.assertEqual((t["slices"], t["unsliced"]), ([], ["04-loose"]))

    def test_unknown_focus_raises(self):
        with self.assertRaises(ValueError):
            self.tree("nope")

    def test_ticket_slice_missing_from_slices_list_still_shows(self):
        state.set_ticket(self.run, "05-x", repo="web", slice="03-ghost")
        ids = [s["id"] for s in self.tree()["slices"]]
        self.assertEqual(ids, ["01-core", "02-ui", "03-ghost"])

    def test_no_spec_recorded(self):
        run = mk(self._tmp.name + "/b", spec=None)
        self.assertIsNone(trace.build(state.load(run))["spec"])

    def test_render_text(self):
        out = trace.render(self.tree())
        self.assertEqual(out.splitlines(), [
            "spec: spec.md",
            "├── slice 01-core",
            "│   ├── 01-api-a",
            "│   └── 02-web-b",
            "├── slice 02-ui",
            "│   └── 03-web-c",
            "└── no slice",
            "    └── 04-loose"])

    def test_cli_json_and_focus(self):
        def run(*a):
            return subprocess.run([sys.executable, "-m", "kinetic", "trace", "--run", str(self.run), *a],
                                  capture_output=True, text=True,
                                  env=dict(os.environ, PYTHONPATH="scripts"))
        r = run("--json", "--id", "03-web-c")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)["slices"], [{"id": "02-ui", "tickets": ["03-web-c"]}])
        self.assertNotEqual(run("--id", "nope").returncode, 0)


@unittest.skipUnless(orbit_available(), SKIP_REASON)
class TraceOrbitRunTest(unittest.TestCase):
    """`kinetic trace` on an Orbit run prints the Board's Task IDs."""

    def test_trace_prints_the_spec_slice_and_ticket_task_ids(self):
        with tempfile.TemporaryDirectory() as tmp:
            run, board = make_orbit_run(Path(tmp))
            deep_board(board)
            (run / "spec.md").write_text("# Spec\n\nBuild the exporter.\n")
            spec_id = state.publish_spec(run, "Export CSV")["ref"]
            stage_two(run)
            mapping = state.publish_tickets(run)
            ids = [spec_id, mapping["01-core"], mapping["01-api-a"], mapping["02-web-b"]]
            out = subprocess.run([sys.executable, "-m", "kinetic", "trace", "--run", str(run)],
                                 capture_output=True, text=True,
                                 env=dict(os.environ, PYTHONPATH=SCRIPTS))
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertEqual(out.stdout.splitlines()[0], f"spec: {spec_id}")
            for tid in ids[1:]:
                self.assertIn(tid, out.stdout)
            # orbit tree on the spec Task shows the same three levels.
            tree = orbit_json(board, "tree", spec_id)["issues"]
            self.assertEqual({t["id"] for t in tree}, set(ids))


if __name__ == "__main__":
    unittest.main()
