# Kinetic session ownership Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A conversation the user gives to kinetic stays with kinetic: the project folder says so, a hook refuses competing process skills, `/kinetic:start` marks the session before any project exists, phase skills started in the wrong place name the right command, and the user chooses the run id.

**Architecture:** `project.py` writes the instruction files and gains two small CLI commands. The hooks gain a machine-level start mark next to the existing run binding, written by `claim_run` when it sees `kinetic project start-session`, and a new pre-tool hook, `skill_guard.py`, that reads the skill name and decides from the session's run or mark. The harness profile reports the new guarantee. Skill text and `next_step` carry the rest.

**Tech Stack:** Python 3.9 stdlib only, `unittest`, markdown skills, Python hooks.

**Spec:** [docs/specs/kinetic-session-ownership-design.md](../specs/kinetic-session-ownership-design.md)

## Global Constraints

- Python 3.9 stdlib only. Every subcommand prints JSON on stdout; errors go to stderr with exit 1 (exit 2 for "no project here").
- Instruction files: `<project>/AGENTS.md` and `<project>/CLAUDE.md`, same text, between `<!-- kinetic:begin -->` and `<!-- kinetic:end -->`. The text is the block in the spec, section 1, verbatim.
- Start mark: `~/.config/kinetic/sessions/<session_id>`, written only when the call's working directory (and the session's launch dir) is in no project. The session id passes the same checks as `_common.session_file`.
- Sweep, run whenever a mark or a binding is written: a mark is deleted 7 days after it was written (mtime); a run binding is deleted only when its run is gone or in phase `done`, never by age.
- Skill guard: `deny` in a session working on a run, `ask` in a session that only carries the start mark, silent otherwise. It is never fail-closed: an unreadable skill name, a bad config or an exception lets the call through.
- Deny and ask reasons, verbatim: `kinetic run <id> is active (phase <p>). Next: <next_step command>.` and `This conversation was started with /kinetic:start. Allow <skill>?`. `<id>` is the run folder's name.
- Default deny list, in `hooks/skill_guard.json` under `"deny"`: `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:executing-plans`, `superpowers:subagent-driven-development`, `superpowers:using-git-worktrees`, `superpowers:finishing-a-development-branch`, and `to-spec`, `to-tickets`, `implement`, `implement-spec`, `wayfinder`, `grill-with-docs`, `grilling`, `grill-me`, `triage`, each also as `mattpocock-skills:<name>`. A project's `skill_guard.deny` (a list) replaces it for sessions working on a run of that project.
- Kinetic's own skills are never refused: any name starting with `kinetic:` or `kntc-`.
- Claude facts (probed 2026-11-15): a model-invoked skill fires the pre-tool hook with `tool_name: "Skill"` and `tool_input: {"skill": "<name>"}`; a slash command the user types fires no such event. Codex, OpenCode and Devin are not probed: no skill hook is registered there.
- Skill text names no Claude-only tool, no concrete model in the body, and no `${CLAUDE_PLUGIN_ROOT}` (enforced by `tests/test_skill_text.py`). Say "the harness's question tool".
- Tests: one module with `PYTHONPATH=scripts python3 -m unittest tests.test_<name>`; the full suite with `PYTHONPATH=scripts python3 -m unittest discover -s tests` (642 tests, OK, before this plan).
- Commit messages start with `kinetic-session-ownership: `. No AI co-author trailer.

## Review Focus

- **A run that has been open for more than 7 days:** its binding must survive the sweep. Test in Task 2.
- **An instruction file the user has edited:** `instructions` keeps their text and never duplicates the block on a second run. Test in Task 1.
- **A skill name in an unexpected shape** (`/superpowers:brainstorming` with a leading slash; `tool_input` missing or not an object): the first is matched, the second passes silently. Test in Task 3.
- **An unsafe session id** (`../x`) on `start-session`: nothing is written outside the sessions folder. Test in Task 2.
- **A malformed `skill_guard.deny`** (a string, a number): the default list is used; the hook neither crashes nor refuses everything. Test in Task 3.

---

### Task 1: Instruction files, `start-session` and `run-dir` existence in `project`

**Files:**
- Modify: `scripts/kinetic/project.py`, `scripts/kinetic/doctor.py`
- Test: `tests/test_project.py`, `tests/test_doctor.py`

**Interfaces:**
- Produces:
  - `project.INSTRUCTION_FILES = ("AGENTS.md", "CLAUDE.md")`, `project.INSTRUCTIONS_BEGIN = "<!-- kinetic:begin -->"`, `project.INSTRUCTIONS_END = "<!-- kinetic:end -->"`, `project.INSTRUCTIONS_TEXT` (the spec's block).
  - `project.write_instructions(project: Path) -> list[str]`: for each file, replace the text between the markers, or append the block (markers included) after a blank line, or create the file; atomic; returns the paths written.
  - `project.missing_instructions(project: Path) -> list[str]`: file names that are absent or hold no complete block.
  - `project.create` calls `write_instructions`.
  - CLI `kinetic project instructions` prints `{"written": [...]}`. CLI `kinetic project start-session` prints `{"ok": true}`, writes nothing, and exits 0 inside and outside a project. CLI `kinetic project run-dir --id ID` prints `{"run_dir": ..., "exists": bool}` (`exists` is true when the folder is there).
  - `doctor`: check `project-instructions`, `warn` naming the missing files with the fix `kinetic project instructions`; `pass` otherwise. Only inside a project.

- [ ] **Step 1: Write the failing tests**

`test_create_writes_instruction_files` (both files hold the block between the markers), `test_instructions_keeps_user_text_and_is_idempotent` (file with user text before and after an old block: the user text survives, the block is replaced, and a second call leaves exactly one block), `test_instructions_appends_when_no_block`, `test_missing_instructions`, `test_cli_start_session_ok_outside_project` (exit 0, `{"ok": true}`, no file created under the temp `HOME`), `test_cli_run_dir_reports_exists`, `test_doctor_project_instructions_warn_and_pass`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_project tests.test_doctor -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement the interfaces above**

`start-session` is handled before `require()` in `main`, like `create`.

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_project tests.test_doctor -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic/project.py scripts/kinetic/doctor.py tests/test_project.py tests/test_doctor.py
git commit -m "kinetic-session-ownership: project instruction files, start-session and run-dir exists"
```

### Task 2: Start mark and sweep in the hooks

**Files:**
- Modify: `hooks/_common.py`, `hooks/claim_run.py`
- Test: `tests/test_hooks.py`

**Interfaces:**
- Consumes: `project.find`, `project.open_runs`, `_common.session_file`, `_common._launch_dir`, `state.atomic_write`.
- Produces:
  - `_common.mark_file(sid) -> Path | None`: `Path.home() / ".config/kinetic/sessions" / sid`, `None` for a session id `session_file` would refuse.
  - `_common.has_mark(inp: dict) -> bool`: the mark file of `inp["session_id"]` exists.
  - `_common.MARK_DAYS = 7` and `_common.sweep_sessions(proj=None) -> None`: deletes marks whose mtime is older than `MARK_DAYS`; when `proj` is given, deletes each binding in `<proj>/.kinetic/sessions/` whose content is not the `run_dir` of a run in `project.open_runs(proj)`. Never raises.
  - `claim_run._kinetic_args(cmd: str, sub: str) -> list | None`: the words after the first `kinetic project <sub>` in `cmd` (any path to the `kinetic` executable), replacing `_run_arg`'s search; `_run_arg` keeps its behaviour on top of it.
  - `claim_run.main`: on `kinetic project start-session`, when the session id is usable and neither the call's cwd nor its launch dir is in a project, writes the mark (content: the UTC time, ISO) and calls `sweep_sessions()`. On `use-run`, after writing the binding, calls `sweep_sessions(proj)`.

- [ ] **Step 1: Write the failing tests**

`test_start_session_writes_mark_outside_project`, `test_start_session_inside_project_writes_nothing`, `test_start_session_via_plugin_bin_path` (`python3 /opt/kinetic/bin/kinetic project start-session`), `test_start_session_unsafe_session_id_writes_nothing` (session id `../x`; nothing appears under the temp `HOME`), `test_has_mark`, `test_sweep_deletes_mark_older_than_7_days_keeps_newer` (set mtimes with `os.utime`), `test_sweep_keeps_old_binding_of_open_run_and_drops_done_or_gone` (three bindings: an open run with an mtime 30 days old, a run in phase `done`, a path that no longer exists).

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_hooks -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement the interfaces above**

- [ ] **Step 4: Run them and see them pass**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_hooks -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add hooks/_common.py hooks/claim_run.py tests/test_hooks.py
git commit -m "kinetic-session-ownership: start mark and sweep of marks and bindings"
```

### Task 3: Skill guard hook and the harness guarantee

**Files:**
- Create: `hooks/skill_guard.py`, `hooks/skill_guard.json`
- Modify: `hooks/hooks.json`, `scripts/kinetic/harness.py`, `scripts/kinetic/install.py:294-327,700-707`
- Test: `tests/test_hooks.py`, `tests/test_harness.py`, `tests/test_install.py`

**Interfaces:**
- Consumes: `_common.active_run`, `_common.has_mark` (Task 2), `_common.pre_tool`, `_common.run`, `next_step.suggest`, `state.load`, `project.find`, `project.load`.
- Produces:
  - `hooks/skill_guard.json`: `{"deny": [...]}` with the default list from Global Constraints.
  - `skill_guard.skill_name(inp: dict) -> str | None`: `tool_input["skill"]` as a string with one leading `/` stripped; `None` when `tool_input` is not an object or has no string `skill`.
  - `skill_guard.deny_list(run_dir=None) -> set[str]`: the project's `skill_guard.deny` when `run_dir` is given and that key is a list of strings; else the default list.
  - `skill_guard.main(inp)`: the decision table in the spec, section 2. Registered with `_common.run(main)` (not fail-closed).
  - `hooks/hooks.json`: one more `PreToolUse` group, matcher `Skill`, running `skill_guard.py --harness claude`.
  - `harness.GUARANTEES` gains `"skill-ownership"` (comment: pre-tool refusal of other plugins' process skills). Each profile's `tools` gains `"skill"`: `"Skill"` for claude, `None` for devin, codex and opencode. Claude's `enforced` includes the new guarantee; the other three exclude it. `render` adds `skill=<name or none>` to the `Tools:` line.
  - `install._hook_spec(edit_matcher, shell, skill=None)` appends `("PreToolUse", f"^{skill}$", "skill_guard.py")` only when `skill` is set. The Devin and Codex specs pass their profile's `tools["skill"]`, so their generated hooks do not change today.

- [ ] **Step 1: Write the failing tests**

In `test_hooks`, using the hook as a subprocess like the isolation tests: `test_skill_guard_denies_listed_skill_in_run` (reason contains `kinetic run`, the run folder name, `phase` and `Next:`), `test_skill_guard_asks_in_marked_session` (reason is the ask text with the skill name), `test_skill_guard_silent_for_unlisted_kinetic_and_unmarked` (`superpowers:receiving-code-review`, `kinetic:grill`, `kntc-run`, and a listed skill in a session with no run and no mark), `test_skill_guard_leading_slash_matches`, `test_skill_guard_unreadable_input_passes` (`tool_input` missing, `tool_input: "x"`, `skill: 5`), `test_skill_guard_project_deny_list_replaces_default`, `test_skill_guard_malformed_project_deny_uses_default` (`"skill_guard": {"deny": "x"}` and `{"deny": 3}`), `test_hooks_json_wires_skill_guard`.
In `test_harness`: `test_skill_ownership_enforced_only_on_claude`, `test_render_names_skill_tool`.
In `test_install`: `test_devin_and_codex_hooks_have_no_skill_guard`.

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_hooks tests.test_harness tests.test_install -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement the interfaces above**

Order in `main`: unreadable name, kinetic's own, not on the list → return. Then `active_run(inp, tickets=False)`: found → load the full state for the phase and `next_step.suggest`, and `deny`. Else `has_mark(inp)` → `ask`. A run session uses `deny_list(run_dir)`; a marked session uses the default list.

- [ ] **Step 4: Run the full suite and see it pass**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS. Existing tests that assert the full `GUARANTEES` list or the exact `Tools:` line are updated to the new values.

- [ ] **Step 5: Commit**

```bash
git add hooks scripts/kinetic/harness.py scripts/kinetic/install.py tests
git commit -m "kinetic-session-ownership: skill guard hook and the skill-ownership guarantee"
```

### Task 4: Skill text, `next_step`, run-id question and docs

**Files:**
- Modify: `scripts/kinetic/next_step.py`, `skills/start/SKILL.md`, `skills/run/SKILL.md`, `skills/grill/SKILL.md`, `skills/discover/SKILL.md`, `skills/specify/SKILL.md`, `skills/decompose/SKILL.md`, `skills/ship/SKILL.md`, `skills/retro/SKILL.md`, `README.md`, `GLOSSARY.md`
- Create: `docs/smoke/skill-hook.md`
- Test: `tests/test_next_step.py`, `tests/test_skill_text.py`

**Interfaces:**
- Consumes: `kinetic project start-session`, `kinetic project run-dir --id` with `exists` (Task 1), `kinetic project instructions` (Task 1), the `skill-ownership` guarantee (Task 3).
- Produces:
  - `next_step._active()`: outside every project it returns `{"command": "/kinetic:start", "reason": "no kinetic project here"}`. Inside a project the behaviour is unchanged.
  - Each of `grill`, `discover`, `specify`, `decompose`, `ship`, `retro` starts its body, right after the title paragraph, with this line, verbatim: ``No `Run:` was passed by the orchestrator: run `kinetic next_step`, tell the user its `command` and `reason` in one line, and stop.``
  - `start`: a new first step, before the project check, runs `kinetic project start-session` and explains in one sentence that it marks this conversation as kinetic's. Step "First run" asks the run-id question before computing the run dir.
  - `run`: preflight asks the run-id question only when it creates a new run from an idea or a spec path; a ticket reference is the id without asking; an existing run is resumed.
  - The run-id question, in both skills, with the harness's question tool when the profile names one, else in chat: the recommended id first (marked recommended), then a typed id. A typed id is checked with `kinetic project run-dir --id <id>`: exit 1, or `"exists": true`, asks again with the reason.
  - `README.md`: the hooks section lists `skill_guard` and the `skill-ownership` guarantee (Enforced on Claude Code, Instructed elsewhere); the configuration section documents `skill_guard.deny` and the instruction files; the start section mentions the mark and the run-id question.
  - `GLOSSARY.md`, under "Projects and runs": **Start mark**: the record, kept on the machine, that a session was opened with the start skill; it keeps the conversation with kinetic before any project exists. _Avoid_: Chat mode, session flag.
  - `docs/smoke/skill-hook.md`: the Claude probe (date, how it was run, the two results from Global Constraints) and the open facts for Codex, OpenCode and Devin, each with the probe to run.
  - `docs/smoke/project.md` gains one open fact: whether each harness reads an instruction file from a parent folder when the session starts inside a worktree under `runs/`.

- [ ] **Step 1: Write the failing tests**

`test_next_step_outside_project_points_to_start`. In `test_skill_text`: `test_phase_skills_guard_line` (the verbatim line in all six), `test_start_marks_session_before_project_check` (`kinetic project start-session` appears before `kinetic project find`), `test_start_and_run_ask_run_id` (both name a recommended id, a typed id and `kinetic project run-dir --id`).

- [ ] **Step 2: Run them and see them fail**

Run: `PYTHONPATH=scripts python3 -m unittest tests.test_next_step tests.test_skill_text -v`
Expected: FAIL on the new tests.

- [ ] **Step 3: Implement `next_step`, edit the skills and the docs**

- [ ] **Step 4: Run the full suite and see it pass**

Run: `PYTHONPATH=scripts python3 -m unittest discover -s tests`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/kinetic/next_step.py skills README.md GLOSSARY.md docs/smoke/skill-hook.md tests
git commit -m "kinetic-session-ownership: start marks the session and asks the run id; phase skills point to the right command"
```
