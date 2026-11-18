from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from kinetic import orbit_store

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("orbit"), "orbit CLI not installed")
class OrbitReviewTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.anchor = Path(self.tmp.name)
        self.env = dict(os.environ, GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="user.name",
                        GIT_CONFIG_VALUE_0="Test", GIT_CONFIG_KEY_1="user.email",
                        GIT_CONFIG_VALUE_1="test@example.com")
        self.orbit("init", "--agents", "none")
        self.orbit("setup", str(ROOT / "profiles" / "kinetic.toml"))
        self.tid = self.orbit("add", "Implement feature")["id"]
        self.run = self.anchor / "run"
        self.st = {"run_id": "review-run", "run_dir": str(self.run)}
        self.store = orbit_store.make({"anchor": str(self.anchor)})
        self.evidence = {"command": "python -m unittest", "exit_code": 0,
                         "log_path": "/tmp/verify.log", "at": "2026-11-18T12:00:00Z"}
        self.orbit("claim", self.tid, "--by", orbit_store.holder("review-run", self.tid, "implementer"))

    def orbit(self, *args):
        p = subprocess.run(["orbit", "--dir", str(self.anchor), *args, "--json"],
                           cwd=self.anchor, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)["data"]

    def test_both_review_axes_required_before_native_approval(self):
        self.store.record_verify(self.run, self.st, self.tid, self.evidence)
        self.assertEqual(self.orbit("show", self.tid)["properties"]["kinetic-verify"]["exit_code"], 0)
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence])
        evidence = self.orbit("show", self.tid)["properties"]["evidence"]["items"][-1]
        self.assertEqual((evidence["commands"], evidence["exit_code"], evidence["files"]),
                         (["python -m unittest"], 0, ["/tmp/verify.log"]))
        self.store.record_review(self.run, self.st, self.tid, "spec", "approve")
        self.assertEqual(self.orbit("show", self.tid)["status"], "in_review")
        self.store.record_review(self.run, self.st, self.tid, "standards", "approve")
        issue = self.orbit("show", self.tid)
        self.assertEqual(issue["status"], "done")
        review = issue["properties"]["orbit"]["review"]
        self.assertNotEqual(review["submitted_by"], review["reviewed_by"])
        self.assertEqual(review["verdict"], "approve")

    def test_failed_review_records_native_attempt_and_new_round_resets_axes(self):
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence])
        self.store.record_review(self.run, self.st, self.tid, "spec", "approve")
        self.store.record_review(self.run, self.st, self.tid, "standards", "request-changes", "Missing error handling")
        issue = self.orbit("show", self.tid)
        self.assertEqual(issue["status"], "running")
        self.assertEqual(issue["properties"]["orbit"]["attempts"]["kinetic-reviewer-changes"], 1)
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence])
        self.store.record_review(self.run, self.st, self.tid, "standards", "approve")
        issue = self.orbit("show", self.tid)
        self.assertEqual(issue["status"], "in_review")
        self.assertEqual(issue["properties"]["orbit"]["review"]["rounds"], 2)

    def test_failed_verify_cannot_be_submitted(self):
        with self.assertRaisesRegex(ValueError, "passing verify"):
            self.store.submit_review(self.run, self.st, self.tid, [dict(self.evidence, exit_code=1)])
        self.assertEqual(self.orbit("show", self.tid)["status"], "running")

    def test_integration_review_uses_distinct_run_identity(self):
        self.orbit("release", self.tid, "--by", orbit_store.holder("review-run", self.tid, "implementer"))
        self.orbit("claim", self.tid, "--by", orbit_store.holder("review-run"))
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence], scope="integration")
        self.store.record_review(self.run, self.st, self.tid, "integration", "approve")
        record = self.orbit("show", self.tid)["properties"]["orbit"]["review"]
        self.assertEqual((record["submitted_by"], record["reviewed_by"]),
                         ("kinetic-review-run-orchestrator", "kinetic-review-run-integration-reviewer"))
