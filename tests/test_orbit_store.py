from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from kinetic import orbit_store, state, ticket_store
from kinetic.tracker import ROOT, adapter_defaults

from tests.test_ticket_store import TicketStoreContract

NAMES = adapter_defaults(ROOT / "trackers" / "orbit.md")
RUN = "0123456789ab"
HOLDER = f"kinetic-{RUN}"


def ticket(**fields) -> dict:
    t = state.ticket_defaults()
    t.update(fields)
    return t


def orbit_available() -> bool:
    if not shutil.which("orbit"):
        return False
    probe = subprocess.run(["orbit", "batch", "--help"], capture_output=True)
    if probe.returncode != 0:
        return False
    version = subprocess.run(["orbit", "--version"], capture_output=True, text=True)
    m = re.search(r"(\d+)\.(\d+)\.(\d+)", version.stdout or "")
    return bool(m) and tuple(int(g) for g in m.groups()) >= (0, 10, 16)


SKIP_REASON = "orbit 0.10.16 or later (Batches and Annotations, nested SubTasks) not installed"


class DeriveTest(unittest.TestCase):
    def derive(self, **fields):
        return orbit_store.derive(ticket(**fields), RUN, NAMES)

    def test_derive_matches_status_table(self):
        run_label = f"kinetic-run-{RUN}"
        self.assertEqual(self.derive(status="todo"),
                         {"status": "todo", "claim": None, "waiting_on": None,
                          "labels": {run_label, "kinetic"}})
        self.assertEqual(self.derive(status="todo", kind="human"),
                         {"status": "todo", "claim": None, "waiting_on": None,
                          "labels": {run_label, "ready-for-human"}})
        self.assertEqual(self.derive(status="running"),
                         {"status": "in_progress", "claim": HOLDER, "waiting_on": None,
                          "labels": {run_label}})
        for status in ("needs_decision", "blocked"):
            d = self.derive(status=status, human_note="pick a schema")
            self.assertEqual((d["status"], d["claim"], d["labels"]),
                             ("in_progress", HOLDER, {run_label}), status)
            self.assertEqual(d["waiting_on"], "pick a schema")
        self.assertEqual(self.derive(status="needs_decision")["waiting_on"],
                         "kinetic: needs_decision")
        for status in ("done", "done_with_concerns", "merged", "released"):
            d = self.derive(status=status)
            self.assertEqual((d["status"], d["claim"], d["waiting_on"]), ("done", None, None), status)

    def test_ships_with_label(self):
        self.assertIn("ships-with-g1", self.derive(ships_with="g1")["labels"])


class OpsForTest(unittest.TestCase):
    def ops(self, before, after):
        return orbit_store.ops_for("orb-a.b", before, after, RUN, NAMES)

    def test_ops_for_dispatch_unholds_and_claims_in_one_batch(self):
        ops = self.ops(ticket(status="todo"), ticket(status="running"))
        self.assertEqual([o["op"] for o in ops], ["label", "claim", "annotate"])
        self.assertEqual(ops[0]["remove"], ["kinetic"])
        self.assertEqual(ops[1], {"op": "claim", "id": "orb-a.b", "by": HOLDER})
        self.assertEqual(ops[2]["owner"], "kinetic")
        self.assertEqual(ops[2]["value"]["status"], "running")

    def test_no_op_mutation_sends_no_batch(self):
        t = ticket(status="running", repo="api")
        self.assertEqual(self.ops(t, dict(t, liveness="alive")), [])

    def test_blocked_by_becomes_dependency_and_annotation(self):
        edge = {"id": "orb-x.y", "type": "merge"}
        ops = self.ops(ticket(), ticket(blocked_by=[edge]))
        self.assertIn({"op": "depend", "id": "orb-a.b", "on": "orb-x.y"}, ops)
        self.assertEqual(ops[-1]["value"]["blocked_by"], [edge])

    def test_resume_clears_waiting_and_reclaims(self):
        ops = self.ops(ticket(status="needs_decision"), ticket(status="running"))
        kinds = [o["op"] for o in ops]
        self.assertEqual(kinds[:2], ["update", "claim"])
        self.assertEqual(ops[0], {"op": "update", "id": "orb-a.b", "clear_waiting": True})

    def test_pause_sets_waiting_after_claim(self):
        ops = self.ops(ticket(status="running"), ticket(status="needs_decision", human_note="why"))
        self.assertEqual(ops[0], {"op": "update", "id": "orb-a.b", "waiting_on": "why"})

    def test_done_releases_claim(self):
        ops = self.ops(ticket(status="running"), ticket(status="done"))
        self.assertIn({"op": "done", "id": "orb-a.b", "by": HOLDER}, ops)

    def test_reopen_before_running_again(self):
        ops = self.ops(ticket(status="done"), ticket(status="running"))
        self.assertEqual(ops[0], {"op": "reopen", "id": "orb-a.b"})
        self.assertIn({"op": "claim", "id": "orb-a.b", "by": HOLDER}, ops)


def task_json(tid, ann, *, status="todo", lease_until=None, task_type="task", labels=()):
    return {"id": tid, "type": task_type, "status": status, "lease_until": lease_until,
            "labels": list(labels), "annotations": {"kinetic": ann} if ann is not None else {}}


class FakeOrbitTest(unittest.TestCase):
    """Orbit store behavior with `call` patched: no binary needed."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir = Path(self._tmp.name) / "run"
        record = {"kind": "orbit", "config": {"anchor": "/board", "names": NAMES}}
        state.init(self.run_dir, "feature", "ABC-1", ticket_store_record=record)
        f = self.run_dir / ".kinetic" / "state.json"
        raw = json.loads(f.read_text())
        raw["run_id"] = RUN
        f.write_text(json.dumps(raw))
        self.tasks = []
        self.batches = []
        self.fail = None

        def fake(anchor, args, stdin=None):
            if self.fail:
                raise self.fail
            if args[0] == "list":
                return {"tasks": self.tasks, "count": len(self.tasks)}
            if args[0] == "batch":
                self.batches.append(json.loads(stdin)["operations"])
                return {"keys": {}, "results": [], "committed": True, "dry_run": False}
            raise AssertionError(args)

        patcher = mock.patch.object(orbit_store, "call", side_effect=fake)
        self.call = patcher.start()
        self.addCleanup(patcher.stop)

    def test_orbit_anchor_defaults_to_project(self):
        from kinetic import project
        proj = project.create(Path(self._tmp.name).resolve(), "proj")
        project.update(proj, {"tracker": "orbit", "repos": {"api": {"path": "/elsewhere/api"}}})
        store = orbit_store.OrbitTicketStore({})
        with mock.patch("os.getcwd", return_value=str(proj)):
            self.assertEqual(store._anchor({}), str(proj))
            project.update(proj, {"tracker_settings": {"orbit_dir": "/board2"}})
            self.assertEqual(orbit_store.OrbitTicketStore({})._anchor({}), "/board2")

    def test_outside_project_is_board_not_found(self):
        store = orbit_store.OrbitTicketStore({})
        st = {"run_dir": str(self.run_dir)}
        for call_ in (lambda: store._names(st), lambda: store._anchor(st)):
            with self.assertRaises(orbit_store.OrbitStoreError) as caught:
                call_()
            self.assertEqual(caught.exception.code, "BOARD_NOT_FOUND")

    def publish_one(self, tid="orb-a.b", **fields):
        self.tasks.append(task_json(tid, ticket(repo="api", **fields)))

    def test_read_maps_annotation_to_ticket(self):
        self.publish_one(status="running", worktree="/wt")
        t = state.load(self.run_dir)["tickets"]["orb-a.b"]
        self.assertEqual((t["status"], t["repo"], t["worktree"]), ("running", "api", "/wt"))

    def test_read_skips_slice_parents(self):
        self.tasks.append(task_json("orb-a", None, task_type="epic"))
        self.assertEqual(state.load(self.run_dir)["tickets"], {})

    def test_no_op_set_ticket_sends_no_batch(self):
        self.publish_one(status="todo")
        state.set_ticket(self.run_dir, "orb-a.b", repo="api")
        self.assertEqual(self.batches, [])

    def test_set_ticket_on_published_sends_one_batch(self):
        self.publish_one(status="todo")
        state.set_ticket(self.run_dir, "orb-a.b", status="running", worktree="/wt")
        self.assertEqual(len(self.batches), 1)
        self.assertEqual([o["op"] for o in self.batches[0]], ["label", "claim", "annotate"])

    def test_state_json_has_no_tickets(self):
        self.publish_one(status="todo")
        state.set_ticket(self.run_dir, "orb-a.b", status="running")
        raw = json.loads((self.run_dir / ".kinetic" / "state.json").read_text())
        self.assertEqual(raw["tickets"], {})

    def test_new_ticket_is_staged_not_sent(self):
        state.set_ticket(self.run_dir, "01-api-a", repo="api")
        self.assertEqual(self.batches, [])
        staged = json.loads((self.run_dir / ".kinetic" / "tickets.staged.json").read_text())
        self.assertEqual(staged["01-api-a"]["repo"], "api")
        self.assertEqual(state.load(self.run_dir)["tickets"]["01-api-a"]["repo"], "api")

    def test_orbit_failure_leaves_state_json_untouched(self):
        self.publish_one(status="todo")
        before = (self.run_dir / ".kinetic" / "state.json").read_bytes()
        self.fail = orbit_store.OrbitStoreError("BOARD_NOT_FOUND", "no board", "set orbit_dir")
        with self.assertRaises(orbit_store.OrbitStoreError) as caught:
            state.set_ticket(self.run_dir, "orb-a.b", status="running")
        self.assertIn("set orbit_dir", str(caught.exception))
        self.assertEqual((self.run_dir / ".kinetic" / "state.json").read_bytes(), before)

    def test_outside_done_on_human_ticket_is_human_done(self):
        self.tasks.append(task_json("orb-h.1", ticket(kind="human", status="todo"), status="done"))
        t = state.load(self.run_dir)["tickets"]["orb-h.1"]
        self.assertEqual((t["status"], t["done_by"]), ("done", "human"))
        self.assertEqual(self.batches[0][-1]["value"]["status"], "done")

    def test_outside_done_on_agent_ticket_needs_decision(self):
        self.tasks.append(task_json("orb-a.b", ticket(repo="api", status="running"), status="done"))
        t = state.load(self.run_dir)["tickets"]["orb-a.b"]
        self.assertEqual(t["status"], "needs_decision")
        self.assertEqual(t["human_note"], "marked done outside kinetic")

    def test_staged_tickets_are_visible_to_graph_validate(self):
        from kinetic import graph
        stage_two(self.run_dir)
        st = state.load(self.run_dir)
        self.assertEqual(graph.topo_order(st["tickets"]), ["01-api-a", "02-web-b"])
        self.assertEqual(self.batches, [])

    def test_read_marks_stale_lease_stuck(self):
        self.tasks.append(task_json("orb-a.b", ticket(repo="api", status="running"),
                                    status="in_progress", lease_until="2000-01-01T00:00:00Z"))
        self.tasks.append(task_json("orb-a.c", ticket(repo="web", status="running"),
                                    status="in_progress", lease_until="2999-01-01T00:00:00Z"))
        tickets = state.load(self.run_dir)["tickets"]
        self.assertEqual(tickets["orb-a.b"]["liveness"], "stuck")
        self.assertEqual(tickets["orb-a.c"]["liveness"], "alive")


class CallTest(unittest.TestCase):
    def run_result(self, envelope, code=0):
        return subprocess.CompletedProcess([], code, stdout=json.dumps(envelope), stderr="")

    def test_locked_is_retried(self):
        locked = self.run_result({"ok": False, "error": {"code": "LOCKED", "message": "busy"}}, 1)
        ok = self.run_result({"ok": True, "data": {"n": 1}})
        with mock.patch.object(orbit_store.subprocess, "run", side_effect=[locked, locked, ok]), \
                mock.patch.object(orbit_store.time, "sleep") as sleep:
            self.assertEqual(orbit_store.call("/board", ["list"]), {"n": 1})
        self.assertEqual(sleep.call_count, 2)

    def test_other_errors_raise_with_code(self):
        bad = self.run_result({"ok": False, "error": {"code": "NOT_FOUND", "message": "no x"}}, 1)
        with mock.patch.object(orbit_store.subprocess, "run", return_value=bad):
            with self.assertRaises(orbit_store.OrbitStoreError) as caught:
                orbit_store.call("/board", ["show", "x"])
        self.assertEqual(caught.exception.code, "NOT_FOUND")

    def test_missing_binary_names_the_fix(self):
        with mock.patch.object(orbit_store.subprocess, "run", side_effect=FileNotFoundError()):
            with self.assertRaises(orbit_store.OrbitStoreError) as caught:
                orbit_store.call("/board", ["list"])
        self.assertIn("install orbit", str(caught.exception))
        self.assertIn("0.10.16", str(caught.exception))

    def test_depth_refusal_names_the_config_fix(self):
        bad = self.run_result({"ok": False, "error": {
            "code": "INVALID_ARGUMENT",
            "message": "SubTasks are one level deep by default; "
                       "raise board.max_subtask_depth to nest further"}}, 1)
        with mock.patch.object(orbit_store.subprocess, "run", return_value=bad):
            with self.assertRaises(orbit_store.OrbitStoreError) as caught:
                orbit_store.call("/board", ["batch", "--file", "-"])
        self.assertEqual(caught.exception.fix,
                         "add max_subtask_depth = 2 under [board] in the Board's config.toml")


class OrbitHealthDepthTest(unittest.TestCase):
    """health() reports whether the Board's config allows nested SubTasks."""

    DEPTH_FIX = "add max_subtask_depth = 2 under [board] in the Board's config.toml"

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        tmp_path = Path(tmp.name)
        self.bin = tmp_path / "bin"
        self.bin.mkdir()
        self.board = tmp_path / "repo"
        (self.board / ".orbit" / "board").mkdir(parents=True)
        self.config = self.board / ".orbit" / "board" / "config.toml"
        self.settings = tmp_path / "settings.json"
        self.settings.write_text(json.dumps({"tracker": "orbit",
                                             "tracker_settings": {"orbit_dir": str(self.board)}}))
        patcher = mock.patch.dict(os.environ, {"PATH": str(self.bin)})
        patcher.start()
        self.addCleanup(patcher.stop)
        orbit = self.bin / "orbit"
        orbit.write_text("#!/bin/sh\n"
                         'case "$*" in *"batch --help"*) exit 0;; '
                         "*export*) echo '{\"ok\":true,\"data\":{\"schema_version\":3}}'; "
                         "exit 0;; esac\n"
                         "exit 1\n")
        orbit.chmod(0o755)

    def depth_row(self) -> dict:
        rows = orbit_store.OrbitTicketStore({}).health(self.settings)
        return {r["name"]: r for r in rows}["orbit-depth"]

    def test_missing_max_subtask_depth_fails_with_the_fix(self):
        self.config.write_text('[board]\nnon_ready_labels = ["kinetic"]\n')
        row = self.depth_row()
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["fix"], self.DEPTH_FIX)

    def test_depth_one_fails_with_the_fix(self):
        self.config.write_text("[board]\nmax_subtask_depth = 1\n")
        row = self.depth_row()
        self.assertEqual(row["status"], "fail")
        self.assertEqual(row["fix"], self.DEPTH_FIX)

    def test_depth_two_or_more_passes(self):
        for depth in (2, 3):
            self.config.write_text(f"[board]\nmax_subtask_depth = {depth}\n")
            self.assertEqual(self.depth_row()["status"], "pass", depth)

    def test_unreadable_config_warns(self):
        self.config.write_text("[board]\nmax_subtask_depth = 2\n")
        self.config.chmod(0o000)
        self.addCleanup(self.config.chmod, 0o644)
        self.assertEqual(self.depth_row()["status"], "warn")

    def test_absent_config_warns(self):
        self.assertEqual(self.depth_row()["status"], "warn")


GIT_ENV = {"GIT_AUTHOR_NAME": "T", "GIT_AUTHOR_EMAIL": "t@example.com",
           "GIT_COMMITTER_NAME": "T", "GIT_COMMITTER_EMAIL": "t@example.com"}


def make_orbit_run(tmp: Path) -> tuple[Path, Path]:
    """A run on a fresh Orbit Board; returns (run_dir, board anchor)."""
    board = tmp / "repo"
    board.mkdir()
    env = {**os.environ, **GIT_ENV}
    subprocess.run(["git", "init", "-q"], cwd=board, check=True, env=env)
    subprocess.run(["git", "commit", "-q", "--allow-empty", "-m", "init"], cwd=board, check=True, env=env)
    subprocess.run(["orbit", "init", ".", "--json"], cwd=board, check=True, env=env,
                   capture_output=True)
    run_dir = tmp / "run"
    record = {"kind": "orbit", "config": {"anchor": str(board), "names": NAMES}}
    state.init(run_dir, "feature", "ABC-1", ticket_store_record=record)
    return run_dir, board


def orbit_json(board: Path, *args) -> dict:
    out = subprocess.run(["orbit", "--dir", str(board), *args, "--json"], capture_output=True,
                         text=True, env={**os.environ, **GIT_ENV})
    return json.loads(out.stdout)["data"]


def deep_board(board: Path) -> None:
    """Let the Board nest SubTasks (spec > slice > ticket) and hold the `kinetic` label."""
    config = board / ".orbit" / "board" / "config.toml"
    text = config.read_text()
    text = text.replace("[board]", "[board]\nmax_subtask_depth = 2", 1)
    text = re.sub(r"non_ready_labels\s*=\s*\[[^]]*\]",
                  'non_ready_labels = ["kinetic", "ready-for-human"]', text)
    config.write_text(text)


@unittest.skipUnless(orbit_available(), SKIP_REASON)
class OrbitContractTest(TicketStoreContract, unittest.TestCase):
    """The ticket store contract against a real Orbit Board."""

    def make_run(self) -> Path:
        run_dir, self.board = make_orbit_run(self.tmp)
        return run_dir


def stage_two(run_dir: Path) -> None:
    state.set_slices(run_dir, ["01-core"])
    state.set_ticket(run_dir, "01-api-a", repo="api", slice="01-core")
    state.set_ticket(run_dir, "02-web-b", repo="web", slice="01-core",
                     blocked_by=[{"id": "01-api-a", "type": "merge"}])
    issues = run_dir / "issues"
    issues.mkdir(exist_ok=True)
    (issues / "01-api-a.md").write_text(
        "# 01: Build the API\n\n**Status:** todo\n**Repo:** api\n\n"
        "**What to build:** the endpoint.\n\n- [ ] it answers\n")
    (issues / "02-web-b.md").write_text("# 02: Call the API\n\n**What to build:** the page.\n")


@unittest.skipUnless(orbit_available(), SKIP_REASON)
class OrbitPublishTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir, self.board = make_orbit_run(Path(self._tmp.name))
        stage_two(self.run_dir)

    def test_publish_annotates_slice_with_spec_only_when_recorded(self):
        state.set_spec(self.run_dir, "spec.md")
        core = state.publish_tickets(self.run_dir)["01-core"]
        self.assertEqual(orbit_json(self.board, "show", core)["annotations"]["kinetic"],
                         {"spec": "spec.md"})

    def test_publish_without_spec_adds_no_slice_annotation(self):
        core = state.publish_tickets(self.run_dir)["01-core"]
        self.assertNotIn("kinetic", orbit_json(self.board, "show", core).get("annotations") or {})

    def test_publish_creates_tasks_and_rewrites_ids(self):
        mapping = state.publish_tickets(self.run_dir)
        a, b, core = mapping["01-api-a"], mapping["02-web-b"], mapping["01-core"]
        st = state.load(self.run_dir)
        self.assertEqual(set(st["tickets"]), {a, b})
        self.assertEqual(st["tickets"][b]["blocked_by"], [{"id": a, "type": "merge"}])
        self.assertEqual(st["tickets"][a]["slice"], core)
        self.assertEqual(st["slices"], [core])
        shown = orbit_json(self.board, "show", a)
        self.assertEqual(shown["title"], "Build the API")
        self.assertEqual(shown["parent"], core)
        self.assertIn("**What to build:** the endpoint.", shown["body"])
        self.assertNotIn("**Status:**", shown["body"])
        self.assertIn("kinetic", shown["labels"])
        self.assertEqual(orbit_json(self.board, "show", b)["depends_on"], [a])
        self.assertFalse((self.run_dir / ".kinetic" / "tickets.staged.json").exists())
        self.assertFalse((self.run_dir / "issues" / "01-api-a.md").exists())
        self.assertEqual(orbit_json(self.board, "list", "--include-closed")["count"], 3)

    def test_publish_twice_is_safe(self):
        # A crash after Orbit committed but before kinetic saved: both files are as before.
        staged = (self.run_dir / ".kinetic" / "tickets.staged.json").read_text()
        saved = (self.run_dir / ".kinetic" / "state.json").read_text()
        first = state.publish_tickets(self.run_dir)
        (self.run_dir / ".kinetic" / "tickets.staged.json").write_text(staged)
        (self.run_dir / ".kinetic" / "state.json").write_text(saved)
        second = state.publish_tickets(self.run_dir)
        self.assertEqual(first, second)
        self.assertEqual(orbit_json(self.board, "list", "--include-closed")["count"], 3)
        self.assertEqual(set(state.load(self.run_dir)["tickets"]), {first["01-api-a"], first["02-web-b"]})

    def test_dispatch_claims_and_unholds(self):
        a = state.publish_tickets(self.run_dir)["01-api-a"]
        state.set_ticket(self.run_dir, a, status="running", worktree="/wt/a")
        shown = orbit_json(self.board, "show", a)
        st = state.load(self.run_dir)
        self.assertEqual(shown["status"], "in_progress")
        self.assertEqual(shown["claimed_by"], f"kinetic-{st['run_id']}")
        self.assertNotIn("kinetic", shown["labels"])
        self.assertEqual(st["tickets"][a]["worktree"], "/wt/a")
        self.assertEqual(st["tickets"][a]["liveness"], "alive")

    def test_touch_extends_the_lease(self):
        import time
        a = state.publish_tickets(self.run_dir)["01-api-a"]
        state.set_ticket(self.run_dir, a, status="running")
        first = orbit_json(self.board, "show", a)["lease_until"]
        time.sleep(1.1)
        st = state.load(self.run_dir)
        ticket_store.for_state(st).touch(self.run_dir, st, a)
        self.assertGreater(orbit_json(self.board, "show", a)["lease_until"], first)

    def test_human_ticket_mid_build_reaches_the_board(self):
        a = state.publish_tickets(self.run_dir)["01-api-a"]
        human = f"{a}-human-1"
        state.set_ticket(self.run_dir, human, kind="human")
        st = state.load(self.run_dir)
        blockers = st["tickets"][a]["blocked_by"] + [{"id": human, "type": "merge"}]
        state.set_ticket(self.run_dir, a, blocked_by=blockers)
        tickets = state.load(self.run_dir)["tickets"]
        human_ids = [i for i, t in tickets.items() if t.get("kind") == "human"]
        self.assertEqual(len(human_ids), 1)
        self.assertNotEqual(human_ids[0], human)  # it is a Task ID now
        self.assertEqual(tickets[a]["blocked_by"][-1]["id"], human_ids[0])
        self.assertEqual(orbit_json(self.board, "show", a)["depends_on"], [human_ids[0]])
        state.human_done(self.run_dir, human, note="done by hand")  # the provisional id still works
        self.assertEqual(orbit_json(self.board, "show", human_ids[0])["status"], "done")

    def test_ticket_added_after_publish_reaches_the_board(self):
        state.publish_tickets(self.run_dir)
        state.set_ticket(self.run_dir, "03-api-review-1", repo="api", slice="01-core")
        tickets = state.load(self.run_dir)["tickets"]
        self.assertEqual(len(tickets), 3)
        self.assertFalse((self.run_dir / ".kinetic" / "tickets.staged.json").exists())
        self.assertEqual(orbit_json(self.board, "list", "--include-closed")["count"], 4)

    def test_lost_save_after_publish_heals_on_read(self):
        saved = (self.run_dir / ".kinetic" / "state.json").read_text()
        mapping = state.publish_tickets(self.run_dir)
        (self.run_dir / ".kinetic" / "state.json").write_text(saved)  # the save after publish was lost
        st = state.load(self.run_dir)
        self.assertEqual(st["slices"], [mapping["01-core"]])
        a, b = mapping["01-api-a"], mapping["02-web-b"]
        for tid in (a, b):
            state.set_ticket(self.run_dir, tid, status="running")
            state.set_ticket(self.run_dir, tid, status="done")
        self.assertEqual(orbit_json(self.board, "show", mapping["01-core"])["status"], "done")

    def test_slice_closes_when_last_ticket_done(self):
        mapping = state.publish_tickets(self.run_dir)
        a, b, core = mapping["01-api-a"], mapping["02-web-b"], mapping["01-core"]
        state.set_ticket(self.run_dir, a, status="running")
        state.set_ticket(self.run_dir, a, status="done")
        self.assertEqual(orbit_json(self.board, "show", core)["status"], "todo")
        state.set_ticket(self.run_dir, b, status="running")
        state.set_ticket(self.run_dir, b, status="done")
        self.assertEqual(orbit_json(self.board, "show", b)["status"], "done")
        self.assertEqual(orbit_json(self.board, "show", core)["status"], "done")

    def test_breakdown_approval_publishes(self):
        state.record_gate(self.run_dir, "breakdown", True)
        tickets = state.load(self.run_dir)["tickets"]
        self.assertTrue(all(not i.startswith("0") for i in tickets), tickets)
        self.assertEqual(len(tickets), 2)


@unittest.skipUnless(orbit_available(), SKIP_REASON)
class OrbitSpecTreeTest(unittest.TestCase):
    """New-shape run on a depth-2 Board: spec epic > slice Tasks > ticket Tasks."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir, self.board = make_orbit_run(Path(self._tmp.name))
        deep_board(self.board)
        (self.run_dir / "spec.md").write_text("# Spec\n\nBuild the exporter.\n")
        self.spec_id = state.publish_spec(self.run_dir, "Export CSV")["ref"]
        stage_two(self.run_dir)
        self.mapping = state.publish_tickets(self.run_dir)
        self.a = self.mapping["01-api-a"]
        self.b = self.mapping["02-web-b"]
        self.core = self.mapping["01-core"]

    def test_slice_is_a_plain_task_parented_under_the_spec(self):
        shown = orbit_json(self.board, "show", self.core)
        self.assertNotEqual(shown["type"], "epic")
        self.assertEqual(shown["parent"], self.spec_id)
        self.assertEqual(shown["annotations"]["kinetic"],
                         {"role": "slice", "spec": self.spec_id})

    def test_tree_shows_spec_slice_tickets(self):
        self.assertEqual(orbit_json(self.board, "show", self.a)["parent"], self.core)
        self.assertEqual(orbit_json(self.board, "show", self.b)["parent"], self.core)
        tree = orbit_json(self.board, "tree", self.spec_id)["tasks"]
        self.assertEqual({t["id"]: t["depth"] for t in tree},
                         {self.spec_id: 0, self.core: 1, self.a: 2, self.b: 2})

    def test_read_rebuilds_tickets_skipping_spec_and_slice(self):
        tickets = state.load(self.run_dir)["tickets"]
        self.assertEqual(set(tickets), {self.a, self.b})
        self.assertEqual(tickets[self.a]["slice"], self.core)

    def test_last_ticket_closes_the_slice_then_the_spec(self):
        state.set_ticket(self.run_dir, self.a, status="running")
        state.set_ticket(self.run_dir, self.a, status="done")
        self.assertEqual(orbit_json(self.board, "show", self.core)["status"], "todo")
        self.assertEqual(orbit_json(self.board, "show", self.spec_id)["status"], "todo")
        state.set_ticket(self.run_dir, self.b, status="running")
        state.set_ticket(self.run_dir, self.b, status="done")
        self.assertEqual(orbit_json(self.board, "show", self.core)["status"], "done")
        self.assertEqual(orbit_json(self.board, "show", self.spec_id)["status"], "done")

    def test_reopening_a_done_ticket_reopens_slice_and_spec(self):
        for tid in (self.a, self.b):
            state.set_ticket(self.run_dir, tid, status="running")
            state.set_ticket(self.run_dir, tid, status="done")
        self.assertEqual(orbit_json(self.board, "show", self.spec_id)["status"], "done")
        state.set_ticket(self.run_dir, self.b, status="running")
        self.assertEqual(orbit_json(self.board, "show", self.b)["status"], "in_progress")
        self.assertNotEqual(orbit_json(self.board, "show", self.core)["status"], "done")
        self.assertNotEqual(orbit_json(self.board, "show", self.spec_id)["status"], "done")


@unittest.skipUnless(orbit_available(), SKIP_REASON)
class OrbitSpecPublishTest(unittest.TestCase):
    """The run's spec is one epic Task on the Board, rewritten by republishing."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.run_dir, self.board = make_orbit_run(Path(self._tmp.name))
        deep_board(self.board)
        (self.run_dir / "spec.md").write_text("# Spec\n\nBuild the exporter.\n")

    def test_publish_spec_creates_the_epic_task(self):
        result = state.publish_spec(self.run_dir, "Export CSV")
        self.assertTrue(result["published"])
        st = state.load(self.run_dir)
        run_id, task_id = st["run_id"], result["ref"]
        self.assertEqual(st["spec"], task_id)
        self.assertEqual(st["ticket_store"]["config"]["spec_task"], task_id)
        shown = orbit_json(self.board, "show", task_id)
        self.assertEqual((shown["type"], shown["title"]), ("epic", "Export CSV"))
        self.assertIsNone(shown["parent"])
        self.assertEqual(shown["source_key"], f"kinetic:{run_id}:spec")
        self.assertEqual(shown["waiting_on"], "kinetic: spec approval")
        self.assertTrue(shown["body"].splitlines()[0].startswith("generated from spec.md"),
                        shown["body"])
        self.assertIn("Build the exporter.", shown["body"])
        self.assertEqual(shown["annotations"]["kinetic"],
                         {"role": "spec", "run": run_id, "phase": "awaiting-approval"})
        for label in (f"kinetic-run-{run_id}", "kinetic-spec", "kinetic"):
            self.assertIn(label, shown["labels"])

    def test_publish_spec_twice_rewrites_the_body(self):
        first = state.publish_spec(self.run_dir, "Export CSV")["ref"]
        (self.run_dir / "spec.md").write_text("# Spec\n\nBuild the bigger exporter.\n")
        second = state.publish_spec(self.run_dir, "Export CSV")["ref"]
        self.assertEqual(first, second)
        shown = orbit_json(self.board, "show", second)
        self.assertIn("Build the bigger exporter.", shown["body"])
        self.assertEqual(orbit_json(self.board, "list", "--type", "epic")["count"], 1)

    def test_spec_is_an_epic_the_ready_queue_never_offers(self):
        task_id = state.publish_spec(self.run_dir, "Export CSV")["ref"]
        epics = orbit_json(self.board, "list", "--type", "epic")
        self.assertEqual([t["id"] for t in epics["tasks"]], [task_id])
        ready = orbit_json(self.board, "ready")
        self.assertNotIn(task_id, [t["id"] for t in ready["tasks"]])


if __name__ == "__main__":
    unittest.main()
