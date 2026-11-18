"""The checkpoint capability: the orchestrator records a ticket's handoff on its behalf."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from kinetic import orbit_store, state, ticket_store

ROOT = Path(__file__).resolve().parent.parent


class BareStore:
    def read(self, run_dir, st):
        return st.setdefault("tickets", {})

    def write(self, run_dir, st, before, after):
        return None


class CheckpointFallbackTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.run_dir = Path(tmp.name)
        state.init(self.run_dir, lane="feature", ref="x")
        state.set_ticket(self.run_dir, "01-api-a", repo="api")

    def test_local_store_declares_no_checkpoint(self):
        self.assertFalse(ticket_store.has(ticket_store.LocalTicketStore(), "checkpoint"))

    def test_record_without_the_capability_is_a_reported_no_op(self):
        with patch.object(ticket_store, "store_for", return_value=BareStore()):
            out = state.record_checkpoint(self.run_dir, "01-api-a", "tests green", "open PR")
        self.assertEqual(out, {"id": "01-api-a", "recorded": False})

    def test_last_checkpoint_without_the_capability_is_none(self):
        with patch.object(ticket_store, "store_for", return_value=BareStore()):
            self.assertIsNone(state.last_checkpoint(self.run_dir, "01-api-a"))

    def test_progress_is_required(self):
        with self.assertRaisesRegex(ValueError, "progress"):
            state.record_checkpoint(self.run_dir, "01-api-a", "  ")


@unittest.skipUnless(shutil.which("orbit"), "orbit CLI not installed")
class OrbitCheckpointTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.anchor = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="user.name",
                        GIT_CONFIG_VALUE_0="Test", GIT_CONFIG_KEY_1="user.email",
                        GIT_CONFIG_VALUE_1="test@example.com")
        self.orbit("init", "--agents", "none")
        self.orbit("setup", str(ROOT / "profiles" / "kinetic.toml"))
        self.tid = self.orbit("add", "Implement feature", "--source-key", "kinetic:cp-run:t",
                              "--label", "kinetic-run-cp-run")["id"]
        self.run = self.anchor / "run"
        self.st = {"run_id": "cp-run", "run_dir": str(self.run)}
        self.store = orbit_store.make({"anchor": str(self.anchor)})

    def orbit(self, *args):
        p = subprocess.run(["orbit", "--dir", str(self.anchor), *args, "--json"],
                           cwd=self.anchor, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)["data"]

    def claim(self):
        self.orbit("claim", self.tid, "--by", orbit_store.holder("cp-run", self.tid, "implementer"))

    def test_store_declares_checkpoint(self):
        self.assertTrue(ticket_store.has(self.store, "checkpoint"))

    def test_checkpoint_round_trips_as_the_implementer(self):
        self.claim()
        self.store.checkpoint(self.run, self.st, self.tid, "tests green", "open PR", None)
        got = self.store.last_checkpoint(self.run, self.st, self.tid)
        self.assertEqual((got["progress"], got["next"]), ("tests green", "open PR"))
        self.assertEqual(got["by"], orbit_store.holder("cp-run", self.tid, "implementer"))
        self.assertNotIn("blocker", got)

    def test_later_checkpoint_keeps_omitted_fields(self):
        self.claim()
        self.store.checkpoint(self.run, self.st, self.tid, "tests green", "open PR", None)
        self.store.checkpoint(self.run, self.st, self.tid, "PR open", None, "waiting on review")
        got = self.store.last_checkpoint(self.run, self.st, self.tid)
        self.assertEqual((got["progress"], got["next"], got["blocker"]),
                         ("PR open", "open PR", "waiting on review"))

    def test_no_checkpoint_yet_reads_as_none(self):
        self.claim()
        self.assertIsNone(self.store.last_checkpoint(self.run, self.st, self.tid))

    def test_checkpoint_needs_the_implementer_to_hold_the_ticket(self):
        with self.assertRaisesRegex(ValueError, "not held by its implementer"):
            self.store.checkpoint(self.run, self.st, self.tid, "x", None, None)
