from __future__ import annotations

import json
import fcntl
import os
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from kinetic import orbit_store, state
from kinetic.tracker import adapter_defaults

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
        self.tid = self.orbit("add", "Implement feature", "--source-key", "kinetic:review-run:local-ticket", "--label", "kinetic-run-review-run")["id"]
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
        verdicts = issue["properties"]["kinetic-reviews"]["verdicts"]
        self.assertNotEqual(verdicts["spec"]["by"], verdicts["standards"]["by"])
        self.assertEqual(verdicts["spec"]["by"], orbit_store.holder("review-run", self.tid, "spec-reviewer"))
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
        child = self.orbit("add", "Child work", "--parent", self.tid)["id"]
        self.orbit("done", child)
        self.orbit("release", self.tid, "--by", orbit_store.holder("review-run", self.tid, "implementer"))
        self.orbit("claim", self.tid, "--by", orbit_store.holder("review-run"))
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence], scope="integration")
        self.store.record_review(self.run, self.st, self.tid, "integration", "approve")
        record = self.orbit("show", self.tid)["properties"]["orbit"]["review"]
        self.assertEqual((record["submitted_by"], record["reviewed_by"]),
                         ("kinetic-review-run-orchestrator", "kinetic-review-run-integration-reviewer"))
        self.orbit("reopen", child)
        self.assertEqual(self.orbit("show", self.tid)["status"], "todo")
        self.orbit("done", child)
        self.store.submit_review(self.run, self.st, self.tid, [self.evidence], scope="integration")
        self.store.record_review(self.run, self.st, self.tid, "integration", "approve")
        self.assertEqual(self.orbit("show", self.tid)["properties"]["orbit"]["review"]["rounds"], 2)

    def test_committed_submit_recovers_after_crash_before_journal_cleanup(self):
        original = Path.unlink
        def crash(path, *args, **kwargs):
            if path.name == "orbit-write.json":
                raise OSError("client crashed after commit")
            return original(path, *args, **kwargs)
        with patch.object(Path, "unlink", crash):
            with self.assertRaisesRegex(OSError, "client crashed"):
                self.store.submit_review(self.run, self.st, self.tid, [self.evidence])
        restored = orbit_store.make(dict(self.store.config))
        restored.submit_review(self.run, self.st, self.tid, [self.evidence])
        self.assertFalse((self.run / ".kinetic" / "orbit-write.json").exists())
        issue = self.orbit("show", self.tid)
        self.assertEqual(issue["properties"]["orbit"]["review"]["rounds"], 1)
        self.assertEqual(len(issue["properties"]["evidence"]["items"]), 1)
        restored.record_review(self.run, self.st, self.tid, "spec", "approve")
        restored.record_review(self.run, self.st, self.tid, "standards", "approve")
        self.assertEqual(self.orbit("show", self.tid)["status"], "done")

    def test_verify_provisional_id_can_submit_its_published_ticket(self):
        self.orbit("property", "set", self.tid, "kinetic", "--value", json.dumps({"repo": "api", "kind": "agent"}))
        raw = state.init(self.run, "quick", "request")
        raw["run_id"] = "review-run"
        raw["ticket_store"] = {"kind": "orbit", "config": {
            "anchor": str(self.anchor), "published": True,
            "names": adapter_defaults(ROOT / "trackers" / "orbit.md")}}
        state.save(self.run, raw)
        state.record_verify(self.run, "local-ticket", 0, "/tmp/verify.log", command="python -m unittest")
        state.submit_review(self.run, "local-ticket")
        raw = state.load(self.run)
        self.assertIn(self.tid, raw["verify"])
        self.assertEqual(raw["tickets"][self.tid]["status"], "in_review")

    def test_verify_store_failure_preserves_record_and_retries_without_lock(self):
        self.orbit("property", "set", self.tid, "kinetic", "--value", json.dumps({"repo": "api", "kind": "agent"}))
        raw = state.init(self.run, "quick", "request")
        raw["run_id"] = "review-run"
        raw["ticket_store"] = {"kind": "orbit", "config": {
            "anchor": str(self.anchor), "published": True,
            "names": adapter_defaults(ROOT / "trackers" / "orbit.md")}}
        state.save(self.run, raw)
        original = subprocess.run
        fail = True
        attempts = []

        def command(args, **kwargs):
            nonlocal fail
            with open(self.run / ".kinetic" / "state.lock", "a") as lock:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if args[3] == "batch":
                saved = state.load(self.run, tickets=False)["verify"][self.tid]
                self.assertEqual(saved["exit_code"], 0)
                attempts.append(args[args.index("--idempotency-key") + 1])
                if fail:
                    fail = False
                    raise OSError("connection interrupted")
            return original(args, **kwargs)

        with patch("subprocess.run", side_effect=command):
            with self.assertRaisesRegex(ValueError, "verify recorded locally.*retry record-verify"):
                state.record_verify(self.run, self.tid, 0, "/tmp/verify.log", at="2026-11-18T12:00:00Z")
            self.assertEqual(state.load(self.run, tickets=False)["verify"][self.tid]["exit_code"], 0)
            state.record_verify(self.run, self.tid, 0, "/tmp/verify.log", at="2026-11-18T12:00:00Z")
        self.assertEqual(attempts[0], attempts[1])
        self.assertEqual(self.orbit("show", self.tid)["properties"]["kinetic-verify"]["exit_code"], 0)

    def test_initial_rejection_and_three_fix_rounds_can_escalate_then_proceed(self):
        self.orbit("property", "set", self.tid, "kinetic", "--value", json.dumps({"repo": "api", "kind": "agent"}))
        raw = state.init(self.run, "quick", "request")
        raw["run_id"] = "review-run"
        raw["ticket_store"] = {"kind": "orbit", "config": {
            "anchor": str(self.anchor), "published": True,
            "names": adapter_defaults(ROOT / "trackers" / "orbit.md")}}
        state.save(self.run, raw)
        state.record_verify(self.run, self.tid, 0, "/tmp/verify.log", command="python -m unittest")
        state.submit_review(self.run, self.tid)
        state.record_review(self.run, self.tid, "spec", "request-changes", "Initial finding")
        for round_number in range(1, 4):
            state.submit_review(self.run, self.tid, scope="fix")
            state.record_review(self.run, self.tid, "fix", "request-changes", f"Round {round_number} finding")
        issue = self.orbit("show", self.tid)
        self.assertEqual(issue["properties"]["orbit"]["attempts"]["kinetic-reviewer-changes"], 4)
        state.set_ticket(self.run, self.tid, status="needs_decision")
        self.assertEqual(self.orbit("show", self.tid)["status"], "needs_decision")
        state.add_ruling(self.run, "Remaining finding", "Accepted as nonblocking", "Documented concern")
        state.set_ticket(self.run, self.tid, status="running")
        state.submit_review(self.run, self.tid, scope="fix")
        state.record_review(self.run, self.tid, "fix", "approve", "Nonblocking finding adjudicated")
        state.set_ticket(self.run, self.tid, status="done_with_concerns")
        state.set_ticket(self.run, self.tid, status="merged")
        self.assertEqual(self.orbit("show", self.tid)["status"], "merged")

    def test_unavailable_store_keeps_verify_and_alias_retry_replaces_local_key(self):
        self.orbit("property", "set", self.tid, "kinetic", "--value", json.dumps({"repo": "api", "kind": "agent"}))
        raw = state.init(self.run, "quick", "request")
        raw["run_id"] = "review-run"
        raw["ticket_store"] = {"kind": "orbit", "config": {
            "anchor": str(self.anchor), "published": True,
            "names": adapter_defaults(ROOT / "trackers" / "orbit.md")}}
        state.save(self.run, raw)
        with patch("subprocess.run", side_effect=OSError("store unavailable")):
            with self.assertRaisesRegex(ValueError, "verify recorded locally"):
                state.record_verify(self.run, "local-ticket", 0, "/tmp/verify.log")
        self.assertIn("local-ticket", state.load(self.run, tickets=False)["verify"])
        state.record_verify(self.run, "local-ticket", 0, "/tmp/verify.log")
        self.assertEqual(set(state.load(self.run, tickets=False)["verify"]), {self.tid})
