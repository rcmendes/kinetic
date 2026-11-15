"""Bare-clone helpers: recognising bare repos, and listing, cloning and refreshing
the clones in the project's bare-repos/ folder, plus creating a repo on GitHub."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

from kinetic import project

VISIBILITIES = ("private", "internal", "public")


def is_bare(path) -> bool:
    d = Path(path)
    if not (d / "HEAD").is_file() or not (d / "objects").is_dir() or not (d / "refs").is_dir():
        return False
    if (d / ".git").exists():
        return False
    try:
        return "bare = true" in (d / "config").read_text(errors="ignore").replace("\t", "")
    except OSError:
        return False


def _git(path, *args):
    try:
        r = subprocess.run(["git", "-C", str(path), *args], capture_output=True, text=True,
                           timeout=30)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def default_branch(clone):
    return _git(clone, "symbolic-ref", "--short", "HEAD")


def list_clones(bare_dir) -> list:
    root = Path(bare_dir)
    if not root.is_dir():
        return []
    out = []
    for child in sorted(root.iterdir(), key=lambda c: c.name):
        if child.is_dir() and is_bare(child):
            out.append({"name": child.name, "path": str(child),
                        "remote": _git(child, "config", "--get", "remote.origin.url"),
                        "default_branch": default_branch(child)})
    return out


def _run(argv) -> str:
    try:
        r = subprocess.run(argv, capture_output=True, text=True)
    except OSError as e:
        raise RuntimeError(str(e))
    if r.returncode != 0:
        raise RuntimeError((r.stderr or r.stdout).strip() or f"{argv[0]} failed")
    return r.stdout


def _valid_name(name) -> bool:
    return bool(name) and Path(name).name == name and name not in (".", "..")


def clone(bare_dir, repo=None, url=None, name=None) -> dict:
    root = Path(bare_dir)
    if not root.is_dir():
        raise ValueError(f"not a directory: {bare_dir}")
    if repo and not url and not name:
        if repo.count("/") != 1 or not all(repo.split("/")):
            raise ValueError(f"--repo must be ORG/NAME, got: {repo}")
        name = repo.split("/")[1]
    elif not (url and name and not repo):
        raise ValueError("give either --repo ORG/NAME, or --url together with --name")
    if not _valid_name(name):
        raise ValueError(f"--name must be a single path component, got: {name}")
    dest = root / (name if name.endswith(".git") else name + ".git")
    if dest.exists():
        raise ValueError(f"already exists: {dest}")
    try:
        if repo:
            _run(["gh", "repo", "clone", repo, str(dest), "--", "--bare"])
        else:
            _run(["git", "clone", "--bare", url, str(dest)])
        _run(["git", "-C", str(dest), "config", "remote.origin.fetch",
              "+refs/heads/*:refs/remotes/origin/*"])
        _run(["git", "-C", str(dest), "fetch", "origin"])
        branch = default_branch(dest)
        if branch:
            _run(["git", "-C", str(dest), "branch", f"--set-upstream-to=origin/{branch}", branch])
    except RuntimeError:
        shutil.rmtree(dest, ignore_errors=True)  # no half-made clone left to block a retry
        raise
    return {"name": dest.name, "path": str(dest), "default_branch": branch}


def _origin_refs(clone_path) -> dict:
    out = _run(["git", "-C", str(clone_path), "for-each-ref",
                "--format=%(refname) %(objectname) %(symref)", "refs/remotes/origin"])
    refs = {}
    for line in out.splitlines():
        ref, sha, *symref = line.split(" ")
        if not any(symref):  # origin/HEAD is a pointer, not a branch that advanced
            refs[ref] = sha
    return refs


def _refresh_one(bare_dir, name) -> dict:
    if not _valid_name(name):
        return {"name": name, "ok": False, "advanced": [],
                "error": f"name is not a single path component: {name}"}
    path = Path(bare_dir) / name
    if not (path.is_dir() and is_bare(path)):
        return {"name": name, "ok": False, "advanced": [],
                "error": f"no bare clone named {name} in {bare_dir}"}
    if not _git(path, "config", "--get", "remote.origin.fetch"):
        return {"name": name, "ok": False, "advanced": [],
                "error": f"no fetch refspec; run git -C {path} config "
                         "remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'"}
    try:
        before = _origin_refs(path)
        _run(["git", "-C", str(path), "fetch", "origin", "--prune"])
        after = _origin_refs(path)
    except RuntimeError as e:
        return {"name": name, "ok": False, "advanced": [], "error": str(e)}
    advanced = [{"ref": ref, "from": before.get(ref), "to": after.get(ref)}
                for ref in sorted(set(before) | set(after)) if before.get(ref) != after.get(ref)]
    return {"name": name, "ok": True, "advanced": advanced, "error": None}


def refresh(bare_dir, names=None) -> list:
    if names is None:
        names = [c["name"] for c in list_clones(bare_dir)]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        return list(pool.map(lambda n: _refresh_one(bare_dir, n), names))


def gh_ready() -> bool:
    if not shutil.which("gh"):
        return False
    try:
        return subprocess.run(["gh", "auth", "status"], capture_output=True).returncode == 0
    except OSError:
        return False


def create(bare_dir, owner: str, name: str, visibility: str) -> dict:
    if visibility not in VISIBILITIES:
        raise ValueError(f"visibility must be one of {', '.join(VISIBILITIES)}, got: {visibility}")
    _run(["gh", "repo", "create", f"{owner}/{name}", f"--{visibility}", "--add-readme"])
    return clone(bare_dir, repo=f"{owner}/{name}")


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.bare")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("gh-ready")
    sp = sub.add_parser("clone")
    sp.add_argument("--repo", default=None)
    sp.add_argument("--url", default=None)
    sp.add_argument("--name", default=None)
    sp = sub.add_parser("create")
    sp.add_argument("--owner", required=True)
    sp.add_argument("--name", required=True)
    sp.add_argument("--visibility", default="private", choices=VISIBILITIES)
    sp = sub.add_parser("refresh")
    sp.add_argument("--name", action="append", default=None)
    a = p.parse_args(argv)
    code = 0
    try:
        if a.cmd == "gh-ready":
            out = {"ready": gh_ready()}
        else:
            d = project.bare_dir(project.require())
            if a.cmd == "list":
                out = list_clones(d)
            elif a.cmd == "clone":
                out = clone(d, a.repo, a.url, a.name)
            elif a.cmd == "create":
                out = create(d, a.owner, a.name, a.visibility)
            else:
                results = refresh(d, a.name)
                out = {"results": results}
                code = 0 if all(r["ok"] for r in results) else 1
    except project.NoProject as e:
        print(str(e), file=sys.stderr)
        return 2
    except (ValueError, RuntimeError, OSError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return code


if __name__ == "__main__":
    sys.exit(main())
