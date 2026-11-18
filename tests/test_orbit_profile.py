from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


@unittest.skipUnless(shutil.which("orbit"), "orbit CLI not installed")
class OrbitProfileTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.anchor = Path(self.tmp.name) / "project"
        self.anchor.mkdir()
        self.env = dict(os.environ, HOME=self.tmp.name,
                        GIT_CONFIG_COUNT="2", GIT_CONFIG_KEY_0="user.name",
                        GIT_CONFIG_VALUE_0="Test", GIT_CONFIG_KEY_1="user.email",
                        GIT_CONFIG_VALUE_1="test@example.com")
        self.orbit("init", "--agents", "none")

    def orbit(self, *args):
        p = subprocess.run(["orbit", "--dir", str(self.anchor), *args, "--json"],
                           cwd=self.anchor, env=self.env, capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        return json.loads(p.stdout)["data"]

    def test_setup_installs_native_statuses_and_review_policy(self):
        profile = ROOT / "profiles" / "kinetic.toml"
        self.orbit("setup", str(profile))
        self.orbit("setup", str(profile), "--check")
        caps = self.orbit("capabilities")
        statuses = {s["name"]: s["category"] for s in caps["workflow"]["statuses"]}
        self.assertEqual(statuses, {
            "todo": "todo", "running": "active", "needs_decision": "active",
            "blocked": "active", "in_review": "active", "done": "done",
            "done_with_concerns": "done", "merged": "done", "released": "done",
            "cancelled": "canceled",
        })
        tid = self.orbit("add", "Implement feature")["id"]
        self.orbit("claim", tid, "--by", "author")
        self.orbit("submit", tid, "--by", "author", "--command", "python -m unittest", "--exit-code", "0")
        p = subprocess.run(["orbit", "--dir", str(self.anchor), "review", tid,
                            "--verdict", "approve", "--by", "author", "--json"],
                           cwd=self.anchor, env=self.env, capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(json.loads(p.stdout)["error"]["code"], "POLICY_VIOLATION")
        self.orbit("review", tid, "--verdict", "approve", "--by", "reviewer")
        self.assertEqual(self.orbit("show", tid)["status"], "done")
        self.orbit("doctor")

    def test_install_makes_named_profile_available_and_uninstall_removes_it(self):
        p = subprocess.run([sys.executable, "-m", "kinetic", "install", "--harness", "claude"],
                           cwd=ROOT, env=dict(self.env, PYTHONPATH=str(ROOT / "scripts")),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.orbit("setup", "kinetic")
        self.orbit("setup", "kinetic", "--check")
        profile = Path(self.tmp.name) / ".config" / "orbit" / "profiles" / "kinetic.toml"
        self.assertTrue(profile.is_file())
        p = subprocess.run([sys.executable, "-m", "kinetic", "install", "--harness", "claude", "--uninstall"],
                           cwd=ROOT, env=dict(self.env, PYTHONPATH=str(ROOT / "scripts")),
                           capture_output=True, text=True)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        self.assertFalse(profile.exists())

    def test_install_preserves_an_existing_user_profile(self):
        profile = Path(self.tmp.name) / ".config" / "orbit" / "profiles" / "kinetic.toml"
        profile.parent.mkdir(parents=True)
        profile.write_text('name = "custom"\n')
        p = subprocess.run([sys.executable, "-m", "kinetic", "install", "--harness", "claude"],
                           cwd=ROOT, env=dict(self.env, PYTHONPATH=str(ROOT / "scripts")),
                           capture_output=True, text=True)
        self.assertNotEqual(p.returncode, 0)
        self.assertEqual(profile.read_text(), 'name = "custom"\n')
        self.assertFalse((Path(self.tmp.name) / ".local" / "bin" / "kinetic").exists())
