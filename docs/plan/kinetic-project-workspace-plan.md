# Kinetic project workspace Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Kinetic keeps each project in its own folder (`.kinetic/`, `bare-repos/`, `runs/`), created from a conversation, with every run self-contained and found from the current directory.

**Architecture:** One new stdlib module, `scripts/kinetic/project.py`, owns project resolution (walk up to `.kinetic/`), creation, the project config file, and run lookup. Every module that hard-codes `~/.config/kinetic/repos.json` or project-level keys of `settings.json` takes its path from `project.config_path()` instead. The global active-run pointer is replaced by "run from cwd, else this session's binding in the project". A new `start` skill carries the conversation-to-project flow; `init` and `bare-setup` are deleted.

**Tech Stack:** Python 3.9 stdlib only, `unittest`, markdown skills, Python hooks.

**Spec:** [docs/specs/kinetic-project-workspace-design.md](../specs/kinetic-project-workspace-design.md)

## Global Constraints

- Python 3.9 stdlib only. Every subcommand prints JSON on stdout; errors go to stderr with exit 1 (exit 2 for "no project here").
- Project layout: `<project>/.kinetic/config.json`, `<project>/.kinetic/sessions/`, `<project>/bare-repos/<repo>.git/`, `<project>/runs/<run-id>/`.
- Run layout: user documents at the top (`spec.md`, `discovery.md`, `issues/`, `reviews/`, `docs/`, `needs-human/`), `worktrees/<repo>/` and `worktrees/<repo>-<ticket>/`, bookkeeping in `<run>/.kinetic/` (`state.json`, `state.lock`, `reports/`, `logs/`, `touch/`, staged tickets).
- `config.json` carries `"version": 1`. A higher version or bad JSON is refused before anything is written. Writes are atomic (tmp file plus `os.replace`) and keep unknown keys.
- Project keys (in `config.json`, top level): `repos` (per repo: `path`, `verify`, `verify_ticket`, `setup`, `publish_snapshot`, `pin`, `base_branch`), `tracker`, `tracker_settings`, `tracker_names`, `tracker_description`, `pr_template`.
- Machine keys stay in `~/.config/kinetic/settings.json`: `harness`, clone path, install manifest, `repo_roots`, `github_orgs`, `catalog_dir`. The two key sets are disjoint, so no merge is needed.
- No backward compatibility: `~/.config/kinetic/repos.json`, `workspace_root`, `bare_dir` and `~/.config/kinetic/active-run` are no longer read or written, and their tests go.
- Project resolution uses `os.path.realpath` and requires `.kinetic` to be a directory (a repo's `.kinetic.json` file never matches).
- A project is never created inside a git work tree.
- Skill text names no Claude-only tool, no concrete model in the body, and no `${CLAUDE_PLUGIN_ROOT}` (enforced by `tests/test_skill_text.py`).
- Tests: one module with `PYTHONPATH=scripts python3 -m unittest tests.test_<name>`; the full suite with `PYTHONPATH=scripts python3 -m unittest discover -s tests`.
- Commit messages start with `kinetic-project-workspace: `. No AI co-author trailer.

## Review Focus

- **A session at the project root with two open runs and no binding:** hooks must return no active run, never "the first one". Test in Task 4.
- **A worktree under `runs/<id>/worktrees/<repo>/`** (its own `.git` file) must still resolve to the project and to run `<id>`. Test in Task 1 and Task 4.
- **macOS temp paths** (`/var/...` vs `/private/var/...`): resolution and "cwd inside run" checks agree after `realpath`. Test in Task 1.
- **`create` on an existing project, a non-empty folder, or inside a git repo** is refused and writes nothing. Test in Task 1.
- **A failing `setup`** leaves the worktree in place, logs the output and reports `ok: false`, and the ticket is blocked, not silently built. Test in Task 6.

---

### Task 1: `project` module

**Files:**
- Create: `scripts/kinetic/project.py`
- Test: `tests/test_project.py`

**Interfaces:**
- Produces:
  - `class NoProject(Exception)`
  - `find(cwd=None) -> Path | None`: nearest ancestor (realpath) holding a `.kinetic` directory.
  - `require(cwd=None) -> Path`: `find` or raise `NoProject("no kinetic project here; start one with /kinetic:start")`.
  - `config_path(cwd=None) -> Path`: `require(cwd) / ".kinetic" / "config.json"`.
  - `load(project: Path) -> dict` and `update(project: Path, changes: dict) -> dict` (atomic, keeps unknown keys, refuses `version > 1` with `config.ConfigError`).
  - `create(parent: Path, name: str) -> Path`: makes `<parent>/<name>/` with `.kinetic/config.json` (`{"version": 1, "repos": {}}`), `.kinetic/sessions/`, `bare-repos/`, `runs/`.
  - `run_dir(project: Path, run_id: str) -> Path` and `bare_dir(project: Path) -> Path`.
  - `run_for_path(path) -> Path | None`: the `runs/<id>` folder that contains `path`, if it has `.kinetic/state.json`.
  - `open_runs(project: Path) -> list[dict]`: `{run_id, run_dir, phase}` for runs whose phase is not `done`.
  - CLI `kinetic project find | create --parent P --name N | update --json OBJ | runs | run-dir --id ID | current-run | use-run --run DIR`. `use-run` only validates and prints `{"run_dir"}`; the hook in Task 4 records the binding.

- [ ] **Step 1: Write the failing tests**

`test_find_from_root_run_and_worktree` (a project with `runs/r1/worktrees/api` holding a `.git` file; `find` from each equals the project realpath), `test_find_ignores_kinetic_json_file`, `test_find_realpath_tmp` (call with the non-realpath temp dir), `test_create_layout`, `test_create_refuses_existing_nonempty_and_inside_git` (each raises `ValueError`, nothing created), `test_update_atomic_keeps_unknown_keys`, `test_load_refuses_bad_json_and_newer_version`, `test_run_for_path_and_open_runs`, `test_require_raises_no_project`, `test_cli_no_project_exit_2`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_project -v`
Expected: FAIL, `No module named 'kinetic.project'`.

- [ ] **Step 3: Implement `scripts/kinetic/project.py`**

Reuse `config._load` for reading. "Inside a git work tree" is `git -C <parent> rev-parse --is-inside-work-tree` returning `true`. A folder counts as non-empty when it has any entry.

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_project -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic/project.py tests/test_project.py
git commit -m "kinetic-project-workspace: project module resolves, creates and lists runs"
```

### Task 2: Repo config and project settings come from the project

**Files:**
- Modify: `scripts/kinetic/config.py`, `scripts/kinetic/discover.py`, `scripts/kinetic/tracker.py`, `scripts/kinetic/verify.py`, `scripts/kinetic/gitops.py:128-136`, `scripts/kinetic/doctor.py:585-590,713`, `scripts/kinetic/orbit_store.py:205-216,399`
- Test: `tests/test_config.py`, `tests/test_discover.py`, `tests/test_tracker.py`, `tests/test_verify.py`, `tests/test_doctor.py`, `tests/test_orbit_store.py`

**Interfaces:**
- Consumes: `project.config_path`, `project.update`, `project.find`.
- Produces:
  - `config.resolve(repo_name, repo_path, project_config: Path) -> dict` reads `_load(project_config)["repos"][repo_name]`, merged with `<repo>/.kinetic.json` (repo wins, as today). Returns the existing keys plus `verify_ticket` (default `None`) and `setup` (default `None`). `DEFAULT_CENTRAL` is deleted.
  - `discover.register(name, path, verify, publish_snapshot=None, pin=None, setup=None, verify_ticket=None, *, project_config: Path) -> dict` writes under `repos` through `project.update`. `discover.SETTINGS` stays global and holds only `repo_roots` and `github_orgs`. `discover.REPOS` is deleted.
  - `tracker.resolve(settings: Path, plugin_root=ROOT)` keeps its signature. `DEFAULT_SETTINGS` is deleted. Every CLI `main` passes `project.config_path()` instead, and with no project it resolves `local`.
  - `orbit_store`: with no `orbit_dir`, the Board anchor is the project folder, not `repos[0]`.
  - `doctor` outside a project checks only the machine. Inside one, it reads repos from the project config.

- [ ] **Step 1: Update the tests first**

Replace the `central=` fixtures with a temp project from `project.create`. New tests: `test_resolve_reads_project_repos`, `test_resolve_new_keys_default_none`, `test_register_writes_project_config_keeps_other_keys`, `test_tracker_cli_without_project_is_local`, `test_orbit_anchor_defaults_to_project`, and `test_doctor_outside_project_machine_only`. Delete the tests that assert `~/.config/kinetic/repos.json` or project keys in the global `settings.json`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_config tests.test_discover tests.test_tracker tests.test_verify tests.test_doctor tests.test_orbit_store -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement the changes listed in Interfaces**

`verify.run` and `gitops.pin` take `project_config: Path` in place of `central`. Their CLIs pass `project.config_path(a.run)`, resolved from the run dir, so they work from any cwd.

- [ ] **Step 4: Run them and see them pass, then the full suite**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS. A remaining failure that references a deleted constant points to a caller this task must also fix: `rtk grep -rn "DEFAULT_CENTRAL\|DEFAULT_SETTINGS\|discover.REPOS" scripts hooks tests` must return nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic tests
git commit -m "kinetic-project-workspace: repo config and tracker settings live in the project"
```

### Task 3: Bare repos live in the project; new repos on GitHub

**Files:**
- Modify: `scripts/kinetic/bare.py`
- Delete: `skills/bare-setup/`
- Test: `tests/test_bare.py`

**Interfaces:**
- Consumes: `project.require`, `project.bare_dir`.
- Produces:
  - `bare.show`, `bare.set_dir`, `bare.detect`, `_scan`, `_cwd_container`, `_local_md_hint` and the `detect|set|show` subcommands are deleted.
  - `kinetic bare list|clone|refresh` use `project.bare_dir(project.require())`. `clone` stores `<name>.git`.
  - `bare.create(bare_dir, owner: str, name: str, visibility: str) -> dict` runs `gh repo create <owner>/<name> --<visibility> --add-readme`, then `clone(bare_dir, repo=f"{owner}/{name}")`. `visibility` must be `private`, `internal` or `public`.
  - `bare.gh_ready() -> bool`: `gh` is on PATH and `gh auth status` exits 0. CLI `kinetic bare gh-ready` prints `{"ready": bool}`.

- [ ] **Step 1: Write the failing tests**

`test_clone_into_project_bare_repos` (from a local remote, `name.git` under `bare-repos/`), `test_create_calls_gh_then_clones` (stub `_run`; assert the exact `gh repo create o/n --private --add-readme` argv precedes the clone), `test_create_rejects_bad_visibility`, `test_gh_ready_false_without_gh` (PATH emptied), `test_cli_outside_project_exit_2`. Delete the detect, set and show tests.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_bare -v`
Expected: FAIL.

- [ ] **Step 3: Implement the changes listed in Interfaces, then delete `skills/bare-setup/`**

- [ ] **Step 4: Run the tests and see them pass**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_bare tests.test_skill_text -v`
Expected: PASS. Remove `bare-setup` from `test_skill_text.py`'s user-only list.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic/bare.py tests skills
git commit -m "kinetic-project-workspace: bare clones live in the project; create a repo on GitHub"
```

### Task 4: Run bookkeeping in `<run>/.kinetic/`, active run from cwd or session

**Files:**
- Modify: `scripts/kinetic/state.py:30-31,111-125,439-441,536`, `scripts/kinetic/verify.py` (logs dir), `scripts/kinetic/gitops.py` (logs dir), `scripts/kinetic/next_step.py`, `hooks/_common.py:234-300`, `hooks/claim_run.py`, `hooks/orient.py`
- Test: `tests/test_state.py`, `tests/test_next_step.py`, `tests/test_hooks.py`

**Interfaces:**
- Consumes: `project.find`, `project.run_for_path`, `project.open_runs`.
- Produces:
  - `state.internal(run_dir) -> Path` (`<run_dir>/.kinetic`). `state.json`, `state.lock`, `reports/`, `logs/` and `touch/` move under it. `stale_running` reads `state.internal(run_dir) / "reports"`.
  - `state.set_ticket_store(run_dir, record: dict)`: allowed only while the run has no tickets, otherwise `ValueError`. CLI `kinetic state set-ticket-store --run D --record JSON`.
  - `_common.active_run(inp, tickets=True)` resolves, in order: (1) `project.run_for_path(cwd)`; (2) the project from cwd plus `<project>/.kinetic/sessions/<session_id>`, whose content is a run dir; (3) `None`. `pointer_run_dir` is deleted, and `_in_scope` is kept.
  - `claim_run.main`: on a Bash command containing `kinetic project use-run`, it parses `--run` with `shlex`, writes `<project>/.kinetic/sessions/<session_id>` and calls `state.set_owner`.
  - `next_step`: `--run`, else `project.run_for_path(cwd)`, else exactly one open run, else `{"command": "/kinetic:run", "reason": "N open runs; open one from its folder"}`. `active_run_file` is deleted.
  - `orient`: outside any project, it injects one line: `kinetic: no project here. If this conversation becomes a project (a goal, the systems involved, references), offer /kinetic:start.`

- [ ] **Step 1: Write the failing tests**

`test_state_files_under_internal`, `test_set_ticket_store_refused_with_tickets`, `test_active_run_from_worktree_cwd`, `test_active_run_from_session_binding`, `test_active_run_none_at_root_with_two_runs_no_binding`, `test_claim_run_writes_session_binding`, `test_next_step_two_open_runs`, `test_orient_outside_project_offers_start`. Delete the tests that write `~/.config/kinetic/active-run`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_state tests.test_next_step tests.test_hooks -v`
Expected: FAIL.

- [ ] **Step 3: Implement the changes listed in Interfaces**

The `touch_ticket` stamps go to `state.internal(run_dir) / "touch"`. The isolation hook allows the run dir as today, and the run's `.kinetic/` is inside it.

- [ ] **Step 4: Run the full suite and see it pass**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS. `rtk grep -rn "active-run" scripts hooks tests` returns nothing.

- [ ] **Step 5: Commit**

```bash
git add scripts hooks tests
git commit -m "kinetic-project-workspace: run state inside the run; active run from cwd or session"
```

### Task 5: `setup` command and ticket-scoped verify

**Files:**
- Modify: `scripts/kinetic/gitops.py`, `scripts/kinetic/verify.py`
- Test: `tests/test_gitops.py`, `tests/test_verify.py`

**Interfaces:**
- Consumes: `config.resolve` (Task 2), `state.internal` (Task 4).
- Produces:
  - `gitops.run_setup(run_dir, repo_name, worktree: Path, project_config: Path) -> dict` returns `{"ok": bool, "skipped": bool, "log_path": str | None}`. It runs `setup` with `cwd=worktree` and logs to `state.internal(run_dir)/logs/setup-<worktree name>-<ts>.log`. With no `setup` configured: `{"ok": True, "skipped": True, "log_path": None}`. CLI `kinetic gitops setup --run D --repo-name N --path P` exits 1 when not ok.
  - `verify.run(..., scope: str = "full")`: `scope="ticket"` uses `verify_ticket` when set, else `verify`. CLI `--scope {full,ticket}`.

- [ ] **Step 1: Write the failing tests**

`test_setup_skipped_when_unset`, `test_setup_failure_logs_and_reports` (`setup: "echo boom; exit 3"` gives `ok` False, the log contains `boom`, the worktree still exists), `test_verify_ticket_scope_uses_verify_ticket`, `test_verify_ticket_scope_falls_back_to_verify`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_gitops tests.test_verify -v`
Expected: FAIL.

- [ ] **Step 3: Implement `run_setup` and `scope` as listed**

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_gitops tests.test_verify -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic/gitops.py scripts/kinetic/verify.py tests
git commit -m "kinetic-project-workspace: per-repo setup after a worktree, ticket-scoped verify"
```

### Task 6: Skills: `start` replaces `init`; run, discover, decompose and build follow the project

**Files:**
- Create: `skills/start/SKILL.md`
- Delete: `skills/init/`
- Modify: `skills/run/SKILL.md`, `skills/discover/SKILL.md`, `skills/decompose/SKILL.md`, `skills/ask/SKILL.md`, `skills/cleanup/SKILL.md`, the build-loop guide under `skills/run/`, `agents/` (implementer verify call), `trackers/orbit.md`, `README.md` (Quick start, Configuration)
- Test: `tests/test_skill_text.py`

**Interfaces:**
- Consumes: every CLI from Tasks 1–5.
- Produces the skill behaviour the spec describes:
  - **`start`** (model-invocable). If `kinetic project find` succeeds, say so and hand off to `/kinetic:run`. Otherwise converse. Once the conversation has a goal, the systems involved and the references, ask once: "turn this into a project?". On yes, propose a name from the conversation and the folder (default `<cwd>/<name>`, refused inside a git repo), run `kinetic project create`, write `runs/<run-id>/docs/notes.md` and `docs/refs/` (copied files; links listed in `notes.md`), then continue as `/kinetic:run` on that run at the grill. Nothing is written before the yes.
  - **`run`** preflight: `kinetic project find` (exit 2 means tell the user to run `/kinetic:start` and stop). Run dir is `kinetic project run-dir --id <run-id>`. After `state init`, run `kinetic project use-run --run <run_dir>`. Recovery happens when `kinetic project current-run` names a run. Ticket worktrees get `kinetic gitops setup` after creation; on exit 1, set the ticket `blocked` with the log path.
  - **`discover`**: repos come from `bare-repos/` (`kinetic bare list`). An unknown repo asks "create on GitHub, or give me the URL?" (offer create only when `kinetic bare gh-ready` is true; for create, confirm owner, name and visibility). Register with `--setup` and `--verify-ticket` when given. Run `kinetic gitops setup` after each integration worktree.
  - **`decompose`**: before publishing, ask whether this run publishes to the tracker. If yes and there is no `tracker` in the project config, ask for the tracker and write it with `kinetic project update`, then `kinetic state set-ticket-store`. When the run's ref is an existing tracker task, slice parents are created as its SubTasks, and kinetic neither claims nor closes that task (it only comments with the run folder).
  - The **implementer** runs `kinetic verify --scope ticket`, and integration runs full.
  - `needs-human` reports are written to `<run_dir>/needs-human/<id>-<N>.md`. Implementer reports go to `<run_dir>/.kinetic/reports/`.
  - `kinetic project update --json '{...}'` (Task 1) is the only way skills change project config.

- [ ] **Step 1: Update `test_skill_text.py` first**

Add `start` to the skill list. Remove `init` and `bare-setup`. Assert that no skill text or README contains `~/.config/kinetic/repos.json`, `workspace_root`, `active-run` or `bare_dir`. Assert that `start` contains `kinetic project create` and does not contain `kinetic project create` before the confirmation sentence.

- [ ] **Step 2: Run it and see it fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_skill_text -v`
Expected: FAIL.

- [ ] **Step 3: Write `skills/start/SKILL.md`, delete `skills/init/`, edit the files listed**

- [ ] **Step 4: Run the full suite and see it pass**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add skills agents trackers README.md tests
git commit -m "kinetic-project-workspace: /kinetic:start creates the project; skills follow it"
```

### Task 7: End-to-end check and the Orbit Board outside a repo

**Files:**
- Modify: `tests/test_e2e_scripts.py`
- Create: `docs/smoke/project.md`

- [ ] **Step 1: Extend the e2e test**

`test_project_e2e`: `project create`, two local remotes cloned into `bare-repos/`, `register`, `state init` in `runs/r1`, integration and ticket worktrees cut from the bare repos, `gitops setup`, `verify --scope ticket`, merge. Assert the run folder holds all of it and nothing is written under the temp `HOME`'s `.config/kinetic` apart from machine keys.

- [ ] **Step 2: Run the full suite**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 3: Orbit Board outside a repo (manual smoke)**

In a temp project folder that is not a git repo, run `orbit init`, then `orbit list --json --dir <project>`. Record the result in `docs/smoke/project.md`. If `ok` is false, change the default anchor in `orbit_store` to `<project>/.kinetic/board/` (create it with `git init` plus `orbit init`), add `test_orbit_anchor_board_folder`, and rerun the suite.

- [ ] **Step 4: Commit**

```bash
git add tests docs/smoke/project.md scripts
git commit -m "kinetic-project-workspace: end-to-end project test and Orbit Board smoke"
```
