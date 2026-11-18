from __future__ import annotations

import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from unittest.mock import patch

from kinetic import state, status, ticket_store

ROOT = Path(__file__).resolve().parent.parent


def t(repo, st="todo", blocked_by=()):
    return {"repo": repo, "status": st, "blocked_by": list(blocked_by),
            "snapshot_published": False}


def S(tickets, gates=None, phase="build"):
    return {"lane": "feature", "phase": phase, "tickets": tickets, "gates": gates or {}}


class StatusTest(unittest.TestCase):
    def test_render_contains_ticket_rows(self):
        out = status.render(S({
            "A-1": t("api", "done"),
            "B-2": t("web", "todo", [{"id": "A-1", "type": "merge"}, {"id": "C", "type": "snapshot"}]),
            "C": t("lib", "running"),
        }))
        self.assertIn("feature", out.splitlines()[0])
        self.assertIn("build", out.splitlines()[0])
        self.assertIn("| id | kind | repo | status | priority | blocked by |", out)
        self.assertLess(out.index("Waiting on you:"), out.index("| id | kind"))
        self.assertIn("| A-1 | agent | api | done | P0 | — |", out)
        self.assertIn("| B-2 | agent | web | todo | P1 | A-1 (merge), C (snapshot) |", out)

    def test_stuck_ticket_is_reported(self):
        stuck = dict(t("api", "running"), liveness="stuck")
        alive = dict(t("web", "running"), liveness="alive")
        out = status.render(S({"A": stuck, "B": alive}))
        self.assertIn("implementer may be stuck: A", out)
        self.assertNotIn("stuck: B", out)

    def test_local_status_unchanged(self):
        out = status.render(S({"A": t("api", "running")}))
        self.assertNotIn("stuck", out)

    def test_render_ready_line(self):
        out = status.render(S({"A": t("api")}))
        self.assertIn("Ready: A", out)
        self.assertIn("Ready: none", status.render(S({"A": t("api", "done")})))

    def test_render_shows_waiting_on_you(self):
        h = {"repo": None, "status": "todo", "blocked_by": [], "snapshot_published": False,
             "kind": "human"}
        out = status.render(S({"H": h}))
        self.assertIn("Waiting on you: H", out)
        self.assertIn("| H | human | — | todo | P0 | — |", out)
        self.assertIn("Waiting on you: none", status.render(S({"A": t("api")})))

    def test_render_gates(self):
        out = status.render(S({}, {"spec": {"approved": True}, "ship": {"approved": False}}))
        self.assertIn("spec: yes", out)
        self.assertIn("ship: no", out)

    def test_cli_prints_markdown(self):
        with tempfile.TemporaryDirectory() as d:
            state.init(d, "quick", "X-1")
            state.set_ticket(d, "X-1", repo="api")
            r = subprocess.run([sys.executable, "-m", "kinetic.status", "--run", d],
                               capture_output=True, text=True,
                               env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")})
            self.assertEqual(r.returncode, 0, r.stderr)
            self.assertIn("| X-1 | agent | api | todo | P0 | — |", r.stdout)


if __name__ == "__main__":
    unittest.main()


class QueryStore:
    """A store that declares query and must not be asked to read every ticket."""

    def __init__(self):
        self.rollups = 0

    def capabilities(self):
        return frozenset({"query"})

    def read(self, run_dir, st):
        raise AssertionError("summary must not read every ticket")

    def rollup(self, run_dir, st):
        self.rollups += 1
        return {"done": 4, "blocked": 1, "open": 2, "cancelled": 0, "total": 7}


class CoreOnlyStore:
    def read(self, run_dir, st):
        return {"A": t("api", "done"), "B": t("api", "todo", [{"id": "A", "type": "merge"}]),
                "C": t("api", "todo", [{"id": "X", "type": "merge"}])}


class SummaryTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        state.init(self.run_dir, lane="feature", ref="x")
        state.record_gate(self.run_dir, "spec", True)

    def test_a_store_with_query_answers_from_one_rollup(self):
        store = QueryStore()
        with patch.object(ticket_store, "for_state", return_value=store):
            out = status.summary(self.run_dir)
        self.assertEqual(store.rollups, 1)
        self.assertIn("Progress: 4 of 7 done, 1 blocked, 2 open", out)
        self.assertIn("- spec: yes", out)

    def test_a_store_without_query_is_counted_from_its_tickets(self):
        with patch.object(ticket_store, "for_state", return_value=CoreOnlyStore()):
            out = status.summary(self.run_dir)
        self.assertIn("Progress: 1 of 3 done, 1 blocked, 1 open", out)

    def test_the_local_store_gives_the_same_counts_as_the_table(self):
        state.set_ticket(self.run_dir, "a", repo="api", status="running")
        out = status.summary(self.run_dir)
        self.assertIn("Progress: 0 of 1 done, 0 blocked, 1 open", out)

    def test_summary_flag_prints_the_summary(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts")}
        done = subprocess.run([sys.executable, "-m", "kinetic.status", "--run", str(self.run_dir),
                               "--summary"], capture_output=True, text=True, env=env)
        self.assertEqual(done.returncode, 0, done.stderr)
        self.assertIn("Progress:", done.stdout)


class PriorityColumnTest(unittest.TestCase):
    def test_status_shows_the_rank_of_each_ticket(self):
        out = status.render(S({"A": t("api"), "B": t("api", "todo", [{"id": "A", "type": "merge"}])}))
        self.assertIn("| A | agent | api | todo | P0 | — |", out)
        self.assertIn("| B | agent | api | todo | P1 | A (merge) |", out)
