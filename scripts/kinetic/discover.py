"""Deterministic helpers for the discover phase: saved repo roots and GitHub
orgs, listing git repos under a root, and registering a repo in the project config.
The judgment (which repos a ticket touches) lives in skills/discover."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from kinetic import config, project

SETTINGS = Path.home() / ".config/kinetic/settings.json"


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def _real(p: str) -> str:
    return os.path.realpath(os.path.expanduser(p))


def _lists(data: dict, path) -> dict:
    out = {}
    for key in ("repo_roots", "github_orgs"):
        v = data.get(key) or []
        if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
            raise config.ConfigError(f"'{key}' in {path} must be a list of strings")
        out[key] = list(v)
    return out


def settings(path=None) -> dict:
    path = Path(path or SETTINGS)
    return _lists(config._load(path), path)


def _merge(old: list, new: list) -> list:
    out = list(old)
    for x in new:
        if x not in out:
            out.append(x)
    return out


def remember(roots: list, orgs: list, path=None) -> dict:
    path = Path(path or SETTINGS)
    data = config._load(path)  # raises on bad JSON before anything is written
    old = _lists(data, path)
    real = [_real(r) for r in roots]
    bad = [r for r, rr in zip(roots, real) if not os.path.isdir(rr)]
    if bad:
        raise ValueError(f"not a directory: {', '.join(bad)}")
    data["repo_roots"] = _merge([_real(r) for r in old["repo_roots"]], real)
    data["github_orgs"] = _merge(old["github_orgs"], [o.strip() for o in orgs if o.strip()])
    _write(path, data)
    return {"repo_roots": data["repo_roots"], "github_orgs": data["github_orgs"]}


def _git(path: str, *args) -> str | None:
    try:
        r = subprocess.run(["git", "-C", path, *args], capture_output=True, text=True)
    except OSError:
        return None
    return r.stdout.strip() if r.returncode == 0 and r.stdout.strip() else None


def _item(path: str, registered: dict) -> dict:
    from kinetic import bare  # bare imports discover at module level
    name = registered.get(path)
    return {"name": os.path.basename(path), "path": path,
            "remote": _git(path, "config", "--get", "remote.origin.url"),
            "bare": bare.is_bare(path),
            "registered": name is not None, "registered_as": name}


def scan(roots: list, project_config: Path | None = None, enclosing: bool = False) -> list:
    """List git repos: a root that is a repo's top level is that repo; otherwise its
    direct child repos. enclosing=True (init on the current directory) also returns
    the top level of a repo the root sits inside."""
    entries = config._load(project_config).get("repos") or {} if project_config else {}
    registered = {_real(e["path"]): n for n, e in entries.items()
                  if isinstance(e, dict) and e.get("path")}
    from kinetic import bare  # bare imports discover at module level
    out, seen = [], set()

    def add(p):
        if p not in seen:
            seen.add(p)
            out.append(_item(p, registered))

    for root in roots:
        root = _real(root)
        if not os.path.isdir(root):
            continue
        if bare.is_bare(root):
            add(root)
            continue
        top = _git(root, "rev-parse", "--show-toplevel")
        if top and (enclosing or _real(top) == root):
            add(_real(top))
            continue
        try:
            children = sorted(os.scandir(root), key=lambda e: e.name)
        except OSError:
            continue
        for child in children:
            try:
                if child.is_dir() and os.access(child.path, os.R_OK | os.X_OK) \
                        and (os.path.exists(os.path.join(child.path, ".git"))
                             or bare.is_bare(child.path)):
                    add(_real(child.path))
            except OSError:
                continue
    return out


def register(name: str, path: str, verify=None, publish_snapshot=None, pin=None,
             setup=None, verify_ticket=None, *, project_config: Path) -> dict:
    """Add a repo, or (same name and path) merge the non-empty commands given into its entry."""
    project_dir = Path(project_config).parent.parent
    repos = project.load(project_dir).get("repos") or {}  # raises on bad JSON before anything is written
    real = _real(path)
    old = repos.get(name)
    if old is not None and _real(old.get("path", "")) != real:
        raise ValueError(f"repo '{name}' is already registered at {old.get('path')}")
    given = {k: v for k, v in (("verify", verify), ("verify_ticket", verify_ticket), ("setup", setup),
                               ("publish_snapshot", publish_snapshot), ("pin", pin))
             if (v or "").strip()}
    entry = {**(old or {"path": real}), **given}
    if not entry.get("verify"):
        raise ValueError(f"no verify command for repo '{name}'")
    if entry != old:
        project.update(project_dir, {"repos": {**repos, name: entry}})
    return entry


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.discover")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("settings")
    sp = sub.add_parser("remember")
    sp.add_argument("--root", action="extend", nargs="+", default=[])
    sp.add_argument("--org", action="extend", nargs="+", default=[])
    sp = sub.add_parser("scan")
    sp.add_argument("--root", action="extend", nargs="+", required=True)
    sp.add_argument("--enclosing", action="store_true")
    sp = sub.add_parser("register")
    for a in ("--name", "--path"):
        sp.add_argument(a, required=True)
    sp.add_argument("--verify", default=None)  # required for a new repo; optional when updating one
    sp.add_argument("--publish-snapshot", default=None)
    sp.add_argument("--pin", default=None)
    sp.add_argument("--setup", default=None)
    sp.add_argument("--verify-ticket", default=None)
    a = p.parse_args(argv)
    try:
        if a.cmd == "settings":
            out = settings(SETTINGS)
        elif a.cmd == "remember":
            out = remember(a.root, a.org, SETTINGS)
        elif a.cmd == "scan":
            found = project.find()
            out = scan(a.root, found / ".kinetic" / "config.json" if found else None, a.enclosing)
        else:
            out = register(a.name, a.path, a.verify, a.publish_snapshot, a.pin, a.setup,
                           a.verify_ticket, project_config=project.config_path())
    except project.NoProject as e:
        print(str(e), file=sys.stderr)
        return 2
    except (ValueError, OSError, config.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
