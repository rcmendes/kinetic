"""Orbit ticket store: a run's tickets are Tasks on an Orbit Board.

The only module in kinetic that knows Orbit. Tickets are SubTasks of their
slice's Parent Task; every kinetic field lives in the Task's `kinetic`
Annotation, which is authoritative, and the Orbit status, claim, waiting
reason and labels are derived from it and written in the same `orbit batch`.
Tickets not yet published are staged in <run_dir>/.kinetic/tickets.staged.json.
"""
from __future__ import annotations

import json
import hashlib
import uuid
import re
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from kinetic.ticket_store import TicketStoreError

ANNOTATION_OWNER = "kinetic"
LOCK_RETRIES = (0.2, 0.4, 0.8)
STAGED = "tickets.staged.json"
SCHEMA_VERSION = 3  # orbit export schema with Annotations and Source Keys
MIN_ORBIT = "0.10.16"  # Batches, Annotations and nested SubTasks (board.max_subtask_depth)
DEPTH_FIX = "add max_subtask_depth = 2 under [board] in the Board's config.toml"
DONE = ("done", "done_with_concerns", "merged", "released")
PAUSED = ("needs_decision", "blocked")
SPEC_WAITING = "kinetic: spec approval"  # set while the spec gate is open
SPEC_GENERATED = "generated from spec.md; edit the file, then publish-spec again"
# The spec Task's `phase` Annotation for each sync_spec event.
SPEC_PHASES = {"gate-approved": "approved", "gate-rejected": "revising",
               "building": "building"}
# Keys read() adds that are not ticket fields and are never written back.
DERIVED = ("liveness",)


class OrbitStoreError(TicketStoreError):
    def __init__(self, code: str, message: str, fix: str = ""):
        self.code, self.message, self.fix = code, message, fix
        super().__init__(f"orbit {code}: {message}" + (f" (fix: {fix})" if fix else ""))


def call(anchor: str, args: list, stdin: str | None = None) -> dict:
    """Run `orbit --dir <anchor> <args> --json`; return its data or raise."""
    cmd = ["orbit", "--dir", str(anchor), *args, "--json"]
    # Run from the anchor so Orbit's "current Repository" never depends on kinetic's cwd.
    cwd = str(anchor) if Path(anchor).is_dir() else None
    for delay in (*LOCK_RETRIES, None):
        try:
            done = subprocess.run(cmd, input=stdin, capture_output=True, text=True, cwd=cwd)
        except FileNotFoundError:
            raise OrbitStoreError("NOT_INSTALLED", "the orbit command was not found",
                                  f"install orbit with Batches and Annotations ({MIN_ORBIT} or later)")
        try:
            envelope = json.loads(done.stdout or "{}")
        except ValueError:
            raise OrbitStoreError("BAD_OUTPUT", (done.stderr or done.stdout).strip()[:500])
        if envelope.get("ok"):
            return envelope.get("data") or {}
        error = envelope.get("error") or {}
        code = error.get("code") or "ORBIT_FAILED"
        if code == "LOCKED" and delay is not None:
            time.sleep(delay)
            continue
        message = error.get("message") or done.stderr.strip()[:500]
        raise OrbitStoreError(code, message, _fix(code, message))
    raise AssertionError("unreachable")


def _fix(code: str, message: str = "") -> str:
    if code == "INVALID_ARGUMENT" and "board.max_subtask_depth" in message:
        return DEPTH_FIX
    return {
        "LOCKED": "another orbit command holds the Board lock; retry",
        "BOARD_NOT_FOUND": "set tracker_settings.orbit_dir or run orbit init in the repo",
    }.get(code, "")


def run_label(run_id: str, names: dict) -> str:
    return names["run"].replace("<run id>", run_id)


def holder(run_id: str) -> str:
    return f"kinetic-{run_id}"


def _fields(t: dict) -> dict:
    return {k: v for k, v in t.items() if k not in DERIVED}


def derive(ticket: dict, run_id: str, names: dict) -> dict:
    """Orbit status, claim holder, waiting reason and labels for a ticket."""
    status = ticket.get("status", "todo")
    human = ticket.get("kind") == "human"
    labels = {run_label(run_id, names)}
    if ticket.get("ships_with"):
        labels.add(names["ships_with"].replace("<group>", ticket["ships_with"]))
    if human and names["kind"]["human"]:
        labels.add(names["kind"]["human"])
    if status in DONE:
        return {"status": "done", "claim": None, "waiting_on": None, "labels": labels}
    if status == "todo":
        if not human:
            labels.add(names["hold"])
        return {"status": "todo", "claim": None, "waiting_on": None, "labels": labels}
    waiting = None
    if status in PAUSED:
        waiting = ticket.get("human_note") or f"kinetic: {status}"
    return {"status": "in_progress", "claim": holder(run_id), "waiting_on": waiting,
            "labels": labels}


def ops_for(tid: str, before: dict | None, after: dict, run_id: str, names: dict) -> list:
    """`orbit batch` operations moving a published ticket from before to after."""
    if before is not None and _fields(before) == _fields(after):
        return []
    b = derive(before, run_id, names) if before is not None else None
    a = derive(after, run_id, names)
    ops: list = []
    who = holder(run_id)
    if b and b["status"] == "done" and a["status"] != "done":
        ops.append({"op": "reopen", "id": tid})
        b = {**b, "status": "todo", "claim": None}
    if b and b["waiting_on"] and not a["waiting_on"]:
        ops.append({"op": "update", "id": tid, "clear_waiting": True})
    old_labels = b["labels"] if b else set()
    add, remove = sorted(a["labels"] - old_labels), sorted(old_labels - a["labels"])
    if add or remove:
        label = {"op": "label", "id": tid}
        if add:
            label["add"] = add
        if remove:
            label["remove"] = remove
        ops.append(label)
    old_deps = {e["id"] for e in (before or {}).get("blocked_by", [])}
    new_deps = {e["id"] for e in after.get("blocked_by", [])}
    ops += [{"op": "depend", "id": tid, "on": d} for d in sorted(new_deps - old_deps)]
    ops += [{"op": "undepend", "id": tid, "on": d} for d in sorted(old_deps - new_deps)]
    # Claim when entering in_progress, and renew when resuming a paused ticket
    # (its Lease may have lapsed while it waited).
    resuming = (before or {}).get("status") in PAUSED and after.get("status") == "running"
    if a["claim"] and (not b or b["claim"] != a["claim"] or resuming):
        ops.append({"op": "claim", "id": tid, "by": who})
    if a["waiting_on"] and (not b or b["waiting_on"] != a["waiting_on"]):
        ops.append({"op": "update", "id": tid, "waiting_on": a["waiting_on"]})
    if b and b["status"] == "in_progress" and a["status"] == "todo":
        ops.append({"op": "release", "id": tid, "by": who, "force": True})
    if a["status"] == "done" and (not b or b["status"] != "done"):
        ops.append({"op": "done", "id": tid, "by": who})
    ops.append({"op": "annotate", "id": tid, "owner": ANNOTATION_OWNER, "value": _fields(after)})
    return ops


def source_key(run_id: str, provisional_id: str) -> str:
    return f"kinetic:{run_id}:{provisional_id}"


def _rewrite(t: dict, ids: dict) -> dict:
    """Replace provisional ids in a ticket's references with Task IDs."""
    if not ids:
        return t
    t = dict(t)
    if t.get("slice") in ids:
        t["slice"] = ids[t["slice"]]
    t["blocked_by"] = [dict(e, id=ids.get(e["id"], e["id"])) for e in t.get("blocked_by", [])]
    for key in ("after", "related"):
        if t.get(key):
            t[key] = [ids.get(x, x) for x in t[key]]
    return t


_HEADER = re.compile(r"^\*\*(Status|Type|Repo|Slice|Blocked by|After|Ships with|Related|Assignee):\*\*")
_TITLE = re.compile(r"^#\s+(?:\d+:\s*)?(.*\S)\s*$")


def ticket_text(run_dir, provisional_id: str) -> tuple[str, str]:
    """Title and body from <run_dir>/issues/<id>.md, without board-owned header lines."""
    p = Path(run_dir) / "issues" / f"{provisional_id}.md"
    if not p.is_file():
        return provisional_id, ""
    lines = p.read_text().splitlines()
    title = provisional_id
    if lines and _TITLE.match(lines[0]):
        title, lines = _TITLE.match(lines[0]).group(1), lines[1:]
    body = "\n".join(line for line in lines if not _HEADER.match(line)).strip()
    return title, re.sub(r"\n{3,}", "\n\n", body) + ("\n" if body else "")


def _lapsed(lease_until) -> bool:
    if not lease_until:
        return False
    try:
        when = datetime.fromisoformat(str(lease_until).replace("Z", "+00:00"))
    except ValueError:
        return False
    return when < datetime.now(timezone.utc)


def to_ticket(task: dict) -> dict:
    t = dict((task.get("annotations") or {}).get(ANNOTATION_OWNER) or {})
    if t.get("status") == "running":
        t["liveness"] = "stuck" if _lapsed(task.get("lease_until")) else "alive"
    return t


def _role(task: dict):
    """The kinetic role Annotation: "spec", "slice" or None (a ticket)."""
    return ((task.get("annotations") or {}).get(ANNOTATION_OWNER) or {}).get("role")


def _is_slice(task: dict, label: str) -> bool:
    """A slice Task: role "slice" (spec-Task runs) or, for old runs, an epic
    carrying the run label."""
    if _role(task) == "slice":
        return True
    return (task.get("type") == "epic" and _role(task) != "spec"
            and label in (task.get("labels") or []))


class OrbitTicketStore:
    def __init__(self, config: dict):
        self.config = config

    # -- configuration resolved on first use, then kept in state.json --
    @staticmethod
    def _project(st: dict) -> Path:
        """The project of this run (from its run dir), else of the cwd."""
        from kinetic import project
        try:
            return project.require(st.get("run_dir"))
        except project.NoProject as e:
            raise OrbitStoreError("BOARD_NOT_FOUND", str(e), "run from inside a kinetic project")

    def _names(self, st: dict) -> dict:
        names = self.config.get("names")
        if not names:
            from kinetic import tracker
            cfg = self._project(st) / ".kinetic" / "config.json"
            names = tracker.resolve(settings=cfg)["names"]
        else:
            # Names persisted before newer tracker adapter defaults (e.g.
            # "spec") lack them; re-merge over the same base resolve() uses.
            from kinetic import tracker
            path = Path(tracker.ROOT) / "trackers" / "orbit.md"
            names = tracker._merge(tracker.adapter_defaults(path), names)
        self.config["names"] = names
        return names

    def _spec_id(self, st: dict):
        """The run's spec Task ID: from the saved config, else rebuilt from the Board
        (a save lost after `publish_spec` heals here, the way slices heal on read)."""
        if self.config.get("spec_task"):
            return self.config["spec_task"]
        data = call(self._anchor(st), ["list", "--include-closed", "--all", "--label",
                                       run_label(st["run_id"], self._names(st))])
        for task in data.get("tasks", []):
            if _role(task) == "spec":
                self.config["spec_task"] = task["id"]
                return task["id"]
        return None

    def _anchor(self, st: dict) -> str:
        if not self.config.get("anchor"):
            from kinetic import tracker
            proj = self._project(st)
            settings = tracker.resolve(settings=proj / ".kinetic" / "config.json")["settings"]
            anchor = settings.get("orbit_dir") or proj
            self.config["anchor"] = str(Path(anchor).expanduser())
        return self.config["anchor"]

    def _batch(self, st: dict, operations: list) -> dict:
        if not operations:
            return {"keys": {}, "results": []}
        from kinetic.state import atomic_write, internal
        anchor = self._anchor(st)
        journal = internal(st["run_dir"]) / "orbit-write.json"
        digest = hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()
        pending = json.loads(journal.read_text()) if journal.exists() else None
        if pending and pending["digest"] != digest:
            raise OrbitStoreError("PENDING_WRITE", "an interrupted Orbit write must be retried first")
        if not pending:
            cache = internal(st["run_dir"]) / "orbit-feed.json"
            snapshot = json.loads(cache.read_text()).get("issues", {}) if cache.exists() else {}
            versions = {}
            guarded = []
            for operation in operations:
                op = dict(operation)
                tid = op.get("id")
                if tid and not tid.startswith("key:"):
                    if tid not in versions:
                        versions[tid] = snapshot.get(tid, {}).get("updated_at") or call(anchor, ["show", tid])["updated_at"]
                    op["if_version"] = versions[tid]
                guarded.append(op)
            pending = {"digest": digest, "key": "kinetic-" + uuid.uuid4().hex,
                       "operations": guarded}
            atomic_write(journal, json.dumps(pending))
        for attempt in range(3):
            try:
                result = call(anchor, ["batch", "--file", "-", "--idempotency-key", pending["key"]],
                              stdin=json.dumps({"operations": pending["operations"]}))
            except OrbitStoreError as error:
                if error.code != "VERSION_CONFLICT" or attempt == 2:
                    raise
                # A rejected batch made no changes. Refresh guards before retrying;
                # property writes remain scoped to kinetic's namespace.
                versions = {op["id"]: call(anchor, ["show", op["id"]])["updated_at"]
                            for op in pending["operations"] if "if_version" in op}
                for op in pending["operations"]:
                    if "if_version" in op:
                        op["if_version"] = versions[op["id"]]
                atomic_write(journal, json.dumps(pending))
            else:
                journal.unlink()
                return result

    def _issues(self, st: dict) -> list:
        """Resume a durable feed snapshot; capture the cursor before the full read."""
        from kinetic.state import atomic_write, internal
        cache = internal(st["run_dir"]) / "orbit-feed.json"
        anchor = self._anchor(st)
        label = run_label(st["run_id"], self._names(st))
        saved = json.loads(cache.read_text()) if cache.exists() else None
        if saved and saved.get("anchor") != anchor:
            saved = None
        args = ["changes"] + (["--since", saved["cursor"]] if saved else [])
        try:
            feed = call(anchor, args)
        except OrbitStoreError as error:
            if not saved or error.code not in ("INVALID_ARGUMENT", "INVALID_CURSOR"):
                raise
            saved = None
            feed = call(anchor, ["changes"])
        if saved is None or any(e.get("kind") == "board.changed" for e in feed["events"]):
            snapshot = {}
            cursor = None
            while True:
                args = ["list", "--include-closed", "--all", "--label", label]
                if cursor:
                    args += ["--cursor", cursor]
                page = call(anchor, args)
                snapshot.update((issue["id"], issue) for issue in page.get("issues", []))
                cursor = page.get("next_cursor")
                if not cursor:
                    break
        else:
            snapshot = saved["issues"]
            for tid in {e["issue"] for e in feed["events"] if e.get("issue")}:
                try:
                    issue = call(anchor, ["show", tid])
                except OrbitStoreError as error:
                    if error.code != "NOT_FOUND":
                        raise
                    snapshot.pop(tid, None)
                    continue
                if label in issue.get("labels", []):
                    snapshot[tid] = issue
                else:
                    snapshot.pop(tid, None)
        atomic_write(cache, json.dumps({"anchor": anchor, "cursor": feed["cursor"], "issues": snapshot}))
        return list(snapshot.values())

    # -- staged tickets --
    def stage_path(self, run_dir) -> Path:
        from kinetic.state import internal
        return internal(run_dir) / STAGED

    def _staged(self, run_dir) -> dict:
        p = self.stage_path(run_dir)
        return json.loads(p.read_text()) if p.is_file() else {}

    def _save_staged(self, run_dir, staged: dict) -> None:
        from kinetic.state import atomic_write
        p = self.stage_path(run_dir)
        if staged:
            atomic_write(p, json.dumps(staged, indent=2, sort_keys=True))
        elif p.exists():
            p.unlink()

    # -- the interface --
    def read(self, run_dir, st: dict) -> dict:
        staged = self._staged(run_dir)
        published = {}
        if self.config.get("published") or not staged:
            published = self._read_published(st)
        return {**published, **staged}

    def _read_published(self, st: dict) -> dict:
        if not self.config.get("anchor") and not st.get("repos"):
            return {}  # nothing published before the run has a Board
        run_id, names = st["run_id"], self._names(st)
        label = run_label(run_id, names)
        tasks = self._issues(st)
        prefix = source_key(run_id, "")
        ids = {task["source_key"][len(prefix):]: task["id"] for task in tasks
               if (task.get("source_key") or "").startswith(prefix)
               and _role(task) != "spec"}
        # Provisional ids keep resolving after publish, and run-level records are
        # rebuilt from the Board, so a save lost right after a publish heals here.
        st["ticket_aliases"] = ids
        if ids:
            self.config["published"] = True
            self.config["published_slices"] = sorted(
                task["id"] for task in tasks
                if _is_slice(task, label) and task["id"] in ids.values())
            st["slices"] = [ids.get(s, s) for s in st.get("slices", [])]
        tickets, fixes = {}, []
        for task in tasks:
            if _is_slice(task, label) or _role(task) == "spec":
                continue
            t = _rewrite(to_ticket(task), ids)
            if not t:
                continue
            if task.get("status") == "done" and t.get("status") not in DONE:
                # Done in Orbit but not in kinetic: someone finished it outside kinetic.
                actual = dict(t, status="done")
                if t.get("kind") == "human":
                    t.update(status="done", done_by="human")
                else:  # no passing verify, so a person decides what happens next
                    t.update(status="needs_decision", human_note="marked done outside kinetic")
                fixes += ops_for(task["id"], actual, t, run_id, names)
            tickets[task["id"]] = t
        spec = next((t for t in tasks if _role(t) == "spec"), None)
        if spec:
            self.config["spec_task"] = spec["id"]
            slices = [t for t in tasks if _is_slice(t, label) and t["id"] in ids.values()]
            if (slices and spec.get("status") == "todo"
                    and all(t.get("status") == "done" for t in slices)):
                # Every slice is done but the spec Task is not: a missed close heals here.
                fixes.append({"op": "done", "id": spec["id"], "by": holder(run_id)})
        if fixes:
            self._batch(st, fixes)
        return tickets

    def write(self, run_dir, st: dict, before: dict, after: dict) -> None:
        staged = self._staged(run_dir)
        run_id, names = st["run_id"], self._names(st)
        # After the first publish, a new ticket (a NEEDS_HUMAN ticket, a review fix)
        # goes to the Board in this same Batch instead of waiting in the staged file.
        published = bool(self.config.get("published"))
        aliases = st.get("ticket_aliases") or {}
        new, changes, staged_changed = {}, [], False
        for tid, t in after.items():
            if tid not in before and tid not in staged and published:
                new[tid] = t
            elif tid in staged or tid not in before:
                if staged.get(tid) != _fields(t):
                    staged[tid] = _fields(t)
                    staged_changed = True
            else:
                changes += ops_for(tid, before[tid], t, run_id, names)
        ref = self._ref(run_id, set(new), aliases)
        for op in changes:
            if op["op"] in ("depend", "undepend"):
                op["on"] = ref(op["on"])
        operations = (self._add_ops(run_dir, st, new, [], aliases)[0] + changes
                      + self._close_slices(st, staged, before, after))
        self._batch(st, operations)
        if staged_changed:
            self._save_staged(run_dir, staged)

    @staticmethod
    def _ref(run_id: str, local: set, aliases: dict):
        """A Batch reference: `key:` for Tasks added in this Batch, else the Task ID."""
        def ref(x: str) -> str:
            if x in local:
                return f"key:{source_key(run_id, x)}"
            return aliases.get(x, x)
        return ref

    def _add_ops(self, run_dir, st: dict, tickets: dict, new_slices: list, aliases: dict):
        """`add` operations for slices and tickets not yet on the Board; returns (ops, order)."""
        from kinetic import graph
        run_id, names = st["run_id"], self._names(st)
        ref = self._ref(run_id, set(tickets) | set(new_slices), aliases)
        spec_id = self._spec_id(st) if new_slices else None
        ops = []
        for s in new_slices:
            add = {"op": "add", "key": source_key(run_id, s),
                   "title": ticket_text(run_dir, s)[0],
                   "labels": [run_label(run_id, names)]}
            if spec_id:
                # New shape: a plain Task nested under the run's spec Task.
                add["parent"] = spec_id
                add["annotations"] = {ANNOTATION_OWNER: {"role": "slice", "spec": spec_id}}
            else:
                add["type"] = "epic"
                # optional: Boards and runs without a recorded spec never see this Annotation
                if st.get("spec"):
                    add["annotations"] = {ANNOTATION_OWNER: {"spec": st["spec"]}}
            ops.append(add)
        try:
            order = graph.topo_order(tickets)
        except ValueError:
            order = sorted(tickets)  # graph validate reports the cycle; Orbit refuses it
        for pid in order:
            t = tickets[pid]
            title, body = ticket_text(run_dir, pid)
            fresh = dict(t, status="todo")
            add = {"op": "add", "key": source_key(run_id, pid), "title": title, "body": body,
                   "labels": sorted(derive(fresh, run_id, names)["labels"]),
                   "depends_on": [ref(e["id"]) for e in t.get("blocked_by", [])],
                   "annotations": {ANNOTATION_OWNER: _fields(t)}}
            if t.get("slice"):
                add["parent"] = ref(t["slice"])
            ops.append(add)
            if t.get("status", "todo") != "todo":
                ops += [o for o in ops_for(ref(pid), fresh, t, run_id, names)
                        if o["op"] not in ("depend", "undepend")]
        return ops, order

    def _close_slices(self, st: dict, staged: dict, before: dict, after: dict) -> list:
        """Close a slice's Task in the Batch that finishes its last SubTask, and
        the spec Task in the Batch that finishes the last slice."""
        published = set(self.config.get("published_slices", []))
        by_slice: dict = {}
        for tid, t in after.items():
            if tid not in staged and t.get("slice") in published:
                by_slice.setdefault(t["slice"], []).append(tid)
        ops = []
        for slice_id, tids in sorted(by_slice.items()):
            finished = all(after[t].get("status") in DONE for t in tids)
            just = any((before.get(t) or {}).get("status") not in DONE for t in tids)
            if finished and just:
                ops.append({"op": "done", "id": slice_id, "by": holder(st["run_id"])})
        # Orbit refuses `done` on a Task with open SubTasks, so the spec closes
        # only once every published slice's tickets are done. Decompose keeps
        # every published slice non-empty (graph.validate refuses a slice no
        # ticket references), so each one shows up in by_slice; an empty slice
        # would make this check never pass.
        if ops and all(
                by_slice.get(sid)
                and all(after[t].get("status") in DONE for t in by_slice[sid])
                for sid in published):
            spec_id = self._spec_id(st)
            if spec_id:
                ops.append({"op": "done", "id": spec_id, "by": holder(st["run_id"])})
        return ops

    def spec_ops(self, run_dir, st: dict, title: str, body: str) -> list:
        """Batch operations publishing the run's spec Task: one `add` whose Source Key
        matches the Task when it exists, so the ops that follow rewrite it either way."""
        run_id, names = st["run_id"], self._names(st)
        key = source_key(run_id, "spec")
        ref = f"key:{key}"
        labels = sorted({run_label(run_id, names), names["spec"], names["hold"]})
        annotation = {"role": "spec", "run": run_id, "phase": "awaiting-approval"}
        text = f"{SPEC_GENERATED}\n\n{body}"
        return [
            {"op": "add", "key": key, "title": title, "type": "epic",
             "labels": labels, "waiting_on": SPEC_WAITING, "body": text,
             "annotations": {ANNOTATION_OWNER: annotation}},
            {"op": "update", "id": ref, "title": title, "body": text,
             "waiting_on": SPEC_WAITING},
            {"op": "annotate", "id": ref, "owner": ANNOTATION_OWNER, "value": annotation},
            {"op": "label", "id": ref, "add": labels},
        ]

    def publish_spec(self, run_dir, st: dict, title: str, body: str) -> dict:
        """Publish the spec as the run's epic spec Task; republishing rewrites it."""
        anchor = Path(self._anchor(st))
        depth = _max_depth(anchor)
        if depth is not None and depth < 2:
            raise OrbitStoreError("INVALID_ARGUMENT",
                                  "the Board nests SubTasks one level deep, but "
                                  "the spec Task holds slices and their tickets "
                                  f"(board.max_subtask_depth is {depth})",
                                  DEPTH_FIX)
        keys = self._batch(st, self.spec_ops(run_dir, st, title, body)).get("keys", {})
        ref = keys[source_key(st["run_id"], "spec")]
        self.config["spec_task"] = ref
        return {"ref": ref, "published": True}

    def sync_spec(self, run_dir, st: dict, event: str) -> None:
        """Mirror a spec lifecycle event on the spec Task: the gate closing clears
        `waiting_on` and the `phase` Annotation tracks the run. The spec Task is
        closed by _close_slices, in the Batch that finishes the last slice."""
        spec_id = self._spec_id(st)
        if not spec_id:
            return
        phase = SPEC_PHASES.get(event)
        if phase is None:
            raise OrbitStoreError(
                "INVALID_ARGUMENT", f"unknown spec sync event '{event}'",
                "expected one of: " + ", ".join(sorted(SPEC_PHASES)))
        self._batch(st, [
            {"op": "update", "id": spec_id, "clear_waiting": True},
            {"op": "annotate", "id": spec_id, "owner": ANNOTATION_OWNER,
             "value": {"role": "spec", "run": st["run_id"], "phase": phase}},
        ])

    def publish(self, run_dir, st: dict) -> dict:
        """Send every staged ticket (and new slices) in one Batch; returns provisional -> Task ID."""
        staged = self._staged(run_dir)
        if not staged:
            return {}
        run_id = st["run_id"]
        done_slices = set(self.config.get("published_slices", []))
        new_slices = [s for s in st.get("slices", []) if s not in done_slices]
        ops, order = self._add_ops(run_dir, st, staged, new_slices,
                                   st.get("ticket_aliases") or {})
        keys = self._batch(st, ops).get("keys", {})
        mapping = {pid: keys[source_key(run_id, pid)] for pid in [*new_slices, *order]}
        self.config["published"] = True
        self.config["published_slices"] = sorted(done_slices | {mapping[s] for s in new_slices})
        # The Board now holds them; a save lost after this point heals on the next read.
        self._save_staged(run_dir, {})
        for pid in order:
            (Path(run_dir) / "issues" / f"{pid}.md").unlink(missing_ok=True)
        return mapping

    def touch(self, run_dir, st: dict, ticket_id: str) -> None:
        call(self._anchor(st), ["heartbeat", ticket_id, "--by", holder(st["run_id"])])

    def health(self, settings: Path) -> list:
        """Doctor rows: binary with Batches, Board found, export schema, hold label."""
        import shutil
        from kinetic import tracker

        def row(name, status, fix=""):
            return {"name": name, "status": status, "fix": fix}

        resolved = tracker.resolve(settings=settings)
        anchor = Path(resolved["settings"].get("orbit_dir") or Path(settings).parent.parent).expanduser()
        hold = resolved["names"]["hold"]
        install = f"install orbit with Batches and Annotations ({MIN_ORBIT} or later)"
        later = "fix orbit-binary first"
        if not shutil.which("orbit") or subprocess.run(
                ["orbit", "batch", "--help"], capture_output=True).returncode != 0:
            return [row("orbit-binary", "fail", install), row("orbit-board", "fail", later),
                    row("orbit-schema", "fail", later), row("orbit-depth", "fail", later),
                    row("orbit-hold-label", "warn", later)]
        out = [row("orbit-binary", "pass")]
        try:
            schema = call(str(anchor), ["export"]).get("schema_version")
        except OrbitStoreError:
            fix = "set tracker_settings.orbit_dir or run orbit init in the repo"
            return out + [row("orbit-board", "fail", fix),
                          row("orbit-schema", "fail", "fix orbit-board first"),
                          row("orbit-depth", "fail", "fix orbit-board first"),
                          row("orbit-hold-label", "warn", "fix orbit-board first")]
        out.append(row("orbit-board", "pass"))
        out.append(row("orbit-schema", "pass") if schema == SCHEMA_VERSION else
                   row("orbit-schema", "fail",
                       f"orbit export schema_version is {schema}, kinetic needs {SCHEMA_VERSION}"))
        depth = _max_depth(anchor)
        if depth is None:
            out.append(row("orbit-depth", "warn",
                           "could not read the Board's config.toml"))
        else:
            out.append(row("orbit-depth", "pass") if depth >= 2 else
                       row("orbit-depth", "fail", DEPTH_FIX))
        out.append(row("orbit-hold-label", "pass") if hold in _hold_labels(anchor) else
                   row("orbit-hold-label", "warn",
                       f'add "{hold}" to non_ready_labels in the Board\'s config.toml'))
        return out


def _config_candidates(anchor: Path) -> list:
    """Wherever the Board layout keeps its config.toml."""
    return [anchor / ".orbit" / "board" / "config.toml", anchor / ".orbit" / "config.toml",
            anchor / "config.toml"]


def _config_texts(anchor: Path):
    """The text of each existing, readable config.toml candidate."""
    for candidate in _config_candidates(anchor):
        if candidate.is_file():
            try:
                yield candidate.read_text()
            except OSError:
                continue


def _max_depth(anchor: Path) -> int | None:
    """board.max_subtask_depth (Orbit's default is 1); None when no config is readable."""
    saw_config = False
    for text in _config_texts(anchor):
        saw_config = True
        m = re.search(r"max_subtask_depth\s*=\s*(\d+)", text)
        if m:
            return int(m.group(1))
    return 1 if saw_config else None


def _hold_labels(anchor: Path) -> list:
    """non_ready_labels from the Board's config.toml, wherever the layout keeps it."""
    for text in _config_texts(anchor):
        m = re.search(r"non_ready_labels\s*=\s*\[(.*?)\]", text, re.S)
        if m:
            return re.findall(r'"([^"]+)"', m.group(1))
    return []


def make(config: dict) -> OrbitTicketStore:
    return OrbitTicketStore(config)
