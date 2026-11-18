"""The release metadata and the install instructions must agree with each other."""
from __future__ import annotations

import json
import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _json(name):
    return json.loads((ROOT / ".claude-plugin" / name).read_text())


class ReleaseTest(unittest.TestCase):
    def test_marketplace_and_plugin_carry_the_same_version(self):
        listed = _json("marketplace.json")["plugins"][0]
        self.assertEqual(listed["name"], _json("plugin.json")["name"])
        self.assertEqual(listed["version"], _json("plugin.json")["version"])

    def test_readme_states_the_current_version(self):
        said = re.search(r"kinetic is at (\d+\.\d+\.\d+)", (ROOT / "README.md").read_text())
        self.assertIsNotNone(said, "README has no 'kinetic is at <version>' line")
        self.assertEqual(said.group(1), _json("plugin.json")["version"])

    def test_install_commands_name_the_marketplace_owner(self):
        owner = _json("marketplace.json")["owner"]["name"]
        owners = set(re.findall(r"github\.com[:/]([\w.-]+)/kinetic", (ROOT / "README.md").read_text()))
        owners |= set(re.findall(r"marketplace add ([\w.-]+)/kinetic", (ROOT / "README.md").read_text()))
        self.assertEqual(owners, {owner})

    def test_the_release_steps_are_written_down(self):
        text = (ROOT / "docs" / "RELEASING.md").read_text()
        for needed in ("plugin.json", "marketplace.json", "marketplace update", "plugin update"):
            self.assertIn(needed, text)

    def test_the_version_workflow_checks_both_files(self):
        text = (ROOT / ".github" / "workflows" / "version-bump.yml").read_text()
        self.assertIn("marketplace.json", text)


if __name__ == "__main__":
    unittest.main()
