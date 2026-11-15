from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from kinetic import config, project, state


PUBLISHABLE = ("done", "done_with_concerns", "merged", "released")


def _git(repo, *args) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True)


def create_worktree(bare_or_repo, dest, branch: str, base: str) -> None:
    exists = _git(bare_or_repo, "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}").returncode == 0
    if not exists and not base.startswith("origin/"):
        # a bare clone's local branches go stale; cut new branches from the fetched remote tip
        bare = _git(bare_or_repo, "rev-parse", "--is-bare-repository")
        if bare.returncode == 0 and bare.stdout.strip() == "true":
            base = f"origin/{base}"
    args = ["worktree", "add", str(dest), branch] if exists else \
        ["worktree", "add", "-b", branch, str(dest), base]
    r = _git(bare_or_repo, *args)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip())


def merge_ticket(repo_path, ticket_branch: str, integration_branch: str) -> dict:
    co = _git(repo_path, "checkout", integration_branch)
    if co.returncode != 0:
        raise RuntimeError(co.stderr.strip())
    m = _git(repo_path, "merge", "--no-ff", "--no-edit", ticket_branch)
    if m.returncode == 0:
        return {"merged": True, "conflicts": []}
    out = _git(repo_path, "diff", "--name-only", "-z", "--diff-filter=U").stdout
    conflicts = sorted(f for f in out.split("\0") if f)
    if not conflicts:
        raise RuntimeError(m.stderr.strip() or m.stdout.strip())
    return {"merged": False, "conflicts": conflicts}


def status_hash(repo) -> str:
    """Stable sha256 of the repo's working state: `git status --porcelain`,
    `git diff HEAD` and the bytes of untracked files (gitignored files are not covered), so a content edit to an
    already-dirty or untracked file changes it too."""
    h = hashlib.sha256()
    for args in (("status", "--porcelain"), ("diff", "HEAD")):
        r = _git(repo, *args)
        if r.returncode != 0:
            raise RuntimeError(r.stderr.strip() or f"cannot read git {args[0]} in {repo}")
        h.update(r.stdout.encode("utf-8"))
    r = _git(repo, "ls-files", "--others", "--exclude-standard")
    for name in sorted(r.stdout.splitlines()):
        h.update(name.encode("utf-8"))
        try:
            with open(Path(repo) / name, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
        except OSError:
            h.update(b"<unreadable>")  # nested repo or no permission
    return h.hexdigest()


def assert_unchanged(repo, head: str, want_status_hash: str) -> dict:
    """Compare the repo's current HEAD and porcelain status to the recorded ones."""
    cur = _git(repo, "rev-parse", "HEAD")
    if cur.returncode != 0 or not cur.stdout.strip():
        raise RuntimeError(cur.stderr.strip() or f"cannot resolve HEAD in {repo}")
    cur_head = cur.stdout.strip()
    cur_hash = status_hash(repo)
    changed = []
    if cur_head != head:
        changed.append("head")
    if cur_hash != want_status_hash:
        changed.append("status")
    return {"ok": not changed, "changed": changed,
            "head": cur_head, "status_hash": cur_hash}


def _groups(tickets: dict) -> dict:
    out = {}
    for t in tickets.values():
        g = state.field(t, "ships_with")
        if g and t.get("repo"):
            out.setdefault(g, set()).add(t["repo"])
    return out


def ships_with_groups(st: dict) -> dict:
    """Ships-with group -> sorted repos of its tickets (repo-less tickets skipped)."""
    return {g: sorted(r) for g, r in _groups(st["tickets"]).items()}


def merge_order(st: dict) -> list:
    """Repos ordered so a repo follows every repo it has a merge/release blocker in.
    Among ready repos, one sharing a ships_with group with the last pick goes first."""
    tickets = st["tickets"]
    repos = {t["repo"] for t in tickets.values() if t.get("repo")}
    before = {r: set() for r in repos}  # repo -> repos that must come first
    for t in tickets.values():
        if not t.get("repo"):
            continue
        for e in t["blocked_by"]:
            b = tickets.get(e["id"])
            if e["type"] in ("merge", "release") and b and b.get("repo") and b["repo"] != t["repo"]:
                before[t["repo"]].add(b["repo"])
    groups = list(_groups(tickets).values())
    order = []
    while True:
        ready = sorted(r for r in before if r not in order and not (before[r] - set(order)))
        if not ready:
            break
        last = order[-1] if order else None
        near = [r for r in ready if any(last in g and r in g for g in groups)]
        order.append((near or ready)[0])
    if len(order) != len(repos):
        raise ValueError("dependency cycle among repos: " + ", ".join(sorted(repos - set(order))))
    return order


def pin(run_dir, upstream_repo: str, downstream_repo: str, project_config: Path) -> dict:
    repos = state.load(run_dir)["repos"]
    for name in (upstream_repo, downstream_repo):
        if name not in repos:
            raise ValueError(f"repo '{name}' not registered in state")
    up_path, down_path = Path(repos[upstream_repo]["path"]), Path(repos[downstream_repo]["path"])
    publish = config.resolve(upstream_repo, up_path, project_config)["publish_snapshot"]
    pin_cmd = config.resolve(downstream_repo, down_path, project_config)["pin"]
    for name, cmd, key in ((upstream_repo, publish, "publish_snapshot"), (downstream_repo, pin_cmd, "pin")):
        if not cmd:
            return {"ok": False, "log_path": None, "reason": f"{key} command is unset for repo '{name}'"}
    head = _git(up_path, "rev-parse", "HEAD")
    ref = head.stdout.strip()
    if head.returncode != 0 or not ref:
        raise RuntimeError(head.stderr.strip() or f"cannot resolve HEAD in {up_path}")
    logs = state.internal(run_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = logs / f"pin-{upstream_repo}-{downstream_repo}-{ts}.log"
    env = {**os.environ, "KINETIC_UPSTREAM_REF": ref}
    with open(log_path, "wb") as log:
        ok = subprocess.run(publish, shell=True, cwd=str(up_path), stdout=log,
                            stderr=subprocess.STDOUT).returncode == 0
        if ok:
            ok = subprocess.run(pin_cmd, shell=True, cwd=str(down_path), env=env, stdout=log,
                                stderr=subprocess.STDOUT).returncode == 0
    if ok:
        for tid, t in state.load(run_dir)["tickets"].items():
            if t["repo"] == upstream_repo and t["status"] in PUBLISHABLE:
                state.set_ticket(run_dir, tid, snapshot_published=True)
    return {"ok": ok, "log_path": str(log_path)}


def run_setup(run_dir, repo_name: str, worktree, project_config: Path) -> dict:
    """Run the repo's optional setup command in a fresh worktree; never touches state or the worktree."""
    worktree = Path(worktree)
    cmd = config.resolve(repo_name, worktree, Path(project_config))["setup"]
    if not cmd:
        return {"ok": True, "skipped": True, "log_path": None}
    logs = state.internal(run_dir) / "logs"
    logs.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    log_path = logs / f"setup-{worktree.name}-{ts}.log"
    with open(log_path, "wb") as log:
        rc = subprocess.run(cmd, shell=True, cwd=str(worktree), stdin=subprocess.DEVNULL,
                            stdout=log, stderr=subprocess.STDOUT).returncode
    return {"ok": rc == 0, "skipped": False, "log_path": str(log_path)}


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.gitops")
    sub = p.add_subparsers(dest="cmd", required=True)
    w = sub.add_parser("worktree")
    w.add_argument("--repo", required=True)
    w.add_argument("--dest", required=True)
    w.add_argument("--branch", required=True)
    w.add_argument("--base", required=True)
    m = sub.add_parser("merge")
    m.add_argument("--repo", required=True)
    m.add_argument("--ticket-branch", required=True)
    m.add_argument("--integration-branch", required=True)
    mo = sub.add_parser("merge-order")
    mo.add_argument("--run", required=True)
    sw = sub.add_parser("ships-with")
    sw.add_argument("--run", required=True)
    pn = sub.add_parser("pin")
    pn.add_argument("--run", required=True)
    pn.add_argument("--upstream", required=True)
    pn.add_argument("--downstream", required=True)
    su = sub.add_parser("setup")
    su.add_argument("--run", required=True)
    su.add_argument("--repo-name", required=True)
    su.add_argument("--path", required=True)
    sh = sub.add_parser("status-hash")
    sh.add_argument("--repo", required=True)
    au = sub.add_parser("assert-unchanged")
    au.add_argument("--repo", required=True)
    au.add_argument("--head", required=True)
    au.add_argument("--status-hash", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "worktree":
            create_worktree(Path(a.repo), Path(a.dest), a.branch, a.base)
            out, rc = {"ok": True}, 0
        elif a.cmd == "merge":
            out = merge_ticket(Path(a.repo), a.ticket_branch, a.integration_branch)
            rc = 0 if out["merged"] else 1
        elif a.cmd == "merge-order":
            out, rc = {"order": merge_order(state.load(a.run))}, 0
        elif a.cmd == "ships-with":
            out, rc = {"groups": ships_with_groups(state.load(a.run))}, 0
        elif a.cmd == "status-hash":
            out, rc = {"status_hash": status_hash(Path(a.repo))}, 0
        elif a.cmd == "assert-unchanged":
            out = assert_unchanged(Path(a.repo), a.head, a.status_hash)
            rc = 0 if out["ok"] else 1
        elif a.cmd == "setup":
            out = run_setup(a.run, a.repo_name, Path(a.path), project.config_path(a.run))
            rc = 0 if out["ok"] else 1
        else:
            out = pin(a.run, a.upstream, a.downstream, project.config_path(a.run))
            rc = 0 if out["ok"] else 1
    except project.NoProject as e:
        print(str(e), file=sys.stderr)
        return 2
    except (ValueError, OSError, KeyError, RuntimeError, config.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return rc


if __name__ == "__main__":
    sys.exit(main())
