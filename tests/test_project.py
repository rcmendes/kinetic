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
        self.assertEqual(json.loads(p.stdout), {"run_dir": str(self.real / "runs" / "r1"),
                                                "exists": False, "phase": None})

    def test_cli_run_dir_reports_phase(self):
        for rid, phase in (("open", "grill"), ("finished", "done")):
            (self.proj / "runs" / rid / ".kinetic").mkdir(parents=True)
            (self.proj / "runs" / rid / ".kinetic" / "state.json").write_text(json.dumps({"phase": phase}))
        (self.proj / "runs" / "nostate").mkdir()
        got = {r: json.loads(self._cli(self.proj, "run-dir", "--id", r).stdout)["phase"]
               for r in ("open", "finished", "nostate", "missing")}
        self.assertEqual(got, {"open": "grill", "finished": "done", "nostate": None, "missing": None})

    def test_create_writes_instruction_files(self):
        for n in project.INSTRUCTION_FILES:
            t = (self.proj / n).read_text()
            self.assertEqual(t, f"{project.INSTRUCTIONS_BEGIN}\n{project.INSTRUCTIONS_TEXT}\n{project.INSTRUCTIONS_END}\n")

    def test_instructions_keeps_user_text_and_is_idempotent(self):
        old = f"before\n\n{project.INSTRUCTIONS_BEGIN}\nold\n{project.INSTRUCTIONS_END}\n\nafter\n"
        for n in project.INSTRUCTION_FILES:
            (self.proj / n).write_text(old)
        project.write_instructions(self.proj)
        out = project.write_instructions(self.proj)
        self.assertEqual(out, [str(self.proj / n) for n in project.INSTRUCTION_FILES])
        for n in project.INSTRUCTION_FILES:
            t = (self.proj / n).read_text()
            self.assertTrue(t.startswith("before\n\n") and t.endswith("\n\nafter\n"))
            self.assertNotIn("\nold\n", t)
            self.assertEqual(t.count(project.INSTRUCTIONS_BEGIN), 1)
            self.assertIn(project.INSTRUCTIONS_TEXT, t)

    def test_instructions_appends_when_no_block(self):
        (self.proj / "AGENTS.md").write_text("mine\n")
        project.write_instructions(self.proj)
        t = (self.proj / "AGENTS.md").read_text()
        self.assertTrue(t.startswith("mine\n\n" + project.INSTRUCTIONS_BEGIN))
        self.assertTrue(t.endswith(project.INSTRUCTIONS_END + "\n"))

    def test_instructions_keeps_text_after_a_dangling_begin(self):
        block = f"{project.INSTRUCTIONS_BEGIN}\nold\n{project.INSTRUCTIONS_END}"
        (self.proj / "AGENTS.md").write_text(f"a\n{project.INSTRUCTIONS_BEGIN}\nmine\n\n{block}\n")
        project.write_instructions(self.proj)
        t = (self.proj / "AGENTS.md").read_text()
        self.assertTrue(t.startswith(f"a\n{project.INSTRUCTIONS_BEGIN}\nmine\n\n{project.INSTRUCTIONS_BEGIN}\n"), t)
        self.assertNotIn("\nold\n", t)

    def test_instructions_write_through_a_symlink_once(self):
        (self.proj / "AGENTS.md").write_text("mine\n")
        (self.proj / "CLAUDE.md").unlink(missing_ok=True)
        (self.proj / "CLAUDE.md").symlink_to("AGENTS.md")
        project.write_instructions(self.proj)
        self.assertTrue((self.proj / "CLAUDE.md").is_symlink())
        t = (self.proj / "AGENTS.md").read_text()
        self.assertTrue(t.startswith("mine\n\n"))
        self.assertEqual(t.count(project.INSTRUCTIONS_BEGIN), 1)

    def test_failed_instruction_write_leaves_no_tmp_file(self):
        from unittest import mock
        with mock.patch("os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                project.write_instructions(self.proj)
        self.assertEqual(sorted(p.name for p in self.proj.glob("*.tmp")), [])

    def test_missing_instructions(self):
        self.assertEqual(project.missing_instructions(self.proj), [])
        (self.proj / "AGENTS.md").unlink()
        (self.proj / "CLAUDE.md").write_text(f"{project.INSTRUCTIONS_BEGIN}\nno end\n")
        self.assertEqual(project.missing_instructions(self.proj), ["AGENTS.md", "CLAUDE.md"])

    def _cli(self, cwd, *args, home=None):
        env = {**os.environ, "PYTHONPATH": str(ROOT / "scripts")}
        if home:
            env["HOME"] = str(home)
        return subprocess.run([sys.executable, "-m", "kinetic", "project", *args],
                              cwd=cwd, capture_output=True, text=True, env=env)

    def test_cli_start_session_ok_outside_project(self):
        home = self.tmp / "home"
        home.mkdir()
        for cwd in (self.tmp, self.proj):
            p = self._cli(cwd, "start-session", home=home)
            self.assertEqual((p.returncode, json.loads(p.stdout)), (0, {"ok": True}))
        self.assertEqual(list(home.iterdir()), [])

    def test_cli_run_dir_reports_exists(self):
        (self.proj / "runs/r1").mkdir()
        self.assertTrue(json.loads(self._cli(self.proj, "run-dir", "--id", "r1").stdout)["exists"])
        self.assertFalse(json.loads(self._cli(self.proj, "run-dir", "--id", "r2").stdout)["exists"])

    def test_cli_instructions_rewrites(self):
        (self.proj / "CLAUDE.md").unlink()
        out = json.loads(self._cli(self.proj, "instructions").stdout)
        self.assertEqual(len(out["written"]), 2)
        self.assertEqual(project.missing_instructions(self.proj), [])

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
