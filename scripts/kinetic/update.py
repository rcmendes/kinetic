"""kinetic update: see what an update would do, then run it one step at a time.

`kinetic update` only reads: it prints the versions, what the checkout looks like, the
release notes, any open runs, what a ticket store needs from the machine, and which steps can run. `kinetic update
--apply <step>` runs exactly one step (pull, install or marketplace) and nothing else, so a
skill can ask the user before each step that changes anything.
"""
from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
import sys
from pathlib import Path

from kinetic import config, harness, project, versions

STEPS = ("pull", "install", "marketplace")
BRANCH = "main"


class UpdateRefused(Exception):
    """A step cannot run safely; the message says why and what to do."""


def _run(cmd, cwd=None):
    try:
        done = subprocess.run(list(cmd), cwd=cwd, capture_output=True, text=True, timeout=300)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return done.returncode, (done.stdout + done.stderr).strip()


def _git(clone, *args, run=None):
    return (run or _run)(["git", "-C", str(clone), *args])


def checkout_state(clone, run=None) -> dict:
    """Branch, whether tracked files are unchanged, and how many commits origin is ahead."""
    code, branch = _git(clone, "branch", "--show-current", run=run)
    branch = branch if code == 0 else ""
    code, dirty = _git(clone, "status", "--porcelain", "--untracked-files=no", run=run)
    _git(clone, "fetch", "--quiet", "origin", run=run)  # remote-tracking refs only
    behind = 0
    if branch:
        code, count = _git(clone, "rev-list", "--count", f"HEAD..origin/{branch}", run=run)
        behind = int(count) if code == 0 and count.isdigit() else 0
    return {"branch": branch, "clean": code == 0 and not dirty, "behind": behind}


def _requirements() -> list:
    """What each ticket store says it needs from the machine: [{name, installed, minimum, ok}]."""
    import importlib
    found = []
    for path in sorted(Path(__file__).resolve().parent.glob("*_store.py")):
        if path.stem == "ticket_store":
            continue
        try:
            need = getattr(importlib.import_module(f"kinetic.{path.stem}"), "tool_requirement", None)
            if need:
                found.append(need())
        except Exception:  # noqa: BLE001 - one store's check must not hide the plan
            continue
    return found


def _release_notes(repo, tag):
    code, out = _run(["gh", "release", "view", tag, "-R", repo, "--json", "body", "-q", ".body"])
    return out if code == 0 else None


def _open_runs():
    found = project.find()
    return [r["run_id"] for r in project.open_runs(found)] if found else []


def _has_claude():
    return shutil.which("claude") is not None


def _installed(home, settings):
    """Harnesses with an install manifest (the ones `kinetic install` has set up)."""
    from kinetic import doctor
    return [h for h in harness.HARNESS_IDS if doctor._manifest_path(Path(home), h).is_file()]


def plan(clone, home, settings, *, repo=None, gh=None, notes=None, requirements=None,
         open_runs=None, has_claude=None, run=None) -> dict:
    home = Path(home)
    current = versions.checkout_version(clone)
    repo = repo or versions.repository(clone)
    latest = versions.latest_release(repo, home / ".config" / "kinetic" / "update-check.json",
                                     gh=gh, max_age=0) if repo else None
    available = versions.newer(latest, current)
    state = checkout_state(clone, run)
    harnesses = _installed(home, settings)
    out = {"current": current, "latest": latest, "update_available": available,
           "checkout": state, "harnesses": harnesses,
           "open_runs": (open_runs or _open_runs)(),
           "requirements": (requirements or _requirements)()}
    if available:
        out["notes"] = (notes or _release_notes)(repo, f"v{latest}")
    pull_why = ("the checkout is on '%s', not %s" % (state["branch"], BRANCH) if state["branch"] != BRANCH
                else "there are local changes in the checkout" if not state["clean"]
                else "the checkout is already current" if not state["behind"] else "")
    claude_why = ("" if "claude" in harnesses and (has_claude or _has_claude)()
                  else "Claude Code is not installed for kinetic, or `claude` is not on PATH")
    out["steps"] = [
        {"step": "pull", "can": not pull_why, "why_not": pull_why,
         "does": f"git pull --ff-only in {clone}"},
        {"step": "install", "can": bool(harnesses), "why_not": "" if harnesses else "no harness installed",
         "does": "kinetic install --harness " + ", ".join(harnesses)},
        {"step": "marketplace", "can": not claude_why, "why_not": claude_why,
         "does": "claude plugin marketplace update kinetic; claude plugin update kinetic@kinetic"},
    ]
    return out


def apply(step, clone, home, settings, *, run=None, has_claude=None) -> dict:
    run = run or _run
    if step not in STEPS:
        raise UpdateRefused(f"unknown step '{step}': expected one of {', '.join(STEPS)}")
    if step == "pull":
        state = checkout_state(clone)
        if state["branch"] != BRANCH:
            raise UpdateRefused(f"the checkout is on '{state['branch']}', not {BRANCH}; switch to {BRANCH} first")
        if not state["clean"]:
            raise UpdateRefused(f"there are local changes in {clone}; commit or discard them first")
        before = versions.checkout_version(clone)
        _, head = _git(clone, "rev-parse", "HEAD")
        code, output = _git(clone, "pull", "--ff-only", "origin", BRANCH)
        _, after_head = _git(clone, "rev-parse", "HEAD")
        return {"step": "pull", "ok": code == 0, "changed": after_head != head, "from": before,
                "to": versions.checkout_version(clone), "output": output}
    if step == "install":
        results = []
        for hid in _installed(home, settings):
            code, output = run([str(Path(clone) / "bin" / "kinetic"), "install", "--harness", hid], cwd=str(clone))
            results.append({"harness": hid, "ok": code == 0, "output": output})
        return {"step": "install", "ok": bool(results) and all(r["ok"] for r in results), "results": results}
    if "claude" not in _installed(home, settings) or not (has_claude or _has_claude)():
        raise UpdateRefused("Claude Code is not installed for kinetic, or `claude` is not on PATH")
    results = []
    for cmd in (["claude", "plugin", "marketplace", "update", "kinetic"],
                ["claude", "plugin", "update", "kinetic@kinetic"]):
        code, output = run(cmd)
        results.append({"command": " ".join(cmd), "ok": code == 0, "output": output})
    return {"step": "marketplace", "ok": all(r["ok"] for r in results), "results": results,
            "restart_needed": True}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="kinetic.update")
    ap.add_argument("--apply", choices=STEPS, help="run one step instead of printing the plan")
    args = ap.parse_args(argv)
    settings = harness.DEFAULT_SETTINGS
    home = Path.home()
    try:
        cfg = config._load(settings)
    except config.ConfigError:
        cfg = {}
    from kinetic.__main__ import clone_root
    clone = Path(cfg.get("clone") or clone_root())
    try:
        out = (apply(args.apply, clone, home, settings) if args.apply
               else plan(clone, home, settings))
    except UpdateRefused as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0 if out.get("ok", True) else 1


if __name__ == "__main__":
    sys.exit(main())
