"""kinetic update: a plan the user can read, and steps that each change one thing."""
from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kinetic import update

GIT_ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}


def git(cwd, *args):
    done = subprocess.run(["git", "-C", str(cwd), *args], capture_output=True, text=True,
                          env={**os.environ, **GIT_ENV})
    assert done.returncode == 0, done.stderr
    return done.stdout.strip()


class UpdateTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)
        self.home = self.tmp / "home"
        (self.home / ".config" / "kinetic").mkdir(parents=True)
        self.settings = self.home / ".config" / "kinetic" / "settings.json"
        origin = self.tmp / "origin.git"
        subprocess.run(["git", "init", "-q", "--bare", "-b", "main", str(origin)], check=True)
        seed = self.tmp / "seed"
        subprocess.run(["git", "clone", "-q", str(origin), str(seed)], check=True, capture_output=True)
        self.publish(seed, "0.7.0")
        self.seed, self.origin = seed, origin
        self.clone = self.tmp / "clone"
        subprocess.run(["git", "clone", "-q", str(origin), str(self.clone)], check=True, capture_output=True)
        self.settings.write_text(json.dumps({"harness": "claude", "clone": str(self.clone)}))

    def publish(self, seed, version):
        """Commit a release in the seed checkout and send it to the test's own origin."""
        (seed / ".claude-plugin").mkdir(exist_ok=True)
        (seed / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": version}))
        git(seed, "add", "-A")
        git(seed, "commit", "-q", "-m", f"release {version}")
        git(seed, "push", "-q", "origin", "HEAD:refs/heads/main")

    def manifest(self, hid):
        (self.home / ".config" / "kinetic" / f"install-manifest-{hid}.json").write_text("{}")

    def plan(self, **kw):
        kw.setdefault("repo", "acme/kinetic")
        kw.setdefault("gh", lambda repo: "v0.8.0")
        kw.setdefault("notes", lambda repo, tag: "What users must do: nothing.")
        kw.setdefault("requirements", lambda: [])
        kw.setdefault("open_runs", lambda: [])
        return update.plan(self.clone, self.home, self.settings, **kw)

    # -- the plan ---------------------------------------------------------------------

    def test_the_plan_names_the_versions_and_whether_the_checkout_is_behind(self):
        self.publish(self.seed, "0.8.0")
        p = self.plan()
        self.assertEqual((p["current"], p["latest"], p["update_available"]), ("0.7.0", "0.8.0", True))
        self.assertEqual(p["checkout"], {"branch": "main", "clean": True, "behind": 1})
        self.assertIn("What users must do", p["notes"])

    def test_nothing_to_do_when_on_the_latest(self):
        p = self.plan(gh=lambda repo: "v0.7.0")
        self.assertFalse(p["update_available"])
        self.assertNotIn("notes", p)

    def test_an_older_claude_plugin_is_an_update_even_when_the_checkout_is_current(self):
        record = self.home / ".claude" / "plugins" / "installed_plugins.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"plugins": {"kinetic@kinetic": [{"version": "0.6.0"}]}}))
        p = self.plan(gh=lambda repo: "v0.7.0")
        self.assertEqual((p["current"], p["plugin"], p["update_available"]), ("0.7.0", "0.6.0", True))
        self.assertIn("What users must do", p["notes"])

    def test_the_plan_lists_each_step_and_whether_it_can_run(self):
        self.publish(self.seed, "0.8.0")
        self.manifest("claude")
        steps = {s["step"]: s for s in self.plan(has_claude=lambda: True)["steps"]}
        self.assertTrue(steps["pull"]["can"])
        self.assertTrue(steps["install"]["can"])
        self.assertTrue(steps["marketplace"]["can"])

    def test_the_marketplace_step_needs_the_claude_install_and_command(self):
        self.manifest("codex")
        steps = {s["step"]: s for s in self.plan(has_claude=lambda: True)["steps"]}
        self.assertFalse(steps["marketplace"]["can"])
        self.manifest("claude")
        steps = {s["step"]: s for s in self.plan(has_claude=lambda: False)["steps"]}
        self.assertFalse(steps["marketplace"]["can"])

    def test_open_runs_and_a_tool_below_its_minimum_are_reported(self):
        need = {"name": "tool", "installed": "0.10.0", "minimum": "0.11.0", "ok": False}
        p = self.plan(open_runs=lambda: ["run-1"], requirements=lambda: [need])
        self.assertEqual(p["open_runs"], ["run-1"])
        self.assertEqual(p["requirements"], [need])

    def test_a_store_that_names_a_required_tool_is_collected(self):
        from kinetic import orbit_store
        found = update._requirements()
        self.assertEqual([r["name"] for r in found if r["name"] == "orbit"], ["orbit"])
        self.assertEqual(orbit_store.tool_requirement(lambda: None)["installed"], None)
        self.assertTrue(orbit_store.tool_requirement(lambda: None)["ok"])

    def test_a_requirement_is_ok_only_at_or_above_its_minimum(self):
        from kinetic import orbit_store
        low = orbit_store.tool_requirement(lambda: "0.1.0")
        self.assertFalse(low["ok"])
        self.assertTrue(orbit_store.tool_requirement(lambda: orbit_store.MIN_ORBIT)["ok"])

    # -- pull ---------------------------------------------------------------------------

    def test_pull_fast_forwards_a_clean_checkout_on_main(self):
        self.publish(self.seed, "0.8.0")
        out = update.apply("pull", self.clone, self.home, self.settings)
        self.assertTrue(out["ok"])
        self.assertEqual((out["from"], out["to"]), ("0.7.0", "0.8.0"))
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), git(self.seed, "rev-parse", "HEAD"))

    def test_pull_refuses_a_checkout_with_local_changes(self):
        self.publish(self.seed, "0.8.0")
        (self.clone / ".claude-plugin" / "plugin.json").write_text("{}")
        with self.assertRaisesRegex(update.UpdateRefused, "local changes"):
            update.apply("pull", self.clone, self.home, self.settings)

    def test_pull_refuses_a_checkout_that_is_not_on_main(self):
        git(self.clone, "switch", "-q", "-c", "work")
        with self.assertRaisesRegex(update.UpdateRefused, "main"):
            update.apply("pull", self.clone, self.home, self.settings)

    def test_pull_when_already_current_changes_nothing(self):
        before = git(self.clone, "rev-parse", "HEAD")
        out = update.apply("pull", self.clone, self.home, self.settings)
        self.assertEqual((out["ok"], out["changed"]), (True, False))
        self.assertEqual(git(self.clone, "rev-parse", "HEAD"), before)

    # -- install and marketplace -----------------------------------------------------------

    def test_install_reruns_every_installed_harness(self):
        for hid in ("claude", "codex"):
            self.manifest(hid)
        calls = []

        def runner(cmd, cwd=None):
            calls.append(cmd)
            return 0, "ok"
        out = update.apply("install", self.clone, self.home, self.settings, run=runner)
        self.assertEqual([c[-1] for c in calls], ["claude", "codex"])
        self.assertTrue(all(c[0].endswith("bin/kinetic") and c[1] == "install" for c in calls))
        self.assertEqual({r["harness"] for r in out["results"]}, {"claude", "codex"})
        self.assertTrue(out["ok"])

    def test_a_failing_install_is_reported_not_hidden(self):
        self.manifest("claude")
        out = update.apply("install", self.clone, self.home, self.settings, run=lambda c, cwd=None: (1, "boom"))
        self.assertFalse(out["ok"])
        self.assertEqual(out["results"][0]["output"], "boom")

    def test_marketplace_runs_both_claude_commands_and_says_to_restart(self):
        self.manifest("claude")
        calls = []
        out = update.apply("marketplace", self.clone, self.home, self.settings,
                           run=lambda c, cwd=None: calls.append(c) or (0, "done"), has_claude=lambda: True)
        self.assertEqual([c[2] for c in calls], ["marketplace", "update"])
        self.assertTrue(out["restart_needed"])

    def test_marketplace_refuses_without_a_claude_install(self):
        self.manifest("codex")
        with self.assertRaises(update.UpdateRefused):
            update.apply("marketplace", self.clone, self.home, self.settings, has_claude=lambda: True)

    def test_an_unknown_step_is_refused(self):
        with self.assertRaises(update.UpdateRefused):
            update.apply("everything", self.clone, self.home, self.settings)

    def test_the_command_line_refuses_an_unknown_step(self):
        with self.assertRaises(SystemExit) as raised, mock.patch("sys.stderr"):
            update.main(["--apply", "everything"])
        self.assertEqual(raised.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
