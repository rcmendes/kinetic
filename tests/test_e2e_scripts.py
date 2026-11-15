from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from kinetic import bare, board, discover, frontier, gitops, graph, next_step, project, state, verify
from tests.fixtures import two_repo
from tests.test_bare import make_remote


def commit(path, name):
    (Path(path) / name).write_text(name + "\n")
    for args in (("add", name), ("commit", "-m", name)):
        subprocess.run(["git", "-c", "commit.gpgsign=false", "-C", str(path), *args],
                       check=True, capture_output=True, text=True)


class EpicFlowTests(unittest.TestCase):
    """Follows skills/run/guides/build-loop.md: integration worktrees under <run>/worktrees/<repo> on
    kinetic/<run-id>/integration, ticket worktrees on kinetic/<run-id>/<id> cut at dispatch time."""

    def test_epic_flow_scripts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = two_repo.build(Path(tmp) / "repos")
            run = Path(tmp) / "run"
            rid = state.init(run, "epic", "E-1")["run_id"]
            integ_branch = f"kinetic/{rid}/integration"
            for name in ("lib", "svc"):
                wt = run / "worktrees" / name
                gitops.create_worktree(fx[name], wt, integ_branch, "main")
                state.set_repo(run, name, str(wt), integ_branch)
            state.set_ticket(run, "L1", repo="lib")
            state.set_ticket(run, "S1", repo="svc",
                             blocked_by=[{"id": "L1", "type": "snapshot"}])
            state.set_ticket(run, "L2", repo="lib",
                             blocked_by=[{"id": "S1", "type": "merge"}])
            load = lambda: state.load(run)
            self.assertEqual(graph.validate(load()["tickets"], "epic"), [])
            self.assertEqual(frontier.ready(load()), ["L1"])

            def build_ticket(tid):
                repo = load()["tickets"][tid]["repo"]
                wt = run / "worktrees" / f"{repo}-{tid}"
                branch = f"kinetic/{rid}/{tid}"
                gitops.create_worktree(load()["repos"][repo]["path"], wt, branch, integ_branch)
                state.set_ticket(run, tid, branch=branch, worktree=str(wt), status="running")
                commit(wt, f"{tid}.txt")
                state.record_edit(run, str(wt / f"{tid}.txt"))
                v = verify.run(run, tid, repo, str(wt), project_config=fx["project_config"])
                self.assertEqual(v["exit_code"], 0)
                state.set_ticket(run, tid, status="done")
                res = gitops.merge_ticket(load()["repos"][repo]["path"], branch, integ_branch)
                self.assertEqual(res, {"merged": True, "conflicts": []})
                state.set_ticket(run, tid, status="merged")

            build_ticket("L1")
            # R15: pin marks L1 snapshot_published; only then is S1 ready
            self.assertEqual(frontier.ready(load()), [])
            res = gitops.pin(run, "lib", "svc", project_config=fx["project_config"])
            self.assertTrue(res["ok"], res)
            self.assertEqual(frontier.ready(load()), ["S1"])
            build_ticket("S1")
            self.assertEqual(frontier.ready(load()), ["L2"])
            build_ticket("L2")

            # the merge edge L2(lib) <- S1(svc) orders svc before lib; names alone would give lib first
            self.assertEqual(gitops.merge_order(load()), ["svc", "lib"])
            state.set_phase(run, "build")
            self.assertEqual(next_step.suggest(load())["reason"], "review and ship")

    def test_epic_flow_with_human_and_ships_with(self):
        with tempfile.TemporaryDirectory() as tmp:
            fx = two_repo.build(Path(tmp) / "repos")
            run = Path(tmp) / "run"
            rid = state.init(run, "epic", "E-2")["run_id"]
            integ_branch = f"kinetic/{rid}/integration"
            for name in ("lib", "svc"):
                wt = run / "worktrees" / name
                gitops.create_worktree(fx[name], wt, integ_branch, "main")
                state.set_repo(run, name, str(wt), integ_branch)
            state.set_ticket(run, "L1", repo="lib")
            state.set_ticket(run, "H1", kind="human",
                             blocked_by=[{"id": "L1", "type": "merge"}])
            state.set_ticket(run, "S1", repo="svc", ships_with="g",
                             blocked_by=[{"id": "H1", "type": "merge"}])
            state.set_ticket(run, "L2", repo="lib", ships_with="g",
                             blocked_by=[{"id": "S1", "type": "merge"}])
            load = lambda: state.load(run)
            self.assertEqual(graph.validate(load()["tickets"], "epic"), [])
            self.assertEqual(frontier.ready(load()), ["L1"])

            def build_ticket(tid):
                repo = load()["tickets"][tid]["repo"]
                wt = run / "worktrees" / f"{repo}-{tid}"
                branch = f"kinetic/{rid}/{tid}"
                gitops.create_worktree(load()["repos"][repo]["path"], wt, branch, integ_branch)
                state.set_ticket(run, tid, branch=branch, worktree=str(wt), status="running")
                commit(wt, f"{tid}.txt")
                state.record_edit(run, str(wt / f"{tid}.txt"))
                v = verify.run(run, tid, repo, str(wt), project_config=fx["project_config"])
                self.assertEqual(v["exit_code"], 0)
                state.set_ticket(run, tid, status="done")
                res = gitops.merge_ticket(load()["repos"][repo]["path"], branch, integ_branch)
                self.assertEqual(res, {"merged": True, "conflicts": []})
                state.set_ticket(run, tid, status="merged")

            build_ticket("L1")
            self.assertEqual(frontier.ready(load()), [])
            self.assertEqual(frontier.waiting_on_you(load()), ["H1"])
            self.assertTrue(frontier.only_human_left(load()))
            state.human_done(run, "H1", "ok")
            self.assertEqual(frontier.ready(load()), ["S1"])
            build_ticket("S1")
            self.assertEqual(frontier.ready(load()), ["L2"])
            build_ticket("L2")
            self.assertEqual(gitops.ships_with_groups(load()), {"g": ["lib", "svc"]})
            for tid in ("L1", "H1", "S1", "L2"):
                board.write_header(run, tid)
            text = board.render(run).read_text()
            for tid in ("L1", "H1", "S1", "L2"):
                self.assertIn(tid, text)



class ProjectE2ETests(unittest.TestCase):
    def test_project_e2e(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, True)
        env = {"HOME": str(tmp / "home"), "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.com",
               "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@example.com"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        for k, v in old.items():
            self.addCleanup(lambda k=k, v=v: os.environ.__setitem__(k, v) if v is not None else os.environ.pop(k, None))
        (tmp / "home").mkdir()

        proj = project.create(tmp, "proj")
        cfg = project.config_path(proj)
        run = project.run_dir(proj, "r1")
        names = ("api", "web")
        for n in names:
            make_remote(tmp / "remotes" / n)
            bare.clone(project.bare_dir(proj), url=str(tmp / "remotes" / n), name=n)
            discover.register(n, str(project.bare_dir(proj) / f"{n}.git"), "true",
                              setup="touch setup.done", verify_ticket="true", project_config=cfg)
        state.init(run, "epic", "e1")

        for n in names:
            b = project.bare_dir(proj) / f"{n}.git"
            integ, ticket = run / "worktrees" / n, run / "worktrees" / f"{n}-t1"
            gitops.create_worktree(b, integ, f"kinetic/r1/{n}", "main")
            gitops.create_worktree(b, ticket, f"kinetic/r1/{n}-t1", "main")
            self.assertTrue(gitops.run_setup(run, n, ticket, cfg)["ok"])
            self.assertTrue((ticket / "setup.done").exists())
            self.assertEqual(verify.run(run, "t1", n, ticket, cfg, scope="ticket")["exit_code"], 0)
            commit(ticket, "feature.txt")
            self.assertTrue(gitops.merge_ticket(integ, f"kinetic/r1/{n}-t1", f"kinetic/r1/{n}")["merged"])
            self.assertTrue((integ / "feature.txt").exists())

        self.assertTrue((proj / ".kinetic" / "sessions").is_dir())
        self.assertTrue((state.internal(run) / "state.json").is_file())
        self.assertTrue(list((state.internal(run) / "logs").glob("verify-*.log")))
        self.assertEqual(sorted(project.load(proj)["repos"]), list(names))
        # nothing project-scoped leaks into the machine config
        machine = tmp / "home" / ".config" / "kinetic"
        for leaked in ("repos.json", "active-run"):
            self.assertFalse((machine / leaked).exists())
        if (machine / "settings.json").exists():
            keys = set(json.loads((machine / "settings.json").read_text()))
            self.assertFalse(keys & {"repos", "tracker", "tracker_settings", "tracker_names",
                                     "tracker_description", "pr_template"})


if __name__ == "__main__":
    unittest.main()
