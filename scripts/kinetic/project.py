"""A kinetic project folder: <project>/.kinetic/config.json, bare-repos/, runs/<id>/.
Later phases resolve it from any cwd (project root, a run, or a run's worktree)."""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

from kinetic import config

VERSION = 1

INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")
INSTRUCTIONS_BEGIN = "<!-- kinetic:begin -->"
INSTRUCTIONS_END = "<!-- kinetic:end -->"
INSTRUCTIONS_TEXT = """This folder is a kinetic project. Kinetic owns the workflow here.

- Start or resume work with the kinetic run skill (/kinetic:run, or kntc-run where kinetic's
  skills are installed as kntc-*). Use the implement skill for a ticket that is already specified.
- Do not start another plugin's process skill for work in this folder: no other brainstorming,
  planning, spec-writing, ticket-breakdown or implementation-loop skill. Use one only when a
  kinetic skill names it.
- Never edit files under .kinetic/ by hand."""
_BLOCK = f"{INSTRUCTIONS_BEGIN}\n{INSTRUCTIONS_TEXT}\n{INSTRUCTIONS_END}"


class NoProject(Exception):
    pass


def _is_project(d: Path) -> bool:
    # A run also has a .kinetic/ dir (state.json), so require the project's own trio.
    return all((d / n).is_dir() for n in (".kinetic", "bare-repos", "runs"))


def find(cwd=None):
    start = Path(os.path.realpath(cwd or os.getcwd()))
    for d in (start, *start.parents):
        if _is_project(d):
            return d
    return None


def require(cwd=None) -> Path:
    p = find(cwd)
    if p is None:
        raise NoProject("no kinetic project here; start one with /kinetic:start")
    return p


def config_path(cwd=None) -> Path:
    return require(cwd) / ".kinetic" / "config.json"


def _cfg(project: Path) -> Path:
    return Path(project) / ".kinetic" / "config.json"


def load(project: Path) -> dict:
    path = _cfg(project)
    data = config._load(path)
    v = data.get("version", VERSION)
    if not isinstance(v, int) or isinstance(v, bool) or v > VERSION:
        raise config.ConfigError(f"{path} has unsupported version {v!r}; this kinetic supports {VERSION}")
    return data


def _write(path: Path, data: dict) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)


def update(project: Path, changes: dict) -> dict:
    data = load(project)  # raises before anything is written
    data.update(changes)
    data["version"] = VERSION
    _write(_cfg(project), data)
    return data


def _check_name(name: str, what: str) -> None:
    if not name or name in (".", "..") or "/" in name or os.sep in name:
        raise ValueError(f"invalid {what}: {name!r}")


def create(parent: Path, name: str) -> Path:
    _check_name(name, "project name")
    parent = Path(os.path.realpath(parent))
    if not parent.is_dir():
        raise ValueError(f"not a directory: {parent}")
    outer = find(parent)
    if outer is not None:
        raise ValueError(f"{parent} is inside the kinetic project {outer}; pick a folder outside it")
    r = subprocess.run(["git", "-C", str(parent), "rev-parse", "--is-inside-work-tree"],
                       capture_output=True, text=True)
    if r.returncode == 0 and r.stdout.strip() == "true":
        raise ValueError(f"{parent} is inside a git work tree; pick a folder outside any repo")
    root = parent / name
    if root.exists() and (not root.is_dir() or any(root.iterdir())):
        raise ValueError(f"{root} already exists and is not empty")
    for sub in (".kinetic/sessions", "bare-repos", "runs"):
        (root / sub).mkdir(parents=True, exist_ok=True)
    _write(_cfg(root), {"version": VERSION, "repos": {}})
    write_instructions(root)
    return root


def _block_span(text: str):
    """(start, end) of the complete block in text, or None."""
    j = text.find(INSTRUCTIONS_END)
    # the begin nearest the end, so a dangling begin earlier keeps the user text after it
    i = text.rfind(INSTRUCTIONS_BEGIN, 0, j) if j >= 0 else -1
    return None if i < 0 else (i, j + len(INSTRUCTIONS_END))


def write_instructions(project: Path) -> list:
    written, seen = [], set()
    for name in INSTRUCTION_FILES:
        link = Path(project) / name
        path = link.resolve()  # write through a symlink (CLAUDE.md -> AGENTS.md), and once
        if path in seen:
            written.append(str(link))
            continue
        seen.add(path)
        text = path.read_text() if path.is_file() else ""
        span = _block_span(text)
        if span:
            new = text[:span[0]] + _BLOCK + text[span[1]:]
        elif text.strip():
            new = text.rstrip("\n") + "\n\n" + _BLOCK + "\n"
        else:
            new = _BLOCK + "\n"
        tmp = path.with_name(path.name + ".tmp")
        tmp.write_text(new)
        os.replace(tmp, path)
        written.append(str(link))
    return written


def missing_instructions(project: Path) -> list:
    out = []
    for name in INSTRUCTION_FILES:
        try:
            ok = _block_span((Path(project) / name).read_text()) is not None
        except OSError:
            ok = False
        if not ok:
            out.append(name)
    return out


def run_dir(project: Path, run_id: str) -> Path:
    _check_name(run_id, "run id")
    return Path(project) / "runs" / run_id


def bare_dir(project: Path) -> Path:
    return Path(project) / "bare-repos"


def _phase(run: Path):
    try:
        data = json.loads((run / ".kinetic" / "state.json").read_text())
    except (OSError, ValueError):
        return None
    return data.get("phase") if isinstance(data, dict) else None


def run_for_path(path):
    p = Path(os.path.realpath(path))
    project = find(p)
    if project is None:
        return None
    parts = p.relative_to(project).parts
    if len(parts) < 2 or parts[0] != "runs":
        return None
    run = project / "runs" / parts[1]
    return run if (run / ".kinetic" / "state.json").is_file() else None


def open_runs(project: Path) -> list:
    out = []
    runs = Path(project) / "runs"
    for d in sorted(runs.iterdir()) if runs.is_dir() else []:
        if (d / ".kinetic" / "state.json").is_file():
            phase = _phase(d)
            if phase != "done":
                out.append({"run_id": d.name, "run_dir": str(d), "phase": phase})
    return out


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="kinetic.project")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("find")
    sp = sub.add_parser("create")
    sp.add_argument("--parent", required=True)
    sp.add_argument("--name", required=True)
    sp = sub.add_parser("update")
    sp.add_argument("--json", required=True)
    sub.add_parser("runs")
    sp = sub.add_parser("run-dir")
    sp.add_argument("--id", required=True)
    sub.add_parser("instructions")
    sub.add_parser("start-session")
    sub.add_parser("current-run")
    sp = sub.add_parser("use-run")
    sp.add_argument("--run", required=True)
    a = p.parse_args(argv)
    try:
        if a.cmd == "create":
            out = {"project": str(create(Path(a.parent), a.name))}
        elif a.cmd == "start-session":
            out = {"ok": True}  # a hook reacts to the command; nothing is written here
        else:
            proj = require()
            if a.cmd == "find":
                out = {"project": str(proj)}
            elif a.cmd == "update":
                changes = json.loads(a.json)
                if not isinstance(changes, dict):
                    raise ValueError("--json must be a JSON object")
                out = update(proj, changes)
            elif a.cmd == "runs":
                out = open_runs(proj)
            elif a.cmd == "run-dir":
                rd = run_dir(proj, a.id)
                out = {"run_dir": str(rd), "exists": rd.exists()}
            elif a.cmd == "instructions":
                out = {"written": write_instructions(proj)}
            elif a.cmd == "current-run":
                r = run_for_path(os.getcwd())
                out = {"run_dir": str(r) if r else None}
            else:
                r = run_for_path(a.run)
                if r is None:
                    raise ValueError(f"not a run of a kinetic project: {a.run}")
                out = {"run_dir": str(r)}
    except NoProject as e:
        print(str(e), file=sys.stderr)
        return 2
    except (ValueError, OSError, config.ConfigError) as e:
        print(str(e), file=sys.stderr)
        return 1
    print(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
