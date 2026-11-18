"""Orbit ticket store: native Issues, workflow states, hierarchy and typed links.

Kinetic-only metadata lives in the kinetic Property. Published tickets use
Orbit as their source of truth; unpublished tickets are staged in
<run_dir>/.kinetic/tickets.staged.json.
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
MIN_ORBIT = "0.11.0"
REQUIRED_CAPABILITIES = {"workflow", "policy", "issue-types", "hierarchy", "links", "properties",
                         "assignee", "lease", "batch", "idempotency", "profiles", "setup"}
NATIVE_STATUSES = {"todo", "running", "needs_decision", "blocked", "done_with_concerns",
                   "merged", "done", "released", "in_review", "cancelled"}
DONE = ("done", "done_with_concerns", "merged", "released")
PAUSED = ("needs_decision", "blocked")
SPEC_GENERATED = "generated from spec.md; edit the file, then publish-spec again"
# The owned spec Issue's phase Property for each sync_spec event.
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
                                  f"install orbit {MIN_ORBIT} or later with contract 1 support")
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
    return {
        "LOCKED": "another orbit command holds the Board lock; retry",
        "BOARD_NOT_FOUND": "set tracker_settings.orbit_dir or run orbit init in the repo",
    }.get(code, "")


def negotiate(anchor: str) -> dict:
    """Check the kernel contract; optional Board modules are not required."""
    data = call(anchor, ["capabilities"])
    board = data.get("board") or {}
    versions = (data.get("contract", ""), board.get("contract", ""))
    if (any(not re.fullmatch(r"1(?:\.\d+){0,2}", str(version)) for version in versions)
            or board.get("compatible") is not True):
        raise OrbitStoreError("CONTRACT_VERSION_UNSUPPORTED",
                              f"kinetic needs Orbit contract 1; binary={versions[0]}, Board={versions[1]}",
                              "upgrade orbit or migrate the Board to contract 1")
    missing = sorted(REQUIRED_CAPABILITIES - set(data.get("capabilities") or []))
    if missing:
        raise OrbitStoreError("MISSING_CAPABILITIES", "missing required capabilities: " + ", ".join(missing),
                              f"install orbit {MIN_ORBIT} or later with these capabilities")
    return data


def run_label(run_id: str, names: dict) -> str:
    return names["run"].replace("<run id>", run_id)


def holder(run_id: str, ticket_id: str | None = None, role: str = "orchestrator") -> str:
    parts = [run_id, role, *([ticket_id] if ticket_id else [])]
    if any(not re.fullmatch(r"[A-Za-z0-9_.-]+", part) for part in parts):
        raise ValueError("Orbit identity parts must contain only letters, numbers, dots, underscores or hyphens")
    return "kinetic-" + "-".join(parts)


def _fields(t: dict) -> dict:
    return {k: v for k, v in t.items() if k not in DERIVED}


def _property_fields(t: dict) -> dict:
    native = {"status", "slice", "assignee", "blocked_by", "after", "related", "ships_with", "touches",
              "refs"}
    return {k: v for k, v in _fields(t).items() if k not in native}


def derive(ticket: dict, run_id: str, names: dict) -> dict:
    """Native status and identifying labels for a ticket."""
    labels = {run_label(run_id, names)}
    if ticket.get("kind") == "human" and names["kind"]["human"]:
        labels.add(names["kind"]["human"])
    return {"status": ticket.get("status", "todo"), "labels": labels}


def _component(name) -> str | None:
    """A lowercase slug Orbit accepts as a Component (letters, digits, `-`, at most 32)."""
    slug = re.sub(r"[^a-z0-9]+", "-", str(name or "").lower()).strip("-")[:32].strip("-")
    return slug or None


def _repository(name) -> str | None:
    """The repository name if Orbit would accept it as a Repository, else None."""
    return name if isinstance(name, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}", name) else None


def _placement(repo, slice_title=None) -> dict:
    """Orbit's native repository and component fields; a name Orbit would refuse is omitted,
    because one bad name fails the whole batch."""
    fields = {"repository": _repository(repo), "component": _component(slice_title)}
    return {k: v for k, v in fields.items() if v}


def _board_priorities(anchor) -> list | None:
    """The Board's ordered priority names, read from its config.toml, or None if not found.
    Orbit refuses a name outside this list, and one refusal fails the whole batch."""
    for path in (Path(anchor) / ".orbit" / "board" / "config.toml", Path(anchor) / "config.toml"):
        try:
            text = path.read_text()
        except OSError:
            continue
        found = re.search(r"^priorities\s*=\s*\[([^\]]*)\]", text, re.M)
        if found:
            names = re.findall(r"\"([^\"]+)\"|'([^']+)'", found.group(1))
            return [a or b for a, b in names] or None
    return None


def _priority_name(rank: int, names) -> str | None:
    """Kinetic's rank 0-3 spread over the Board's names, most important first."""
    if not names:
        return None
    return names[round(rank * (len(names) - 1) / 3)]


def _links(ticket: dict, run_id: str) -> dict:
    links = {}
    for edge in ticket.get("blocked_by", []):
        links[("blocks", "to", edge["id"])] = {"blocking": True,
                                                   "attributes": {"edge": edge["type"]}}
    for field, kind in (("after", "after"), ("related", "relates-to")):
        for target in ticket.get(field, []):
            links[(kind, "to", target)] = {"blocking": False, "attributes": {}}
    for ref in ticket.get("refs") or []:
        value = {"blocking": False, "attributes": {}}
        if ref.get("url"):
            value["url"] = ref["url"]
        links[("relates-to", "external", f"{ref['system']}:{ref['id']}")] = value
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
        ops.append({"op": "reopen", "id": tid})
        before = "todo"
    if after == "running":
        if before in PAUSED:
            transition("resume")
            ops.append({"op": "claim", "id": tid, "by": who})
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


def _scoped_paths(repo, paths: list) -> list:
    """Orbit compares touched paths across the whole Board, so each carries its repo's name."""
    clean = [p[2:] if p.startswith("./") else p.lstrip("/") for p in paths]
    return [f"{repo}/{p}" for p in clean] if repo else clean


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
    if (before or {}).get("touches") != after.get("touches"):
        ops.append({"op": "property", "id": tid, "owner": "touches",
                    "value": {"paths": _scoped_paths(after.get("repo"), after.get("touches", []))}})
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
    if "status" in metadata:
        # Pre-native runs keep their authoritative ticket fields until explicit migration.
        if t["status"] == "running":
            t["liveness"] = "stuck" if _lapsed(task.get("lease_until")) else "alive"
        return t
    t["status"] = task.get("status", t["status"])
    t["slice"] = task.get("parent", t["slice"])
    touches = (task.get("properties") or {}).get("touches")
    if isinstance(touches, dict):
        own = f"{t.get('repo')}/" if t.get("repo") else None
        t["touches"] = [p[len(own):] if own and p.startswith(own) else p
                        for p in touches.get("paths", [])]
    if task.get("assignee"):
        t["assignee"] = task["assignee"]
    t.update(blocked_by=[], after=[], related=[], ships_with=None, refs=[])
    for link in task.get("links", []):
        kind, target = link["type"], link["target"]
        attributes = link.get("attributes") or {}
        if kind == "blocks" and isinstance(target, str) and link.get("blocking", True):
            t["blocked_by"].append({"id": target, "type": attributes.get("edge", "merge")})
        elif kind in ("after", "relates-to") and isinstance(target, str):
            t["after" if kind == "after" else "related"].append(target)
        elif kind == "relates-to" and isinstance(target, dict) and target.get("system"):
            ref = {"system": target["system"], "id": target["id"]}
            if target.get("url"):
                ref["url"] = target["url"]
            t["refs"].append(ref)
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
        self._negotiated = False

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
        for task in data.get("issues", []):
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
        if not self._negotiated:
            negotiate(self.config["anchor"])
            self._negotiated = True
        return self.config["anchor"]

    def _resume_pending(self, st: dict) -> None:
        from kinetic.state import internal
        journal = internal(st["run_dir"]) / "orbit-write.json"
        if journal.exists():
            pending = json.loads(journal.read_text())
            request = pending.get("request")
            if request is None:
                request = [{k: v for k, v in op.items() if k != "if_version"}
                           for op in pending["operations"]]
            self._batch(st, request)

    def _batch(self, st: dict, operations: list) -> dict:
        if not operations:
            return {"keys": {}, "results": []}
        from kinetic.state import atomic_write, internal
        anchor = self._anchor(st)
        journal = internal(st["run_dir"]) / "orbit-write.json"
        digest = hashlib.sha256(json.dumps(operations, sort_keys=True).encode()).hexdigest()
        pending = json.loads(journal.read_text()) if journal.exists() else None
        if pending and pending["digest"] != digest:
            self._resume_pending(st)
            return self._batch(st, operations)
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
                       "operations": guarded, "request": operations}
            atomic_write(journal, json.dumps(pending))
        for attempt in range(3):
            try:
                result = call(anchor, ["batch", "--file", "-", "--idempotency-key", pending["key"]],
                              stdin=json.dumps({"operations": pending["operations"]}))
            except OrbitStoreError as error:
                if error.code != "VERSION_CONFLICT":
                    if error.code not in ("LOCKED", "BAD_OUTPUT", "ORBIT_FAILED"):
                        journal.unlink(missing_ok=True)
                    raise
                if attempt == 2:
                    journal.unlink(missing_ok=True)
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
        self._resume_pending(st)
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
        if saved is None or any(e.get("type") == "board.changed" for e in feed["events"]):
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
    def capabilities(self) -> frozenset:
        return frozenset({"review-states", "checkpoint", "schedule-hints",
                          "query", "idempotent-write", "placement", "priority",
                          "human-question", "external-ref"})

    def ask(self, run_dir, st: dict, ticket_id: str, question: str) -> None:
        self._resume_pending(st)
        self._batch(st, [{"op": "ask", "id": ticket_id, "question": question,
                          "by": holder(st["run_id"], ticket_id, "implementer")}])

    def answer(self, run_dir, st: dict, ticket_id: str, answer: str) -> None:
        self._resume_pending(st)
        issue = call(self._anchor(st), ["show", ticket_id])
        ask = (issue.get("properties") or {}).get("orbit", {}).get("ask")
        if not ask or "answer" in ask:
            raise ValueError(f"no open question on {ticket_id}")
        self._batch(st, [{"op": "answer", "id": ticket_id, "answer": answer,
                          "by": holder(st["run_id"])}])

    def open_questions(self, run_dir, st: dict) -> list:
        out = []
        for issue in self._issues(st):
            ask = (issue.get("properties") or {}).get("orbit", {}).get("ask")
            if ask and "answer" not in ask:
                out.append({"id": issue["id"], "question": ask["question"], "at": ask["at"]})
        return out

    def rollup(self, run_dir, st: dict) -> dict:
        """Ticket counts from one `digest` of the spec; slices are subtracted from its subtree."""
        from kinetic.ticket_store import tickets_rollup
        spec = self._spec_id(st)
        if not spec:
            return tickets_rollup(self.read(run_dir, st))
        digest = call(self._anchor(st), ["digest", spec])
        counts = digest["rollup"]
        # Orbit's `open` includes the blocked ones; the contract counts them apart.
        out = {"done": counts["done"], "blocked": counts["blocked"],
               "open": counts["open"] - counts["blocked"], "cancelled": counts["cancelled"],
               "total": counts["total"]}
        for slice_ in digest.get("children") or []:
            if slice_.get("blocked"):
                kind = "blocked"
            elif slice_["status"] in DONE:
                kind = "done"
            elif slice_["status"] == "cancelled":
                kind = "cancelled"
            else:
                kind = "open"
            out[kind] -= 1
            out["total"] -= 1
        return out

    def changes(self, run_dir, st: dict, cursor) -> dict:
        """Ids of the run's tickets that changed since `cursor`, from Orbit's change feed."""
        ids = list(self.read(run_dir, st))
        anchor = self._anchor(st)

        def feed(since):
            events, args = [], ["changes"] + (["--since", since] if since else [])
            while True:
                page = call(anchor, args)
                events += page.get("events") or []
                if not page.get("truncated"):
                    return page["cursor"], events
                args = ["changes", "--since", page["cursor"]]

        try:
            head, events = feed(cursor)
        except OrbitStoreError as error:
            if cursor is None or error.code not in ("INVALID_ARGUMENT", "INVALID_CURSOR"):
                raise
            return {"cursor": feed(None)[0], "ids": ids}  # unknown cursor: report everything
        if cursor is None or any(e.get("type") == "board.changed" for e in events):
            return {"cursor": head, "ids": ids}
        touched = {e["issue"] for e in events if e.get("issue")}
        return {"cursor": head, "ids": [i for i in ids if i in touched]}

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
        self.config["needs_migration"] = any(
            "status" in ((task.get("properties") or {}).get(ANNOTATION_OWNER)
                         or (task.get("annotations") or {}).get(ANNOTATION_OWNER) or {})
            or task.get("status") not in NATIVE_STATUSES for task in tasks)
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
        if self.config.get("needs_migration"):
            raise OrbitStoreError("MIGRATION_REQUIRED", "this run still has legacy ticket fields",
                                  "run kinetic orbit_store migrate --run <run_dir> --apply")
        staged = self._staged(run_dir)
        run_id, names = st["run_id"], self._names(st)
        # After the first publish, a new ticket (a NEEDS_HUMAN ticket, a review fix)
        # is staged durably, then published before the remaining state updates.
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
        if published and (new or staged):
            self._save_staged(run_dir, {**staged, **new})
            mapping = self.publish(run_dir, st)
            aliases = {**aliases, **mapping}
            st["ticket_aliases"] = aliases
            staged, staged_changed = {}, False
        ref = self._ref(run_id, set(), aliases)
        for op in changes:
            if op["op"] in ("link", "unlink") and "to" in op:
                op["to"] = ref(op["to"])
        rewritten = {aliases.get(tid, tid): _rewrite(t, aliases) for tid, t in after.items()}
        operations = changes + self._close_slices(st, staged, before, rewritten)
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
        ranks = graph.priorities(tickets)
        board_names = _board_priorities(self._anchor(st))
        for slice_id in new_slices:
            add = {"op": "add", "key": source_key(run_id, slice_id),
                   "title": ticket_text(run_dir, slice_id)[0],
                   "type": "task",
                   "labels": [run_label(run_id, names)],
                   **_placement(None, ticket_text(run_dir, slice_id)[0]),
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
                   **({"priority": _priority_name(ranks[pid], board_names)}
                      if pid in ranks and board_names else {}),
                   "labels": sorted(derive(t, run_id, names)["labels"]),
                   # A slice published earlier has no title here, so only its tickets' repository is set.
                   **_placement(t.get("repo"), ticket_text(run_dir, t["slice"])[0]
                                if t.get("slice") in new_slices else None),
                   "properties": {ANNOTATION_OWNER: _property_fields(t)}}
            if t.get("slice"):
                add["parent"] = ref(t["slice"])
            ops.append(add)
        for pid in order:
            initial = dict(tickets[pid], status="todo")
            for op in ops_for(ref(pid), None, initial, run_id, names):
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
        # Integration review completes the spec; finishing slices alone is insufficient.
        return ops

    def spec_ops(self, run_dir, st: dict, title: str, body: str) -> list:
        """Publish an owned epic, linking any user Issue without mutating it."""
        self._resume_pending(st)
        run_id, names = st["run_id"], self._names(st)
        key = source_key(run_id, "spec")
        existing = self._spec_id(st)
        ref = existing or f"key:{key}"
        labels = sorted({run_label(run_id, names), names["spec"]})
        value = {"role": "spec", "run": run_id, "phase": "awaiting-approval"}
        text = f"{SPEC_GENERATED}\n\n{body}"
        ops = [{"op": "add", "key": key, "title": title, "type": "epic",
                "labels": labels, "body": text, "external_id": f"kinetic:{run_id}",
                "properties": {ANNOTATION_OWNER: value}},
               {"op": "update", "id": ref, "title": title, "body": text},
               {"op": "property", "id": ref, "owner": ANNOTATION_OWNER, "value": value}]
        current = call(self._anchor(st), ["show", existing])["status"] if existing else "todo"
        if current != "needs_decision":
            if current in DONE or current == "cancelled":
                ops.append({"op": "reopen", "id": ref})
            ops.append({"op": "transition", "id": ref, "name": "escalate" if current == "blocked" else "ask",
                        "by": holder(run_id)})
        user_ref = st.get("ref")
        if user_ref and user_ref != existing:
            try:
                user_issue = call(self._anchor(st), ["show", user_ref])
            except OrbitStoreError as error:
                if error.code != "NOT_FOUND":
                    raise
            else:
                ops.append({"op": "link", "id": ref, "type": "relates-to", "to": user_issue["id"],
                            "blocking": False})
        return ops

    def publish_spec(self, run_dir, st: dict, title: str, body: str) -> dict:
        keys = self._batch(st, self.spec_ops(run_dir, st, title, body)).get("keys", {})
        ref = keys[source_key(st["run_id"], "spec")]
        self.config["spec_task"] = ref
        return {"ref": ref, "published": True}

    def sync_spec(self, run_dir, st: dict, event: str) -> None:
        self._resume_pending(st)
        spec_id = self._spec_id(st)
        if not spec_id:
            return
        phase = SPEC_PHASES.get(event)
        if phase is None:
            raise OrbitStoreError("INVALID_ARGUMENT", f"unknown spec sync event '{event}'",
                                  "expected one of: " + ", ".join(sorted(SPEC_PHASES)))
        current = call(self._anchor(st), ["show", spec_id])["status"]
        ops = []
        if event in ("gate-approved", "building") and current == "needs_decision":
            ops.append({"op": "transition", "id": spec_id, "name": "answer", "by": holder(st["run_id"])})
            current = "todo"
        if event == "building" and current == "todo":
            ops.append({"op": "transition", "id": spec_id, "name": "claim",
                        "by": holder(st["run_id"])})
        ops.append({"op": "property", "id": spec_id, "owner": ANNOTATION_OWNER,
                    "value": {"role": "spec", "run": st["run_id"], "phase": phase}})
        self._batch(st, ops)

    def publish(self, run_dir, st: dict) -> dict:
        """Publish staged Issues, then apply active statuses using resolved identities."""
        self._resume_pending(st)
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
        transitions = []
        for pid in order:
            if staged[pid].get("status", "todo") != "todo":
                tid = mapping[pid]
                current = to_ticket(call(self._anchor(st), ["show", tid]))
                transitions += ops_for(tid, current, _rewrite(staged[pid], mapping), run_id, self._names(st))
        self._batch(st, transitions)
        self.config["published"] = True
        self.config["published_slices"] = sorted(done_slices | {mapping[s] for s in new_slices})
        # The Board now holds them; a save lost after this point heals on the next read.
        self._save_staged(run_dir, {})
        for pid in order:
            (Path(run_dir) / "issues" / f"{pid}.md").unlink(missing_ok=True)
        return mapping

    def record_verify(self, run_dir, st: dict, ticket_id: str, evidence: dict) -> None:
        self._resume_pending(st)
        if ticket_id in self._staged(run_dir):
            return
        # Orbit reserves evidence for submit; keep failed and pre-submit verify here.
        self._batch(st, [{"op": "property", "id": ticket_id, "owner": "kinetic-verify", "value": evidence}])

    def submit_review(self, run_dir, st: dict, ticket_id: str, evidence: list,
                      scope: str = "ticket") -> None:
        self._resume_pending(st)
        if scope not in ("ticket", "fix", "integration"):
            raise ValueError("review scope must be ticket, fix or integration")
        if not evidence or any(e.get("exit_code") != 0 for e in evidence):
            raise ValueError("review requires a passing verify for every reviewed repo")
        issue = call(self._anchor(st), ["show", ticket_id])
        if issue["status"] == "in_review":
            if (issue.get("properties") or {}).get("kinetic-reviews", {}).get("scope") != scope:
                raise ValueError("submitted review has a different scope")
            return
        restarting = scope == "integration" and issue["status"] == "todo"
        if issue["status"] != "running" and not restarting:
            raise ValueError("submit review requires running work")
        role = "orchestrator" if scope == "integration" else "implementer"
        by = holder(st["run_id"], None if scope == "integration" else ticket_id, role)
        operations = [{"op": "transition", "id": ticket_id, "name": "claim", "by": by}] if restarting else []
        self._batch(st, [*operations,
            {"op": "submit", "id": ticket_id, "by": by,
             "command": [e["command"] for e in evidence if e.get("command")],
             "exit_code": 0, "file": [e["log_path"] for e in evidence]},
            {"op": "property", "id": ticket_id, "owner": "kinetic-reviews",
             "value": {"scope": scope, "verdicts": {}}},
        ])

    def record_review(self, run_dir, st: dict, ticket_id: str, axis: str,
                      verdict: str, reason: str = "") -> None:
        self._resume_pending(st)
        if axis not in ("spec", "standards", "fix", "integration"):
            raise ValueError("review axis must be spec, standards, fix or integration")
        if verdict not in ("approve", "request-changes"):
            raise ValueError("review verdict must be approve or request-changes")
        if verdict == "request-changes" and not reason.strip():
            raise ValueError("request-changes requires a reason")
        issue = call(self._anchor(st), ["show", ticket_id])
        record = dict((issue.get("properties") or {}).get("kinetic-reviews") or {})
        verdicts = dict(record.get("verdicts") or {})
        by = holder(st["run_id"], None if axis == "integration" else ticket_id, axis + "-reviewer")
        outcome = {"verdict": verdict, "reason": reason, "by": by}
        if verdicts.get(axis) == outcome:
            return
        if issue["status"] != "in_review":
            raise ValueError("record review requires submitted work")
        expected_axes = {"ticket": ("spec", "standards"), "fix": ("fix",), "integration": ("integration",)}
        if axis not in expected_axes.get(record.get("scope"), ()):
            raise ValueError("review axis does not match the submitted scope")
        verdicts[axis] = outcome
        record["verdicts"] = verdicts
        operations = [{"op": "property", "id": ticket_id, "owner": "kinetic-reviews", "value": record}]
        approved = (axis in ("fix", "integration") or
                    all(verdicts.get(a, {}).get("verdict") == "approve" for a in ("spec", "standards")))
        if verdict == "request-changes" or approved:
            operations.append({"op": "review", "id": ticket_id, "verdict": verdict,
                               "reason": reason or None,
                               "by": by})
            if verdict == "approve" and axis != "integration":
                before = st.get("tickets", {})
                after = {**before, ticket_id: {**before.get(ticket_id, {}), "status": "done"}}
                operations.extend(self._close_slices(st, self._staged(run_dir), before, after))
        self._batch(st, operations)

    def checkpoint(self, run_dir, st: dict, ticket_id: str, progress: str,
                   next: str | None = None, blocker: str | None = None) -> None:
        """Record the handoff on the implementer's behalf: Orbit lets only the holder write it."""
        self._resume_pending(st)
        if ticket_id in self._staged(run_dir):
            return
        anchor = self._anchor(st)
        who = holder(st["run_id"], ticket_id, "implementer")
        if call(anchor, ["show", ticket_id]).get("assignee") != who:
            raise ValueError(f"{ticket_id} is not held by its implementer, so it has no checkpoint to update")
        args = ["checkpoint", ticket_id, "--by", who, "--progress", progress]
        for flag, value in (("--next", next), ("--blocker", blocker)):
            if value:
                args += [flag, value]
        call(anchor, args)

    def last_checkpoint(self, run_dir, st: dict, ticket_id: str) -> dict | None:
        if ticket_id in self._staged(run_dir):
            return None
        found = call(self._anchor(st), ["show", ticket_id]).get("checkpoint")
        return {k: v for k, v in found.items() if v is not None} if found else None

    def hints(self, run_dir, st: dict, ready_ids: list) -> list:
        """Prefer safe paths, then the longest critical path. Listing only; nothing is claimed."""
        listed = call(self._anchor(st), ["ready", "--all", "--strategy", "critical-path,touches",
                                         "--label", run_label(st["run_id"], self._names(st)),
                                         "--limit", "1000"]).get("issues", [])
        rank = {issue["id"]: n for n, issue in enumerate(listed)}
        offered = list(ready_ids)
        return sorted(offered, key=lambda tid: (rank.get(tid, len(rank)), offered.index(tid)))

    def touch(self, run_dir, st: dict, ticket_id: str) -> None:
        self._resume_pending(st)
        anchor = self._anchor(st)
        issue = call(anchor, ["show", ticket_id])
        who = holder(st["run_id"], ticket_id, "implementer")
        if issue.get("status") == "running" and issue.get("assignee") == who:
            self._batch(st, [{"op": "heartbeat", "id": ticket_id, "by": who}])

    def health(self, settings: Path) -> list:
        """Doctor checks the contract and native profile through Orbit's handshake."""
        import shutil
        from kinetic import tracker

        def row(name, status, fix=""):
            return {"name": name, "status": status, "fix": fix}

        if not shutil.which("orbit"):
            return [row("orbit-binary", "fail", f"install orbit {MIN_ORBIT} or later")]
        resolved = tracker.resolve(settings=settings)
        anchor = Path(resolved["settings"].get("orbit_dir") or Path(settings).parent.parent).expanduser()
        out = [row("orbit-binary", "pass")]
        try:
            data = negotiate(str(anchor))
        except OrbitStoreError as error:
            return out + [row("orbit-contract", "fail", str(error))]
        out += [row("orbit-board", "pass"), row("orbit-contract", "pass"),
                row("orbit-capabilities", "pass")]
        statuses = {status["name"] for status in (data.get("workflow") or {}).get("statuses", [])}
        missing = sorted(NATIVE_STATUSES - statuses)
        fix = "run kinetic install --harness <your harness>, then orbit setup kinetic in the Board"
        if missing:
            fix += "; missing statuses: " + ", ".join(missing)
        out.append(row("orbit-profile", "pass") if not missing and "kinetic" in data.get("profiles", [])
                   else row("orbit-profile", "fail", fix))
        return out


def _toml(value):
    if isinstance(value, dict):
        return "{ " + ", ".join(f"{key} = {_toml(item)}" for key, item in value.items()
                                  if item is not None) + " }"
    if isinstance(value, list):
        return "[" + ", ".join(_toml(item) for item in value) + "]"
    return json.dumps(value)


def _migration_profile(workflow: dict, temporary: bool) -> str:
    """Retain unrelated legacy states and their routes while adding native workflow."""
    try:
        import tomllib
    except ImportError:  # stdlib since Python 3.11; kinetic itself still runs on 3.9
        raise OrbitStoreError("PYTHON_TOO_OLD", "applying a run migration needs Python 3.11 or later",
                              "run the migration with python3.11+ (preview works on any version)")
    from kinetic.tracker import ROOT
    text = (ROOT / "profiles" / "kinetic.toml").read_text()
    native = tomllib.loads(text)["workflow"]
    states = {item["name"]: item for item in native["statuses"]}
    for item in workflow["statuses"]:
        states.setdefault(item["name"], {**item, "ready": False})
    transitions = {item["name"]: item for item in native["transitions"]}
    for item in workflow["transitions"]:
        if item["name"].startswith("kinetic_migrate_"):
            continue
        item = {key: value for key, value in item.items() if value is not None}
        name = item["name"]
        if name in transitions and transitions[name] != item:
            item = {**item, "name": "legacy_" + name}
        transitions.setdefault(item["name"], item)
    if temporary:
        for target in sorted(NATIVE_STATUSES):
            transitions["kinetic_migrate_" + target] = {
                "name": "kinetic_migrate_" + target,
                "from": sorted(set(states) - {target}), "to": target,
                **({"resolution": "completed"} if target in DONE else {})}
    section = "[workflow]\ninitial = \"todo\"\n"
    section += "statuses = " + _toml(list(states.values())) + "\n"
    section += "transitions = " + _toml(list(transitions.values())) + "\n\n"
    return re.sub(r"\[workflow\]\n.*?(?=\[\[policy.rules\]\])", lambda _: section, text, flags=re.S)


def migrate(run_dir, apply: bool = False) -> dict:
    """Explicitly convert one run's legacy fields, preserving all Issue IDs."""
    from kinetic import state
    st = state.load(run_dir, tickets=False)
    if (st.get("ticket_store") or {}).get("kind") != "orbit":
        raise OrbitStoreError("INVALID_ARGUMENT", "this run does not use the Orbit ticket store")
    store = make(st["ticket_store"]["config"])
    anchor = store._anchor(st)
    capability = negotiate(anchor)
    if apply:
        store._resume_pending(st)
    workflow = capability.get("workflow")
    if not workflow:
        raise OrbitStoreError("INVALID_ARGUMENT", "the Board workflow cannot be resolved",
                              "repair the Board workflow before migrating")
    issues, cursor = [], None
    while True:
        args = ["list", "--all", "--include-closed", "--label", run_label(st["run_id"], store._names(st))]
        if cursor:
            args += ["--cursor", cursor]
        page = call(anchor, args)
        issues += page.get("issues", [])
        cursor = page.get("next_cursor")
        if not cursor:
            break
    operations, migrated = [], []
    for issue in sorted(issues, key=lambda item: item.get("depth", 0), reverse=True):
        metadata = ((issue.get("properties") or {}).get(ANNOTATION_OWNER)
                    or (issue.get("annotations") or {}).get(ANNOTATION_OWNER) or {})
        if "status" not in metadata and issue["status"] in NATIVE_STATUSES:
            continue
        if not metadata and not _is_slice(issue, run_label(st["run_id"], store._names(st))):
            continue
        metadata = dict(metadata)
        if _is_slice(issue, run_label(st["run_id"], store._names(st))):
            metadata["role"] = "slice"
        legacy = {"ready": "todo", "in_progress": "running", "needs_human": "needs_decision"}
        target = metadata.get("status", legacy.get(issue["status"], issue["status"]))
        if metadata.get("role") == "spec" and metadata.get("phase") == "awaiting-approval":
            target = "needs_decision"
        tid = issue["id"]
        if target not in NATIVE_STATUSES:
            raise OrbitStoreError("INVALID_ARGUMENT", f"{tid} has unknown legacy status {target}")
        migrated.append(tid)
        for (kind, field, value), attributes in _links(metadata, st["run_id"]).items():
            existing = next((link for link in issue.get("links", [])
                             if link["type"] == kind and link.get("target") == value), None)
            if existing:
                if (existing.get("attributes") or {}) == attributes["attributes"]:
                    continue
                operations.append({"op": "unlink", "id": tid, "type": kind, field: value})
            operations.append({"op": "link", "id": tid, "type": kind, field: value, **attributes})
        if issue["status"] != target:
            operations.append({"op": "transition", "id": tid, "name": "kinetic_migrate_" + target,
                               "by": issue.get("assignee") or holder(st["run_id"])})
        old_labels = set(issue.get("labels", []))
        obsolete = {"kinetic", store._names(st).get("hold", "kinetic")}
        if metadata.get("ships_with"):
            obsolete.add("ships-with-" + metadata["ships_with"])
        if old_labels & obsolete:
            operations.append({"op": "label", "id": tid, "remove": sorted(old_labels & obsolete)})
        operations.append({"op": "property", "id": tid, "owner": ANNOTATION_OWNER,
                           "value": _property_fields(metadata)})
        if (target == "running" and metadata.get("kind", "agent") != "human"
                and metadata.get("role") not in ("spec", "slice")):
            operations.append({"op": "claim", "id": tid,
                               "by": holder(st["run_id"], tid, "implementer"), "steal": True})
    result = {"applied": False, "migrated": migrated, "operations": operations,
              "workflow": "native kinetic with existing legacy states and legacy_ transition aliases"}
    if not apply:
        return result
    # Keep profile sources in the run so Orbit's applied-profile pointers remain valid.
    profile = state.internal(run_dir) / "orbit-profile.toml"
    temporary = state.internal(run_dir) / "orbit-migration.toml"
    state.atomic_write(profile, _migration_profile(workflow, temporary=False))
    if operations:
        state.atomic_write(temporary, _migration_profile(workflow, temporary=True))
        call(anchor, ["setup", str(temporary)])
        try:
            store._batch(st, operations)
        finally:
            call(anchor, ["setup", str(profile)])
    else:
        call(anchor, ["setup", str(profile)])
    (state.internal(run_dir) / "orbit-feed.json").unlink(missing_ok=True)
    store.config.pop("needs_migration", None)
    state.save(run_dir, st)
    result["applied"] = True
    return result


def main(argv=None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(prog="kinetic orbit_store")
    sub = parser.add_subparsers(dest="command", required=True)
    migration = sub.add_parser("migrate", help="preview migration of one existing run to native Issues")
    migration.add_argument("--run", required=True)
    migration.add_argument("--apply", action="store_true", help="apply the previewed workflow and Issue changes")
    args = parser.parse_args(argv)
    try:
        print(json.dumps(migrate(args.run, args.apply)))
    except (OrbitStoreError, ValueError, OSError, KeyError) as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


def make(config: dict) -> OrbitTicketStore:
    return OrbitTicketStore(config)


def installation_files(clone: Path, home: Path) -> dict:
    """Register the named workflow profile only when Orbit is installed."""
    import shutil
    if not shutil.which("orbit"):
        return {}
    return {home / ".config" / "orbit" / "profiles" / "kinetic.toml":
            (clone / "profiles" / "kinetic.toml").read_text()}
