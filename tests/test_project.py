import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from kinetic import config, project

ROOT = Path(__file__).resolve().parents[1]


def _state(run: Path, phase: str) -> None:
    (run / ".kinetic").mkdir(parents=True, exist_ok=True)
    (run / ".kinetic" / "state.json").write_text(json.dumps({"phase": phase}))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self._t = tempfile.TemporaryDirectory()
        self.addCleanup(self._t.cleanup)
        self.tmp = Path(self._t.name)  # deliberately not realpath'd (macOS /var symlink)
        self.proj = project.create(self.tmp, "p")
        self.real = Path(os.path.realpath(self.proj))

    def test_find_from_root_run_and_worktree(self):
        wt = self.proj / "runs/r1/worktrees/api"
        wt.mkdir(parents=True)
        (wt / ".git").write_text("gitdir: x")
        _state(self.proj / "runs/r1", "build")
        for d in (self.proj, self.proj / "runs/r1", wt):
            self.assertEqual(project.find(d), self.real)

    def test_find_ignores_kinetic_json_file(self):
        d = self.tmp / "repo"
        d.mkdir()
        (d / ".kinetic.json").write_text("{}")
        self.assertIsNone(project.find(d))

    def test_find_realpath_tmp(self):
        self.assertEqual(project.find(str(self.proj)), self.real)

    def test_create_layout(self):
        for sub in (".kinetic/sessions", "bare-repos", "runs"):
            self.assertTrue((self.proj / sub).is_dir())
        self.assertEqual(project.load(self.proj), {"version": 1, "repos": {}})

    def test_create_refuses_existing_nonempty_and_inside_git(self):
        (self.tmp / "full").mkdir()
        (self.tmp / "full/x").write_text("")
        with self.assertRaises(ValueError):
            project.create(self.tmp, "full")
        g = self.tmp / "g"
        g.mkdir()
        subprocess.run(["git", "init", "-q", str(g)], check=True)
        with self.assertRaises(ValueError):
            project.create(g, "q")
        self.assertFalse((g / "q").exists())
        self.assertEqual(list((self.tmp / "full").iterdir()), [self.tmp / "full/x"])

    def test_create_refuses_parent_inside_a_project(self):
        for parent in (self.proj, self.proj / "runs"):
            with self.assertRaises(ValueError):
                project.create(parent, "inner")
            self.assertFalse((parent / "inner").exists())

    def test_cli_run_dir_refuses_unsafe_id(self):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts")}
        for bad in ("../x", "a/b", "..", "."):
            p = subprocess.run([sys.executable, "-m", "kinetic", "project", "run-dir", "--id", bad],
                               cwd=self.proj, capture_output=True, text=True, env=env)
            self.assertEqual(p.returncode, 1, bad)
        p = subprocess.run([sys.executable, "-m", "kinetic", "project", "run-dir", "--id", "r1"],
                           cwd=self.proj, capture_output=True, text=True, env=env)
        self.assertEqual(json.loads(p.stdout), {"run_dir": str(self.real / "runs" / "r1")})

    def test_update_atomic_keeps_unknown_keys(self):
        path = self.proj / ".kinetic/config.json"
        path.write_text(json.dumps({"version": 1, "repos": {}, "extra": 7}))
        out = project.update(self.proj, {"repos": {"a": 1}})
        self.assertEqual(out, {"version": 1, "repos": {"a": 1}, "extra": 7})
        self.assertEqual(json.loads(path.read_text()), out)
        self.assertFalse(path.with_suffix(".json.tmp").exists())

    def test_load_refuses_bad_json_and_newer_version(self):
        path = self.proj / ".kinetic/config.json"
        path.write_text("{nope")
        with self.assertRaises(config.ConfigError):
            project.update(self.proj, {"a": 1})
        self.assertEqual(path.read_text(), "{nope")
        path.write_text(json.dumps({"version": 2}))
        with self.assertRaises(config.ConfigError):
            project.load(self.proj)
        with self.assertRaises(config.ConfigError):
            project.update(self.proj, {"a": 1})
        self.assertEqual(json.loads(path.read_text()), {"version": 2})

    def test_run_for_path_and_open_runs(self):
        r1, r2 = self.proj / "runs/r1", self.proj / "runs/r2"
        (r1 / "worktrees/api").mkdir(parents=True)
        _state(r1, "build")
        _state(r2, "done")
        (self.proj / "runs/r3").mkdir()  # no state.json
        self.assertEqual(project.run_for_path(r1 / "worktrees/api"), Path(os.path.realpath(r1)))
        self.assertIsNone(project.run_for_path(self.proj))
        self.assertIsNone(project.run_for_path(self.proj / "runs/r3"))
        self.assertIsNone(project.run_for_path(self.tmp))
        self.assertEqual(project.open_runs(self.proj),
                         [{"run_id": "r1", "run_dir": str(r1), "phase": "build"}])

    def test_require_raises_no_project(self):
        with self.assertRaises(project.NoProject):
            project.require(self.tmp)

    def test_cli_no_project_exit_2(self):
        p = subprocess.run([sys.executable, "-m", "kinetic", "project", "find"],
                           cwd=self.tmp, capture_output=True, text=True,
                           env={**os.environ, "PYTHONPATH": str(ROOT / "scripts")})
        self.assertEqual(p.returncode, 2)
        self.assertIn("/kinetic:start", p.stderr)


if __name__ == "__main__":
    unittest.main()
