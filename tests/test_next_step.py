from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kinetic import next_step, project, state

ROOT = Path(__file__).resolve().parent.parent


def t(repo="r", st="todo"):
    return {"repo": repo, "status": st, "blocked_by": [], "snapshot_published": False}


class SuggestTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.run_dir = self._tmp.name
        (Path(self.run_dir) / ".kinetic" / "reports").mkdir(parents=True)
        self.addCleanup(self._tmp.cleanup)

    def S(self, tickets=None, phase="build", gates=None):
        return {"run_dir": self.run_dir, "phase": phase, "tickets": tickets or {},
                "gates": gates or {}}

    def test_r1_no_state(self):
        r = next_step.suggest(None)
        self.assertEqual(r["command"], "/kinetic:run <ref>")
        self.assertIn("no active run", r["reason"])

    def test_r2_needs_decision(self):
        r = next_step.suggest(self.S({"A": t("r1", "needs_decision")}))
        self.assertEqual(r["command"], "/kinetic:run")
        self.assertIn("ticket A needs a decision", r["reason"])

    def test_r3_stale_running(self):
        r = next_step.suggest(self.S({"A": t("r1", "running")}))
        self.assertEqual(r["command"], "/kinetic:run")
        self.assertIn("recover A", r["reason"])

    def test_r3_running_with_report_is_not_stale(self):
        (Path(self.run_dir) / ".kinetic" / "reports" / "A.md").write_text("x")
        r = next_step.suggest(self.S({"A": t("r1", "running")}))
        self.assertNotIn("recover", r["reason"])

    def test_r4_build_frontier(self):
        r = next_step.suggest(self.S({"A": t("r1"), "B": t("r2")}))
        self.assertEqual(r["command"], "/kinetic:run")
        self.assertIn("2 tickets ready", r["reason"])

    def test_r5_build_all_done(self):
        r = next_step.suggest(self.S({"A": t("r1", "done"), "B": t("r2", "merged")}))
        self.assertEqual(r["command"], "/kinetic:run")
        self.assertIn("review and ship", r["reason"])

    def test_r6_ship_done(self):
        r = next_step.suggest(self.S({"A": t("r1", "done")}, phase="ship",
                                     gates={"ship": {"approved": True}}))
        self.assertEqual(r["command"], "/kinetic:run")
        self.assertIn("retro", r["reason"])

    def test_r7_fallback_status(self):
        r = next_step.suggest(self.S({"A": t("r1", "done")}, phase="spec"))
        self.assertEqual(r["command"], "/kinetic:status")

    def test_rule_order_earlier_wins(self):
        # needs_decision (R2) and stale running (R3) both match
        r = next_step.suggest(self.S({"A": t("r1", "needs_decision"), "B": t("r2", "running")}))
        self.assertIn("needs a decision", r["reason"])
        # stale running (R3) and a ready ticket (R4) both match
        r = next_step.suggest(self.S({"A": t("r1", "running"), "B": t("r2")}))
        self.assertIn("recover", r["reason"])


class CliTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.base = Path(os.path.realpath(self._tmp.name))
        self.proj = project.create(self.base, "proj")

    def _run(self, args, cwd):
        return subprocess.run([sys.executable, "-m", "kinetic.next_step"] + args,
                              capture_output=True, text=True, cwd=str(cwd),
                              env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")})

    def out(self, args, cwd):
        r = self._run(args, cwd)
        self.assertEqual(r.returncode, 0, r.stderr)
        return json.loads(r.stdout)

    def test_cli_run_flag(self):
        run = project.run_dir(self.proj, "r1")
        state.init(run, "quick", "X-1")
        self.assertEqual(self.out(["--run", str(run)], self.base)["command"], "/kinetic:status")

    def test_no_run_outside_project_or_in_empty_project(self):
        self.assertEqual(self.out([], self.base)["command"], "/kinetic:run <ref>")
        self.assertEqual(self.out([], self.proj)["command"], "/kinetic:run <ref>")

    def test_run_from_cwd(self):
        run = project.run_dir(self.proj, "r1")
        state.init(run, "quick", "X-1")
        state.init(project.run_dir(self.proj, "r2"), "quick", "X-2")
        (run / "worktrees" / "api").mkdir(parents=True)
        self.assertEqual(self.out([], run / "worktrees" / "api")["command"], "/kinetic:status")

    def test_one_open_run_from_project_root(self):
        state.init(project.run_dir(self.proj, "r1"), "quick", "X-1")
        self.assertEqual(self.out([], self.proj)["command"], "/kinetic:status")

    def test_next_step_two_open_runs(self):
        state.init(project.run_dir(self.proj, "r1"), "quick", "X-1")
        state.init(project.run_dir(self.proj, "r2"), "quick", "X-2")
        self.assertEqual(self.out([], self.proj),
                         {"command": "/kinetic:run", "reason": "2 open runs; open one from its folder"})


if __name__ == "__main__":
    unittest.main()
