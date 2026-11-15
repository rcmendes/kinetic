from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from kinetic import project
from kinetic.config import ConfigError, resolve


class ResolveTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(t.name).resolve()
        self.project = project.create(self.tmp, "proj")
        self.cfg = self.project / ".kinetic" / "config.json"
        self.repo = self.tmp / "svc"
        self.repo.mkdir()

    def _project(self, repos):
        project.update(self.project, {"repos": repos})

    def _repo(self, data):
        (self.repo / ".kinetic.json").write_text(json.dumps(data))

    def test_resolve_reads_project_repos(self):
        self._project({"svc": {"verify": "make test", "base_branch": "master", "pin": "p"},
                       "other": {"verify": "nope"}})
        r = resolve("svc", self.repo, self.cfg)
        self.assertEqual((r["verify"], r["base_branch"], r["pin"]), ("make test", "master", "p"))

    def test_resolve_new_keys_default_none(self):
        self._project({"svc": {"verify": "make test"}})
        r = resolve("svc", self.repo, self.cfg)
        self.assertEqual(r, {"verify": "make test", "verify_ticket": None, "setup": None,
                             "publish_snapshot": None, "pin": None, "base_branch": "main"})
        self._project({"svc": {"verify": "v", "verify_ticket": "vt", "setup": "npm ci"}})
        r = resolve("svc", self.repo, self.cfg)
        self.assertEqual((r["verify_ticket"], r["setup"]), ("vt", "npm ci"))

    def test_repo_override_wins(self):
        self._project({"svc": {"verify": "make test", "base_branch": "master"}})
        self._repo({"verify": "mvn verify"})
        r = resolve("svc", self.repo, self.cfg)
        self.assertEqual((r["verify"], r["base_branch"]), ("mvn verify", "master"))

    def test_missing_verify_raises(self):
        with self.assertRaises(ConfigError) as ctx:
            resolve("svc", self.repo, self.cfg)
        self.assertEqual(
            str(ctx.exception),
            f"no verify command for repo 'svc': set it in {self.cfg} or {self.repo}/.kinetic.json",
        )

    def test_malformed_config_raises_config_error(self):
        repo_file = self.repo / ".kinetic.json"
        cases = [
            (self.cfg, "{bad", str(self.cfg)),
            (self.cfg, "[]", str(self.cfg)),
            (self.cfg, '{"repos": {"svc": "x"}}', "svc"),
            (self.cfg, '{"repos": {"svc": null}}', "svc"),
            (repo_file, "{bad", str(repo_file)),
            (repo_file, "[]", str(repo_file)),
            (repo_file, '"x"', str(repo_file)),
        ]
        for path, text, needle in cases:
            self.cfg.write_text('{"version": 1, "repos": {}}')
            repo_file.unlink(missing_ok=True)
            path.write_text(text)
            with self.assertRaises(ConfigError, msg=text) as ctx:
                resolve("svc", self.repo, self.cfg)
            self.assertIn(needle, str(ctx.exception))
            self.assertIn(str(path), str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
