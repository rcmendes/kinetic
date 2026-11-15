from __future__ import annotations

import contextlib
import io
import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kinetic import config, discover, project


def _git_init(path: Path, remote: str | None = None) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)
    if remote:
        subprocess.run(["git", "-C", str(path), "remote", "add", "origin", remote], check=True)


class DiscoverTest(unittest.TestCase):
    def setUp(self):
        t = tempfile.TemporaryDirectory()
        self.addCleanup(t.cleanup)
        self.tmp = Path(os.path.realpath(t.name))
        self.root = self.tmp / "code"
        _git_init(self.root / "api", "git@github.com:acme/api.git")
        _git_init(self.root / "web")
        (self.root / "notes").mkdir()
        _git_init(self.root / "group" / "deep")
        (self.tmp / "api-link").symlink_to(self.root / "api")
        self.project = project.create(self.tmp, "proj")
        project.update(self.project, {"tracker": "jira", "repos": {
            "backend": {"path": str(self.tmp / "api-link"), "verify": "make test"}}})
        self.repos_json = self.project / ".kinetic" / "config.json"

    # settings / remember

    def test_settings_missing_file_gives_empty_lists(self):
        self.assertEqual(discover.settings(path=self.tmp / "nope.json"),
                         {"repo_roots": [], "github_orgs": []})

    def test_remember_creates_file_and_keeps_other_keys(self):
        s = self.tmp / "cfg" / "settings.json"
        out = discover.remember([str(self.root)], ["acme"], path=s)
        self.assertEqual(out, {"repo_roots": [str(self.root)], "github_orgs": ["acme"]})
        s.write_text(json.dumps(dict(json.loads(s.read_text()), tracker="jira")))
        discover.remember([], ["other"], path=s)
        data = json.loads(s.read_text())
        self.assertEqual(data["tracker"], "jira")
        self.assertEqual(data["github_orgs"], ["acme", "other"])

    def test_remember_normalizes_and_dedupes(self):
        link = self.tmp / "code-link"
        link.symlink_to(self.root)
        s = self.tmp / "settings.json"
        discover.remember([str(self.root) + "/", str(link)], ["acme"], path=s)
        out = discover.remember([str(self.root)], ["acme"], path=s)
        self.assertEqual(out["repo_roots"], [str(self.root)])
        self.assertEqual(out["github_orgs"], ["acme"])

    def test_remember_expands_home(self):
        s = self.tmp / "settings.json"
        old = os.environ.get("HOME")
        os.environ["HOME"] = str(self.tmp)
        self.addCleanup(lambda: os.environ.__setitem__("HOME", old) if old else os.environ.pop("HOME"))
        out = discover.remember(["~/code"], [], path=s)
        self.assertEqual(out["repo_roots"], [str(self.root)])

    def test_remember_rejects_non_directory_root(self):
        with self.assertRaises(ValueError):
            discover.remember([str(self.tmp / "missing")], [], path=self.tmp / "s.json")
        self.assertFalse((self.tmp / "s.json").exists())

    # scan

    def test_scan_folder_of_repos_one_level_only(self):
        names = sorted(r["name"] for r in discover.scan([str(self.root)], project_config=self.repos_json))
        self.assertEqual(names, ["api", "web"])

    def test_scan_root_inside_a_repo_returns_its_top_level(self):
        (self.root / "api" / "src").mkdir()
        out = discover.scan([str(self.root / "api" / "src")], project_config=self.repos_json, enclosing=True)
        self.assertEqual([(r["name"], r["path"]) for r in out], [("api", str(self.root / "api"))])
        self.assertEqual(discover.scan([str(self.root / "api" / "src")], project_config=self.repos_json), [])

    def _commit_and_clone_bare(self, dest: Path) -> None:
        src = self.root / "api"
        for k, v in (("user.name", "t"), ("user.email", "t@example.com"), ("commit.gpgsign", "false")):
            subprocess.run(["git", "-C", str(src), "config", k, v], check=True)
        (src / "a.txt").write_text("x\n")
        subprocess.run(["git", "-C", str(src), "add", "a.txt"], check=True)
        subprocess.run(["git", "-C", str(src), "commit", "-q", "-m", "c"], check=True)
        subprocess.run(["git", "clone", "-q", "--bare", str(src), str(dest)], check=True)

    def test_scan_lists_bare_child_with_bare_flag(self):
        self._commit_and_clone_bare(self.root / "svc.git")
        items = {i["name"]: i for i in discover.scan([str(self.root)], project_config=self.repos_json)}
        self.assertTrue(items["svc.git"]["bare"])
        self.assertFalse(items["web"]["bare"])
        self.assertEqual(items["svc.git"]["remote"], str(self.root / "api"))

    def test_scan_root_that_is_a_bare_repo(self):
        bare_root = self.tmp / "solo.git"
        self._commit_and_clone_bare(bare_root)
        out = discover.scan([str(bare_root)], project_config=self.repos_json)
        self.assertEqual([i["path"] for i in out], [str(bare_root)])
        self.assertTrue(out[0]["bare"])

    def test_scan_reports_remote(self):
        out = {r["name"]: r for r in discover.scan([str(self.root)], project_config=self.repos_json)}
        self.assertEqual(out["api"]["remote"], "git@github.com:acme/api.git")
        self.assertIsNone(out["web"]["remote"])

    def test_scan_marks_registered_by_real_path(self):
        out = {r["name"]: r for r in discover.scan([str(self.root)], project_config=self.repos_json)}
        self.assertEqual((out["api"]["registered"], out["api"]["registered_as"]), (True, "backend"))
        self.assertEqual((out["web"]["registered"], out["web"]["registered_as"]), (False, None))

    @unittest.skipIf(os.geteuid() == 0, "root can read any directory")
    def test_scan_skips_missing_root_and_unreadable_child(self):
        locked = self.root / "locked"
        _git_init(locked)
        locked.chmod(0o000)
        self.addCleanup(locked.chmod, 0o755)
        out = discover.scan([str(self.tmp / "missing"), str(self.root)], project_config=self.repos_json)
        self.assertEqual(sorted(r["name"] for r in out), ["api", "web"])

    def test_scan_root_inside_an_enclosing_repo_lists_children(self):
        # a dotfiles repo at the parent must not hide the checkouts under the root
        _git_init(self.tmp)
        names = sorted(r["name"] for r in discover.scan([str(self.root)], project_config=self.repos_json))
        self.assertEqual(names, ["api", "web"])

    def test_scan_enclosing_returns_top_level_from_a_subdir(self):
        (self.root / "api" / "src").mkdir()
        out = discover.scan([str(self.root / "api" / "src")], project_config=self.repos_json, enclosing=True)
        self.assertEqual([r["name"] for r in out], ["api"])

    def test_non_list_settings_values_are_rejected(self):
        s = self.tmp / "settings.json"
        for bad in ('"~/code"', "3"):
            s.write_text('{"repo_roots": %s}' % bad)
            with self.assertRaises(config.ConfigError):
                discover.settings(path=s)
            with self.assertRaises(config.ConfigError):
                discover.remember([str(self.root)], [], path=s)
            self.assertEqual(s.read_text(), '{"repo_roots": %s}' % bad)

    def test_cli_accepts_several_values_after_one_flag(self):
        with contextlib.redirect_stdout(io.StringIO()) as buf:
            code = discover.main(["scan", "--root", str(self.root), str(self.tmp / "missing")])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["name"] for r in json.loads(buf.getvalue())), ["api", "web"])

    # register

    def test_register_adds_entry_and_keeps_others(self):
        entry = discover.register("web", str(self.root / "web"), "npm test", pin="./pin.sh",
                                  setup="npm ci", verify_ticket="npm run t",
                                  project_config=self.repos_json)
        self.assertEqual(entry, {"path": str(self.root / "web"), "verify": "npm test",
                                 "verify_ticket": "npm run t", "setup": "npm ci", "pin": "./pin.sh"})
        data = json.loads(self.repos_json.read_text())
        self.assertEqual(sorted(data["repos"]), ["backend", "web"])

    def test_register_writes_project_config_keeps_other_keys(self):
        discover.register("web", str(self.root / "web"), "npm test", project_config=self.repos_json)
        data = json.loads(self.repos_json.read_text())
        self.assertEqual((data["version"], data["tracker"]), (1, "jira"))
        self.assertIn("web", data["repos"])

    def test_register_refuses_empty_verify(self):
        with self.assertRaises(ValueError):
            discover.register("web", str(self.root / "web"), "  ", project_config=self.repos_json)
        self.assertNotIn("web", json.loads(self.repos_json.read_text())["repos"])

    def test_register_refuses_name_taken_by_other_path(self):
        with self.assertRaises(ValueError):
            discover.register("backend", str(self.root / "web"), "npm test", project_config=self.repos_json)

    def test_register_same_name_and_path_unchanged_is_noop(self):
        before = self.repos_json.read_text()
        entry = discover.register("backend", str(self.root / "api"), "make test",
                                  project_config=self.repos_json)
        self.assertEqual(entry["verify"], "make test")
        self.assertEqual(self.repos_json.read_text(), before)

    def test_register_same_name_and_path_merges_given_fields(self):
        discover.register("backend", str(self.root / "api"), None, setup="make deps",
                          project_config=self.repos_json)
        entry = discover.register("backend", str(self.root / "api"), "make check",
                                  verify_ticket="make t", project_config=self.repos_json)
        self.assertEqual((entry["verify"], entry["setup"], entry["verify_ticket"]),
                         ("make check", "make deps", "make t"))
        self.assertEqual(json.loads(self.repos_json.read_text())["repos"]["backend"], entry)

    def test_register_new_repo_still_needs_verify(self):
        with self.assertRaises(ValueError):
            discover.register("web", str(self.root / "web"), None, project_config=self.repos_json)

    def test_cli_register_updates_commands_without_verify(self):
        with mock.patch("os.getcwd", return_value=str(self.project)), \
                contextlib.redirect_stdout(io.StringIO()):
            code = discover.main(["register", "--name", "backend", "--path", str(self.root / "api"),
                                  "--setup", "make deps"])
        self.assertEqual(code, 0)
        e = json.loads(self.repos_json.read_text())["repos"]["backend"]
        self.assertEqual((e["verify"], e["setup"]), ("make test", "make deps"))

    def test_malformed_config_is_never_overwritten(self):
        cases = (
            (self.tmp / "s.json", lambda p: discover.remember([str(self.root)], [], path=p)),
            (self.repos_json,
             lambda p: discover.register("web", str(self.root / "web"), "npm test", project_config=p)),
        )
        for f, call in cases:
            f.write_text("{not json")
            with self.assertRaises(config.ConfigError):
                call(f)
            self.assertEqual(f.read_text(), "{not json")

    # CLI

    def test_cli_scan_prints_json(self):
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = discover.main(["scan", "--root", str(self.root)])
        self.assertEqual(code, 0)
        self.assertEqual(sorted(r["name"] for r in json.loads(buf.getvalue())), ["api", "web"])

    def test_cli_register_error_exits_1(self):
        err = io.StringIO()
        with mock.patch("os.getcwd", return_value=str(self.project)), contextlib.redirect_stderr(err):
            code = discover.main(["register", "--name", "x", "--path", str(self.root), "--verify", ""])
        self.assertEqual(code, 1)
        self.assertIn("verify", err.getvalue())

    def test_cli_register_passes_setup_and_verify_ticket(self):
        with mock.patch("os.getcwd", return_value=str(self.project)), \
                contextlib.redirect_stdout(io.StringIO()):
            code = discover.main(["register", "--name", "web", "--path", str(self.root / "web"),
                                  "--verify", "v", "--setup", "s", "--verify-ticket", "vt"])
        self.assertEqual(code, 0)
        e = json.loads(self.repos_json.read_text())["repos"]["web"]
        self.assertEqual((e["setup"], e["verify_ticket"]), ("s", "vt"))

    def test_cli_register_without_project_exits_2(self):
        err = io.StringIO()
        with mock.patch("os.getcwd", return_value=str(self.tmp)), contextlib.redirect_stderr(err):
            code = discover.main(["register", "--name", "x", "--path", str(self.root), "--verify", "v"])
        self.assertEqual(code, 2)
        self.assertIn("no kinetic project", err.getvalue())

    def test_cli_remember_uses_patched_settings(self):
        s = self.tmp / "settings.json"
        with mock.patch.object(discover, "SETTINGS", s), contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(discover.main(["remember", "--org", "acme"]), 0)
        self.assertEqual(json.loads(s.read_text())["github_orgs"], ["acme"])


if __name__ == "__main__":
    unittest.main()
