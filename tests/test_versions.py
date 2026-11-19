"""Where kinetic is out of date: the plugin, the checkout the launcher runs, the latest release."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kinetic import versions


class VersionTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.tmp = Path(tmp.name)

    def clone(self, version="0.7.0", remote="git@github.com:rcmendes/kinetic.git"):
        d = self.tmp / "clone"
        (d / ".claude-plugin").mkdir(parents=True, exist_ok=True)
        (d / ".claude-plugin" / "plugin.json").write_text(json.dumps({"name": "kinetic", "version": version}))
        return d

    def test_versions_compare_as_numbers_not_text(self):
        self.assertTrue(versions.newer("0.10.0", "0.9.9"))
        self.assertFalse(versions.newer("0.7.0", "0.7.0"))
        self.assertFalse(versions.newer("0.6.9", "0.7.0"))
        self.assertTrue(versions.newer("v0.7.1", "0.7.0"))

    def test_an_unreadable_version_is_never_newer(self):
        for junk in (None, "", "latest", "1.x"):
            self.assertFalse(versions.newer(junk, "0.7.0"))
            self.assertFalse(versions.newer("0.7.0", junk))

    def test_the_checkout_version_comes_from_its_plugin_json(self):
        self.assertEqual(versions.checkout_version(self.clone("0.7.3")), "0.7.3")
        self.assertIsNone(versions.checkout_version(self.tmp / "nowhere"))

    def test_the_claude_plugin_version_comes_from_the_installed_record(self):
        record = self.tmp / ".claude" / "plugins" / "installed_plugins.json"
        record.parent.mkdir(parents=True)
        record.write_text(json.dumps({"plugins": {"kinetic@kinetic": [{"version": "0.6.0"}]}}))
        self.assertEqual(versions.plugin_version("claude", self.tmp), "0.6.0")

    def test_other_harnesses_have_no_plugin_version_to_read(self):
        self.assertIsNone(versions.plugin_version("codex", self.tmp))

    def test_a_missing_or_broken_installed_record_is_no_version(self):
        self.assertIsNone(versions.plugin_version("claude", self.tmp))
        record = self.tmp / ".claude" / "plugins" / "installed_plugins.json"
        record.parent.mkdir(parents=True)
        record.write_text("{not json")
        self.assertIsNone(versions.plugin_version("claude", self.tmp))

    def test_the_repository_is_read_from_the_clones_origin(self):
        clone = self.clone()
        self.assertEqual(versions.repository(clone, lambda a: "git@github.com:rcmendes/kinetic.git\n"),
                         "rcmendes/kinetic")
        self.assertEqual(versions.repository(clone, lambda a: "https://github.com/acme/k.git\n"), "acme/k")
        self.assertEqual(versions.repository(clone, lambda a: "https://github.com/acme/k\n"), "acme/k")
        self.assertIsNone(versions.repository(clone, lambda a: "ssh://elsewhere/x\n"))
        self.assertIsNone(versions.repository(clone, lambda a: None))

    def test_the_latest_release_is_looked_up_once_a_day(self):
        cache, calls = self.tmp / "update-check.json", []
        def gh(repo):
            calls.append(repo)
            return "v0.8.0"
        now = 1_000_000.0
        self.assertEqual(versions.latest_release("acme/k", cache, now, gh), "0.8.0")
        self.assertEqual(versions.latest_release("acme/k", cache, now + 3600, gh), "0.8.0")
        self.assertEqual(len(calls), 1)
        versions.latest_release("acme/k", cache, now + 25 * 3600, gh)
        self.assertEqual(len(calls), 2)

    def test_a_cache_for_another_repository_is_not_reused(self):
        cache, calls = self.tmp / "update-check.json", []
        gh = lambda repo: calls.append(repo) or "v1.0.0"
        versions.latest_release("acme/k", cache, 1.0, gh)
        versions.latest_release("other/k", cache, 2.0, gh)
        self.assertEqual(calls, ["acme/k", "other/k"])

    def test_a_failed_lookup_is_silent_and_does_not_poison_the_cache(self):
        cache = self.tmp / "update-check.json"
        self.assertIsNone(versions.latest_release("acme/k", cache, 1.0, lambda r: None))
        self.assertIsNone(versions.latest_release("acme/k", cache, 2.0, lambda r: "garbage"))
        self.assertEqual(versions.latest_release("acme/k", cache, 3.0, lambda r: "v0.2.0"), "0.2.0")

    def test_a_lookup_that_raises_is_treated_as_no_answer(self):
        def boom(repo):
            raise OSError("offline")
        self.assertIsNone(versions.latest_release("acme/k", self.tmp / "c.json", 1.0, boom))


class NoticeTest(unittest.TestCase):
    """The once-a-day line at session start: local files only, a refresh runs in the background."""

    DAY = 24 * 3600

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.home = Path(tmp.name) / "home"
        (self.home / ".config" / "kinetic").mkdir(parents=True)
        self.clone = Path(tmp.name) / "clone"
        (self.clone / ".claude-plugin").mkdir(parents=True)
        (self.clone / ".claude-plugin" / "plugin.json").write_text(json.dumps({"version": "0.7.0"}))
        self.cfg = {"harness": "claude", "clone": str(self.clone)}
        self.spawned = []
        self.git = lambda args: "git@github.com:acme/kinetic.git\n"

    def cache(self, latest, checked_at):
        (self.home / ".config" / "kinetic" / "update-check.json").write_text(
            json.dumps({"repo": "acme/kinetic", "checked_at": checked_at, "latest": latest}))

    def notice(self, now, harness="claude", cfg=None):
        return versions.notice(self.home, cfg or self.cfg, harness, now,
                               spawn=lambda repo, cache: self.spawned.append(repo), git=self.git)

    def test_a_newer_cached_release_makes_one_line(self):
        self.cache("0.8.0", 1000.0)
        line = self.notice(1000.0)
        self.assertEqual(line, "kinetic 0.8.0 is available (you have 0.7.0): run /kinetic:update")

    def test_it_is_shown_at_most_once_a_day(self):
        self.cache("0.8.0", 1000.0)
        self.assertIsNotNone(self.notice(1000.0))
        self.assertIsNone(self.notice(1000.0 + 3600))
        self.assertIsNotNone(self.notice(1000.0 + self.DAY + 1))

    def test_the_command_is_spelled_for_the_harness(self):
        self.cache("0.8.0", 1000.0)
        self.assertTrue(self.notice(1000.0, "codex").endswith("$kntc-update"))

    def test_nothing_is_said_when_up_to_date_or_without_a_clone_or_when_off(self):
        self.cache("0.7.0", 1000.0)
        self.assertIsNone(self.notice(1000.0))
        self.cache("0.8.0", 1000.0)
        self.assertIsNone(self.notice(1000.0, cfg={"harness": "claude"}))
        self.assertIsNone(self.notice(1000.0, cfg={**self.cfg, "update_check": False}))
        self.assertEqual(self.spawned, [])

    def test_a_stale_or_missing_cache_starts_a_background_refresh_but_blocks_nothing(self):
        self.assertIsNone(self.notice(1000.0))
        self.assertEqual(self.spawned, ["acme/kinetic"])
        self.cache("0.8.0", 1000.0)
        self.assertIsNotNone(self.notice(1000.0 + 2 * self.DAY))
        self.assertEqual(len(self.spawned), 2)

    def test_a_refresh_is_not_started_again_within_six_hours(self):
        self.notice(1000.0)
        self.notice(1000.0 + 3600)
        self.assertEqual(len(self.spawned), 1)
        self.notice(1000.0 + 7 * 3600)
        self.assertEqual(len(self.spawned), 2)

    def test_a_broken_state_file_never_raises(self):
        (self.home / ".config" / "kinetic" / "update-notice.json").write_text("{nope")
        self.cache("0.8.0", 1000.0)
        self.assertIsNotNone(self.notice(1000.0))


if __name__ == "__main__":
    unittest.main()
