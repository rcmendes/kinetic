"""Orbit ticket store: a run's tickets are Tasks on an Orbit Board.

The only module in kinetic that knows Orbit. Tickets are SubTasks of their
slice's Parent Task; every kinetic field lives in the Task's `kinetic`
Annotation, which is authoritative, and the Orbit status, claim, waiting
reason and labels are derived from it and written in the same `orbit batch`.
Tickets not yet published are staged in <run_dir>/.kinetic/tickets.staged.json.
"""
from __future__ import annotations

import json
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


def _property_fields(t: dict) -> dict:
    native = {"status", "slice", "assignee", "blocked_by", "after", "related", "ships_with"}
    return {k: v for k, v in _fields(t).items() if k not in native}


def derive(ticket: dict, run_id: str, names: dict) -> dict:
    """Native status and identifying labels for a ticket."""
    labels = {run_label(run_id, names)}
    if ticket.get("kind") == "human" and names["kind"]["human"]:
        labels.add(names["kind"]["human"])
    return {"status": ticket.get("status", "todo"), "labels": labels}


def _links(ticket: dict, run_id: str) -> dict:
    links = {}
    for edge in ticket.get("blocked_by", []):
        links[("blocks", "to", edge["id"])] = {"blocking": True,
                                                   "attributes": {"edge": edge["type"]}}
    for field, kind in (("after", "after"), ("related", "relates-to")):
        for target in ticket.get(field, []):
            links[(kind, "to", target)] = {"blocking": False, "attributes": {}}
    group = ticket.get("ships_with")
    if group:
        links[("ships-with", "external", f"kinetic:{run_id}:ship:{group}")] = {
            "blocking": False, "attributes": {"group": group}}
    return links


def _status_ops(tid: str, before: str, after: str, who: str) -> list:
    if before == after:
        return []
    ops = []

    def transition(name):
        ops.append({"op": "transition", "id": tid, "name": name, "by": who})

    if before == "done" and after == "done_with_concerns":
        transition("concerns")
        return ops
    if before in DONE and not (before in ("done", "done_with_concerns")
                               and after in ("merged", "released")
                               or before == "merged" and after == "released"):
        transition("reopen")
        before = "todo"
    if after == "running":
        if before in PAUSED:
            transition("resume")
        elif before == "in_review":
            transition("request_changes")
        else:
            ops.append({"op": "claim", "id": tid, "by": who})
    elif after == "todo":
        if before in PAUSED:
            transition("resume")
        elif before == "in_review":
            transition("request_changes")
        if before != "todo":
            ops.append({"op": "release", "id": tid, "by": who})
    elif after in ("blocked", "needs_decision"):
        if before in PAUSED:
            transition("resume")
        transition("block" if after == "blocked" else "escalate")
    elif after in DONE:
        if after == "done_with_concerns":
            if before != "running" and before != "in_review":
                if before in PAUSED:
                    transition("resume")
                else:
                    ops.append({"op": "claim", "id": tid, "by": who})
            transition("concerns")
        else:
            if before not in DONE:
                ops.append({"op": "done", "id": tid, "by": who})
            if after in ("merged", "released") and before != "merged":
                transition("merge")
            if after == "released":
                transition("release_artifact")
    else:
        raise OrbitStoreError("INVALID_ARGUMENT", f"unsupported kinetic status '{after}'")
    return ops


def ops_for(tid: str, before: dict | None, after: dict, run_id: str, names: dict) -> list:
    """Native workflow, links and Property updates in one Orbit batch."""
    if before is not None and _fields(before) == _fields(after):
        return []
    if (after.get("kind") != "human" and after.get("status") in DONE
            and (before or {}).get("status") not in DONE):
        raise OrbitStoreError("REVIEW_REQUIRED", "agent completion requires approved reviews",
                              "submit-review, then record-review for spec and standards")
    old, new = _links(before or {}, run_id), _links(after, run_id)
    ops = []
    for key, value in old.items():
        if new.get(key) != value:
            kind, target_field, target = key
            ops.append({"op": "unlink", "id": tid, "type": kind, target_field: target})
    for key, value in new.items():
        if old.get(key) != value:
            kind, target_field, target = key
            ops.append({"op": "link", "id": tid, "type": kind, target_field: target, **value})
    b, a = derive(before or {}, run_id, names), derive(after, run_id, names)
    old_labels = b["labels"] if before is not None else set()
    add, remove = sorted(a["labels"] - old_labels), sorted(old_labels - a["labels"])
    if add or remove:
        ops.append({"op": "label", "id": tid, "add": add, "remove": remove})
    if b["status"] != a["status"]:
        ops += _status_ops(tid, b["status"], a["status"], holder(run_id, tid, "implementer"))
    ops.append({"op": "property", "id": tid, "owner": ANNOTATION_OWNER,
                "value": _property_fields(after)})
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
    metadata = (task.get("properties") or {}).get(ANNOTATION_OWNER)
    if metadata is None:
        metadata = (task.get("annotations") or {}).get(ANNOTATION_OWNER)
    if not metadata:
        return {}
    from kinetic.state import ticket_defaults
    t = {**ticket_defaults(), **metadata}
    t["status"] = task.get("status", t["status"])
    t["slice"] = task.get("parent", t["slice"])
    if task.get("assignee"):
        t["assignee"] = task["assignee"]
    t.update(blocked_by=[], after=[], related=[], ships_with=None)
    for link in task.get("links", []):
        kind, target = link["type"], link["target"]
        attributes = link.get("attributes") or {}
        if kind == "blocks" and isinstance(target, str) and link.get("blocking", True):
            t["blocked_by"].append({"id": target, "type": attributes.get("edge", "merge")})
        elif kind in ("after", "relates-to") and isinstance(target, str):
            t["after" if kind == "after" else "related"].append(target)
        elif kind == "ships-with":
            t["ships_with"] = attributes.get("group")
    if t["status"] == "running":
        t["liveness"] = "stuck" if _lapsed(task.get("lease_until")) else "alive"
    return t


def _role(task: dict):
    """The kinetic role Property: spec, slice or None (a ticket)."""
    metadata = ((task.get("properties") or {}).get(ANNOTATION_OWNER)
                or (task.get("annotations") or {}).get(ANNOTATION_OWNER) or {})
    return metadata.get("role")


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
        return call(self._anchor(st), ["batch", "--file", "-"],
                    stdin=json.dumps({"operations": operations}))

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
        data = call(self._anchor(st), ["list", "--include-closed", "--all",
                                       "--label", label])
        tasks = data.get("issues", [])
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
            metadata = ((task.get("properties") or {}).get(ANNOTATION_OWNER)
                        or (task.get("annotations") or {}).get(ANNOTATION_OWNER) or {})
            from kinetic.state import done_refusal
            verified = done_refusal({**st, "tickets": {task["id"]: t}}, task["id"]) is None
            completed_here = metadata.get("status") in DONE or verified or t.get("done_by") == "human"
            if task.get("status") == "done" and not completed_here:
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
            if op["op"] in ("link", "unlink") and "to" in op:
                op["to"] = ref(op["to"])
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
        """Create every Issue before linking, so non-blocking links may point forward."""
        from kinetic import graph
        run_id, names = st["run_id"], self._names(st)
        ref = self._ref(run_id, set(tickets) | set(new_slices), aliases)
        spec_id = self._spec_id(st) if new_slices else None
        ops = []
        for slice_id in new_slices:
            add = {"op": "add", "key": source_key(run_id, slice_id),
                   "title": ticket_text(run_dir, slice_id)[0],
                   "type": "task",
                   "labels": [run_label(run_id, names)],
                   "properties": {ANNOTATION_OWNER: {"role": "slice"}}}
            if spec_id:
                add["parent"] = spec_id
                add["properties"][ANNOTATION_OWNER]["spec"] = spec_id
            elif st.get("spec"):
                add["properties"][ANNOTATION_OWNER]["spec"] = st["spec"]
            ops.append(add)
        try:
            order = graph.topo_order(tickets)
        except ValueError:
            order = sorted(tickets)  # graph validation reports the cycle; Orbit refuses it
        for pid in order:
            t = tickets[pid]
            title, body = ticket_text(run_dir, pid)
            add = {"op": "add", "key": source_key(run_id, pid), "title": title, "body": body,
                   "type": "sub-task" if t.get("slice") else "task",
                   "labels": sorted(derive(t, run_id, names)["labels"]),
                   "properties": {ANNOTATION_OWNER: _property_fields(t)}}
            if t.get("slice"):
                add["parent"] = ref(t["slice"])
            ops.append(add)
        for pid in order:
            for op in ops_for(ref(pid), None, tickets[pid], run_id, names):
                if op["op"] == "label":
                    continue  # identifying labels were set by add
                if op["op"] in ("link", "unlink") and "to" in op:
                    op["to"] = ref(op["to"])
                ops.append(op)
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
