from __future__ import annotations

import contextlib
import io
import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kinetic import bare


def git(path, *args):
    return subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make_remote(path, branch="main"):
    path.mkdir(parents=True, exist_ok=True)
    git(path, "init", "-b", branch)
    git(path, "config", "user.name", "t")
    git(path, "config", "user.email", "t@example.com")
    git(path, "config", "commit.gpgsign", "false")
    (path / "a.txt").write_text("base\n")
    git(path, "add", "a.txt")
    git(path, "commit", "-m", "base")


def make_bare(remote, dest):
    dest.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "clone", "--bare", str(remote), str(dest)],
                   check=True, capture_output=True, text=True)


def commit_file(path, name, text, message):
    (path / name).write_text(text)
    git(path, "add", name)
    git(path, "commit", "-m", message)


def fake_gh_then_git(remote):
    """subprocess.run stand-in: a gh argv bare-clones the local remote into its dest."""
    real = subprocess.run

    def run(argv, *args, **kwargs):
        if argv[0] == "gh":
            dest = argv[4]
            return real(["git", "clone", "--bare", str(remote), dest],
                        check=True, capture_output=True, text=True)
        return real(argv, *args, **kwargs)
    return run


class BareTests(unittest.TestCase):
    def setUp(self):
        self._td = tempfile.TemporaryDirectory()
        self.addCleanup(self._td.cleanup)
        self.tmp = Path(os.path.realpath(self._td.name))

    def test_is_bare_rejects_lookalikes(self):
        make_remote(self.tmp / "remote")
        make_bare(self.tmp / "remote", self.tmp / "b" / "api.git")
        fake = self.tmp / "b" / "fake"
        for d in ("objects", "refs"):
            (fake / d).mkdir(parents=True)
        (fake / "HEAD").write_text("ref: refs/heads/main\n")
        (fake / "config").write_text("[core]\n\tbare = false\n")
        self.assertTrue(bare.is_bare(self.tmp / "b" / "api.git"))
        self.assertFalse(bare.is_bare(fake))
        self.assertFalse(bare.is_bare(self.tmp / "remote"))
        self.assertFalse(bare.is_bare(self.tmp / "remote" / ".git"))

    def test_list_returns_bare_children_with_remote_and_default_branch(self):
        make_remote(self.tmp / "remote")
        make_bare(self.tmp / "remote", self.tmp / "b" / "api.git")
        (self.tmp / "b" / "notes").mkdir()
        out = bare.list_clones(str(self.tmp / "b"))
        self.assertEqual([c["name"] for c in out], ["api"])
        self.assertEqual(out[0]["remote"], str(self.tmp / "remote"))
        self.assertEqual(out[0]["default_branch"], "main")

    def test_missing_bare_dir_lists_nothing_and_clone_refuses(self):
        self.assertEqual(bare.list_clones(str(self.tmp / "gone")), [])
        with self.assertRaises(ValueError):
            bare.clone(str(self.tmp / "gone"), url=str(self.tmp / "remote"), name="api")

    def test_clone_by_url_configures_refspec_fetch_and_upstream(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        out = bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name="api")
        dest = self.tmp / "b" / "api.git"
        self.assertEqual(out, {"name": "api", "path": str(dest), "default_branch": "main"})
        self.assertTrue(bare.is_bare(dest))
        self.assertEqual(git(dest, "config", "--get", "remote.origin.fetch"),
                         "+refs/heads/*:refs/remotes/origin/*")
        self.assertEqual(git(dest, "rev-parse", "origin/main"),
                         git(self.tmp / "remote", "rev-parse", "main"))
        self.assertEqual(git(dest, "rev-parse", "--abbrev-ref", "main@{upstream}"), "origin/main")

    def test_clone_refuses_existing_target_and_bad_args(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b" / "api.git").mkdir(parents=True)
        with self.assertRaises(ValueError):
            bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name="api")
        with self.assertRaises(ValueError):
            bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"))  # no name
        with self.assertRaises(ValueError):
            bare.clone(str(self.tmp / "b"), repo="acme/api", url="x", name="api")
        for n in (".git", "..git", "...git"):
            with self.assertRaises(ValueError):
                bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name=n)

    def test_clone_failure_raises_runtime_error(self):
        (self.tmp / "b").mkdir()
        with self.assertRaises(RuntimeError):
            bare.clone(str(self.tmp / "b"), url=str(self.tmp / "nowhere"), name="api")

    def test_clone_by_repo_calls_gh_with_bare(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        with mock.patch.object(bare.subprocess, "run",
                               side_effect=fake_gh_then_git(self.tmp / "remote")) as run:
            out = bare.clone(str(self.tmp / "b"), repo="acme/api")
        self.assertEqual(run.call_args_list[0].args[0][:4], ["gh", "repo", "clone", "acme/api"])
        self.assertEqual(run.call_args_list[0].args[0][-2:], ["--", "--bare"])
        self.assertEqual(out["name"], "api")
        self.assertEqual(out["default_branch"], "main")

    def _two_clones(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        for n in ("api", "web"):
            bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name=n)

    def test_refresh_reports_advanced_refs_and_leaves_local_branch(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name="api")
        dest = self.tmp / "b" / "api.git"
        old = git(dest, "rev-parse", "main")
        commit_file(self.tmp / "remote", "b.txt", "b\n", "second")
        new = git(self.tmp / "remote", "rev-parse", "main")
        [r] = bare.refresh(str(self.tmp / "b"), ["api"])
        self.assertEqual((r["ok"], r["error"]), (True, None))
        self.assertEqual(r["advanced"],
                         [{"ref": "refs/remotes/origin/main", "from": old, "to": new}])
        self.assertEqual(git(dest, "rev-parse", "main"), old)  # local branch untouched

    def test_refresh_reports_new_and_pruned_refs(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name="api")
        git(self.tmp / "remote", "branch", "feat")
        [r] = bare.refresh(str(self.tmp / "b"), ["api"])
        sha = git(self.tmp / "remote", "rev-parse", "feat")
        self.assertEqual(r["advanced"],
                         [{"ref": "refs/remotes/origin/feat", "from": None, "to": sha}])
        git(self.tmp / "remote", "branch", "-D", "feat")
        [r] = bare.refresh(str(self.tmp / "b"), ["api"])
        self.assertEqual(r["advanced"],
                         [{"ref": "refs/remotes/origin/feat", "from": sha, "to": None}])

    def test_refresh_all_when_names_is_none(self):
        self._two_clones()
        out = bare.refresh(str(self.tmp / "b"))
        self.assertEqual([r["name"] for r in out], ["api", "web"])
        self.assertTrue(all(r["ok"] and r["advanced"] == [] for r in out))

    def test_refresh_reports_failure_and_continues(self):
        self._two_clones()
        git(self.tmp / "b" / "web.git", "remote", "set-url", "origin", str(self.tmp / "nowhere"))
        out = bare.refresh(str(self.tmp / "b"), ["api", "web", "nope"])
        self.assertEqual([r["ok"] for r in out], [True, False, False])
        self.assertTrue(out[1]["error"])
        self.assertIn("no bare clone named nope", out[2]["error"])

    def test_refresh_refuses_clone_without_fetch_refspec(self):
        make_remote(self.tmp / "remote")
        make_bare(self.tmp / "remote", self.tmp / "b" / "api.git")  # plain clone --bare: no refspec
        dest = self.tmp / "b" / "api.git"
        old = git(dest, "rev-parse", "main")
        commit_file(self.tmp / "remote", "b.txt", "b\n", "second")
        [r] = bare.refresh(str(self.tmp / "b"), ["api"])
        self.assertEqual((r["ok"], r["advanced"]), (False, []))
        self.assertEqual(r["error"], f"no fetch refspec; run git -C {dest} config "
                         "remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'")
        self.assertEqual(git(dest, "rev-parse", "main"), old)

    def test_clone_rejects_name_that_is_not_one_path_component(self):
        make_remote(self.tmp / "remote")
        (self.tmp / "b").mkdir()
        for bad in ("../evil", "a/b", "..", "."):
            with self.assertRaises(ValueError):
                bare.clone(str(self.tmp / "b"), url=str(self.tmp / "remote"), name=bad)
        self.assertEqual(sorted(p.name for p in (self.tmp / "b").iterdir()), [])

    def test_refresh_rejects_name_that_is_not_one_path_component(self):
        self._two_clones()
        [r] = bare.refresh(str(self.tmp / "b"), ["../b/api"])
        self.assertEqual((r["ok"], r["advanced"]), (False, []))
        self.assertIn("not a single path component", r["error"])

    def _project(self):
        from kinetic import project
        proj = project.create(self.tmp, "proj")
        return Path(proj), project.bare_dir(Path(proj))

    def _in(self, d, argv):
        old = os.getcwd()
        os.chdir(d)
        try:
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                return bare.main(argv)
        finally:
            os.chdir(old)

    def test_clone_into_project_bare_repos(self):
        make_remote(self.tmp / "remote")
        proj, bd = self._project()
        self.assertEqual(self._in(proj, ["clone", "--url", str(self.tmp / "remote"),
                                         "--name", "api"]), 0)
        self.assertTrue(bare.is_bare(bd / "api.git"))
        self.assertEqual(self._in(proj, ["clone", "--url", str(self.tmp / "remote"),
                                         "--name", "api"]), 1)  # exists
        self.assertEqual(self._in(proj, ["refresh", "--name", "api"]), 0)
        self.assertEqual(self._in(proj, ["refresh", "--name", "api.git"]), 0)
        self.assertEqual(self._in(proj, ["refresh", "--name", "nope"]), 1)
        self.assertEqual(self._in(proj, ["list"]), 0)

    def test_create_calls_gh_then_clones(self):
        calls = []
        with mock.patch.object(bare, "_run", side_effect=lambda argv: calls.append(argv) or ""), \
                mock.patch.object(bare, "clone", return_value={"name": "n.git"}) as cl:
            out = bare.create(str(self.tmp), "o", "n", "private")
        self.assertEqual(calls, [["gh", "repo", "create", "o/n", "--private", "--add-readme"]])
        cl.assert_called_once_with(str(self.tmp), repo="o/n")
        self.assertEqual(out, {"name": "n.git"})

    def test_create_rejects_bad_visibility(self):
        with mock.patch.object(bare, "_run") as run:
            with self.assertRaises(ValueError):
                bare.create(str(self.tmp), "o", "n", "secret")
        run.assert_not_called()

    def test_gh_ready_false_without_gh(self):
        with mock.patch.dict(os.environ, {"PATH": ""}):
            self.assertFalse(bare.gh_ready())

    def test_cli_outside_project_exit_2(self):
        for argv in (["list"], ["refresh"], ["clone", "--url", "x", "--name", "y"]):
            self.assertEqual(self._in(self.tmp, argv), 2)



if __name__ == "__main__":
    unittest.main()
