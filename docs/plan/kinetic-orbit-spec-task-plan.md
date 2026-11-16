# Kinetic: the Spec as an Orbit Task Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** With the `orbit` tracker, a run's spec lives on the Orbit Board as a top-level `epic` Task, its slices are the Task's SubTasks, and its tickets are the slices' SubTasks, so `orbit list --type epic` is the one place for every spec and `orbit tree <spec>` shows spec, slices and tickets. The local tracker and every other tracker behave as today.

**Architecture:** The ticket store interface gains two methods, `publish_spec` and `sync_spec`; the local store implements them as no-ops. The Orbit store publishes the spec Task when `specify` ends, parents slices under it when the breakdown gate publishes, and mirrors the spec gate and the end of the run onto the Task. `spec.md` stays the original; the Board holds a synchronized copy. `kinetic trace` keeps reading run state.

**Tech Stack:** Python 3 (stdlib), `unittest`, the `orbit` CLI (0.10.16 or later: nested SubTasks), the existing `kinetic <module>` command layout.

**Repository:** kinetic (this repo). Depends on Orbit PR 27 (`board.max_subtask_depth`), already merged and released as 0.10.16. Follows [orbit-nested-subtasks-plan.md](orbit-nested-subtasks-plan.md); the `spec` field and `kinetic trace` came from PR 14.

## Decisions (resolved before writing this plan)

| Question | Decision | Evidence |
|---|---|---|
| When does the spec Task appear? | At the end of `specify`, after lint, not at the breakdown gate. The spec exists before the gate, and the Board is the central place. | `skills/specify/SKILL.md` publishes after lint. |
| Orbit status of a spec while the run is open | `todo` with the hold label (`kinetic`), never claimed. Progress goes in `waiting_on` and in the `kinetic` Annotation. | A claim carries a lease that lapses during a long build and would show a stale claim; `waiting_on` works on a `todo` Task, `orbit ready` does not offer it, `orbit list --waiting` lists it, and `done` works without a claim (probed on a scratch Board with the merged binary). |
| Which text is authoritative? | `<run_dir>/spec.md`. The Board body is a copy rewritten by the batch `update` op on every publish. | `spec_lint` reads the file; the Task body has no size limit (tested up to 5 MB with `--dry-run`). |
| How does kinetic know the Board allows three levels? | It reads `board.max_subtask_depth` from the Board's `config.toml`, the way `_hold_labels` reads `non_ready_labels`. `orbit export` does not expose it. Kinetic never edits the Board config; `doctor` prints the fix. | `orbit export` keys: `board` (branch, prefix, counts), `schema_version`, `tasks`. |
| Runs already published with `epic` slices | Keep working: a slice is recognized by its Annotation `role: slice`, or, for old runs, by `type: epic` with the run label. | `orbit_store._read_published` today treats every `epic` as a slice. |
| Run cancelled or abandoned | Out of scope: `state.py` has no cancel path. The spec Task stays `todo`. | `grep -i cancel scripts/kinetic/state.py` finds nothing. |

A probe on a scratch Board at `max_subtask_depth = 2` confirmed one `orbit batch` can add the spec (type `epic`, body, label, Annotation), its slice and its ticket (parents by `key:`), and a later batch can `update` the body and `annotate` the spec by `key:`; `orbit tree` shows the three levels.

## Global Constraints

- Kinetic's core stays independent of Orbit: only `scripts/kinetic/orbit_store.py` may name it (`tests/test_vocabulary.py` scans `scripts`, `hooks`, `skills`, `agents`, `templates`). New behavior goes through the ticket store interface; skill text says "the ticket store", never "Orbit".
- Local and every non-Orbit tracker are unchanged in behavior and output.
- A spec Task has exactly this shape: type `epic`, no parent, source key `kinetic:<run id>:spec`, labels `names.run` and `names.spec` and the hold label `names.hold`, Annotation `kinetic` = `{"role": "spec", "run": "<run id>", "phase": "<phase>"}`, `waiting_on` only while the spec gate is open.
- A slice Task: no `type` (a plain Task), parent the spec, Annotation `kinetic` = `{"role": "slice", "spec": "<spec id>"}`.
- A ticket's Annotation keeps today's shape (no `role` key): `to_ticket` and `_fields` must not change for tickets.
- No new Orbit change. Everything uses `batch`, Annotations, labels, `waiting_on` and nested SubTasks that already exist.
- `state.spec` holds the spec reference: `spec.md` for the local store, the Task ID for Orbit.
- Minimum Orbit version for the `orbit` tracker becomes 0.10.16. The current messages say "0.11.0 or later", which is not a released version; fix them in the same task.
- Docs travel with each commit: `README.md`, `GLOSSARY.md`, `trackers/orbit.md`, the skill files, `docs/specs/kinetic-orbit-tracker-design.md` if it states the old shape.

## Review Focus

- A published run of the old shape (slices as `epic`, no spec Task) still reads, builds and closes.
- A failed publish leaves nothing half-written: the spec Task and the staged tickets are independent, and republishing is safe (source keys).
- `reopen` of a finished ticket reopens its slice and the spec (Orbit's nested `reopen` does this); kinetic must not fight it.
- `tests/test_vocabulary.py` still passes.

## Files

- `scripts/kinetic/ticket_store.py` (interface, local store), `scripts/kinetic/orbit_store.py` (spec publish, slice parenting, health), `scripts/kinetic/state.py` (`publish_spec`, `record_gate` hook, CLI), `scripts/kinetic/trace.py` (no change expected).
- `skills/specify/SKILL.md`, `skills/decompose/SKILL.md`, `skills/run/guides/gates.md`.
- `trackers/orbit.md` (operations, default names), `GLOSSARY.md`, `README.md`.
- Tests: `tests/test_ticket_store.py`, `tests/test_orbit_store.py`, `tests/test_state.py`, `tests/test_trace.py`, `tests/test_skill_text.py`, `tests/test_vocabulary.py`; smoke notes in `docs/smoke/`.

---

### Task 1: Orbit prerequisites in `doctor`

**Files:** `scripts/kinetic/orbit_store.py`, `tests/test_orbit_store.py`

- [ ] Write failing tests for `health`: a Board whose `config.toml` has no `max_subtask_depth` (or 1) gives a row `orbit-depth` with status `fail` and the fix `add max_subtask_depth = 2 under [board] in the Board's config.toml`; a value of 2 or more passes; an unreadable config gives `warn`.
- [ ] Add `_max_depth(anchor)` next to `_hold_labels` (same candidate paths, `re.search(r"max_subtask_depth\s*=\s*(\d+)")`), and the `orbit-depth` row after `orbit-schema`.
- [ ] Replace "0.11.0 or later" with "0.10.16 or later" in `call` and `health` messages, and make `orbit_available()` in `tests/test_orbit_store.py` also require 0.10.16 (parse `orbit --version`) so older binaries skip the Orbit tests.
- [ ] In `_fix`, map Orbit's depth refusal (`INVALID_ARGUMENT` mentioning `board.max_subtask_depth`) to the same fix text, so a publish on a one-level Board fails with an actionable message.
- [ ] Commit: `kinetic-spec-task: doctor checks the Board allows nested SubTasks`.

### Task 2: Ticket store interface for the spec

**Files:** `scripts/kinetic/ticket_store.py`, `scripts/kinetic/state.py`, `tests/test_ticket_store.py`, `tests/test_state.py`

- [ ] Write failing tests: `LocalTicketStore.publish_spec(...)` returns `{"ref": "spec.md", "published": False}`; `sync_spec` is a no-op; `state.publish_spec(run_dir, title)` reads `<run_dir>/spec.md`, calls the run's store, records the returned ref with `set_spec`, and returns it; it fails with a clear message when `spec.md` is missing or empty; the CLI `kinetic state publish-spec --run <dir> --title "<title>"` prints the JSON.
- [ ] Add to the `TicketStore` Protocol: `publish_spec(self, run_dir, st, title, body) -> dict` (returns `{"ref", "published"}`) and `sync_spec(self, run_dir, st, event) -> None`, where `event` is one of `"gate-approved"`, `"gate-rejected"`, `"done"`. Local implements both trivially.
- [ ] `state.publish_spec` is the only caller; it runs under the state lock like `publish_tickets`.
- [ ] Commit: `kinetic-spec-task: the ticket store publishes the spec`.

### Task 3: Orbit store publishes the spec Task

**Files:** `scripts/kinetic/orbit_store.py`, `trackers/orbit.md`, `tests/test_orbit_store.py`

- [ ] Write failing tests against a real Board at depth 2: `publish_spec` creates one `epic` Task with the body of `spec.md`, the labels and Annotation from Global Constraints and `waiting_on = "kinetic: spec approval"`, and returns its Task ID; running it again creates nothing new and rewrites the body when `spec.md` changed; `orbit list --type epic` shows it; `orbit ready` does not offer it.
- [ ] Add `"spec": "kinetic-spec"` to the default names in `trackers/orbit.md` and add `spec_ops(run_dir, st, title, body)` to `orbit_store.py`: one `add` op with `key` `kinetic:<run id>:spec` (an existing key matches the Task, so the `update` op that follows carries the new body and Annotation).
- [ ] The Task ID returns to `state.spec`; also store it in the store config (`self.config["spec_task"]`) for later calls.
- [ ] Commit: `kinetic-spec-task: publish the spec as an epic Task`.

### Task 4: Slices under the spec

**Files:** `scripts/kinetic/orbit_store.py`, `tests/test_orbit_store.py`

- [ ] Write failing tests on a depth-2 Board: after `publish_tickets` the slice Task has no `type: epic`, its parent is the spec Task and its Annotation is `{"role": "slice", "spec": <id>}`; each ticket's parent is its slice; `orbit tree <spec>` shows three levels; closing the last ticket closes the slice and then the spec in the same batch; reopening a done ticket reopens the slice and the spec (assert on the Board, not on kinetic's return value).
- [ ] Old-shape run: a Board with `epic` slices and no spec Task reads, builds and closes exactly as before (keep the existing tests unchanged and green).
- [ ] `_add_ops`: slice `add` gets `parent` from the stored spec Task ID (a real ID, since the spec was published earlier) and the new Annotation, and drops `"type": "epic"` when a spec Task exists; with no spec Task it keeps today's op.
- [ ] `_read_published` and `published_slices`: a slice is a Task whose Annotation says `role: slice`, or, for old runs, `type == "epic"` carrying the run label; skip the spec Task (`role: spec`) when rebuilding tickets.
- [ ] `_close_slices`: when every published slice is done in the same batch, append the spec's `done` op (by the run's holder, no claim needed) and `sync_spec(..., "done")` becomes a no-op for Orbit because this op already did it.
- [ ] Commit: `kinetic-spec-task: parent slices under the spec Task`.

### Task 5: Spec lifecycle mirrored on the Task

**Files:** `scripts/kinetic/state.py`, `scripts/kinetic/orbit_store.py`, `tests/test_state.py`, `tests/test_orbit_store.py`

- [ ] Write failing tests: `record_gate` for `spec` with `approved=True` clears `waiting_on`, sets Annotation `phase: "approved"` and keeps the hold label; `approved=False` clears `waiting_on` and sets `phase: "revising"`; publishing again sets `waiting_on` back and `phase: "awaiting-approval"`; `breakdown` approval sets `phase: "building"`; the final close sets the Task `done`. A local run calls none of this.
- [ ] Hook `record_gate` (after the state write, outside the state lock's critical write) to `store.sync_spec(run_dir, st, "gate-approved" | "gate-rejected")` for the `spec` gate, and a `"building"` update after `breakdown`. Failures from the store raise as `TicketStoreError` after the gate is recorded, with the same message style as `publish_tickets`, so a Board outage never loses an approval.
- [ ] Commit: `kinetic-spec-task: mirror the spec gate on the Task`.

### Task 6: Skills, docs and vocabulary

**Files:** `skills/specify/SKILL.md`, `skills/decompose/SKILL.md`, `skills/run/guides/gates.md`, `trackers/orbit.md`, `GLOSSARY.md`, `README.md`, `tests/test_skill_text.py`, `tests/test_vocabulary.py`

- [ ] Write failing tests: the specify skill tells the agent to run `kinetic state publish-spec` after lint, and no scanned file outside `orbit_store.py` names Orbit.
- [ ] `specify` step 5: after lint, run `kinetic state publish-spec --run <run_dir> --title "<title>"`. If its JSON says `published` is true, the spec is on the ticket store's tracker and nothing more is needed; otherwise follow the adapter as today (remote issue tracker or `spec.md`) and then `kinetic state set-spec`. Remove the manual `set-spec` for the published case.
- [ ] `decompose`: say that slices are children of the spec when the ticket store holds one; `kinetic trace` walks the result.
- [ ] `gates.md`: the spec gate shows "the tracker reference or `<run_dir>/spec.md`" as today; add that on approval or rejection the ticket store updates the spec.
- [ ] `trackers/orbit.md`: new operations "create spec" and "sync spec" and the shape of each Task; a line that the Board must allow three levels (`max_subtask_depth = 2`) and that `doctor` checks it; state that a run whose ref is an existing Orbit Task still links it with `related` and does not nest the spec under it (an `epic` is top-level only).
- [ ] `GLOSSARY.md`: **Ticket store** also holds the run's spec; add **Spec Task**. Update `docs/specs/kinetic-orbit-tracker-design.md` where it says slices are `epic` Parent Tasks.
- [ ] Commit: `kinetic-spec-task: skills and docs for the spec Task`.

### Task 7: End to end and compatibility

**Files:** `tests/test_orbit_store.py`, `tests/test_trace.py`, `docs/smoke/`

- [ ] Run a full cycle on a depth-2 Board in a test: init, `publish-spec`, spec gate approved, slices and tickets staged, breakdown approved (publish), tickets done, spec `done`; `kinetic trace` prints the spec Task ID, the slice IDs and the ticket IDs; `orbit tree` shows the same tree.
- [ ] A run on a depth-1 Board fails at `publish-spec` with the actionable message from Task 1, leaves no Task behind, and a retry after the Board is fixed succeeds.
- [ ] Add a smoke note to `docs/smoke/` with the manual check: `orbit list --type epic`, `orbit tree <spec>`, `orbit show <spec>` (Annotation and `waiting_on`).
- [ ] Run the whole suite (`python3 -m unittest discover -s tests -t .`) with the merged Orbit binary on `PATH` and with Orbit absent (Orbit tests skip).
- [ ] Commit: `kinetic-spec-task: end-to-end and compatibility tests`.

## Risks

- **Slice recognition.** `_read_published` is the place most likely to mix up a spec, a slice and a ticket. The two-shape tests in Task 4 are the guard; keep the old-shape tests untouched.
- **Board config is manual.** Kinetic never edits it, so a first run on a new Board fails until the user adds one line. `doctor` and the publish error say exactly which line. If that proves annoying, a later change can offer to write it with confirmation.
- **Shared Boards.** A spec Task carries the hold label, so `orbit ready` never offers it, but other tools running `orbit list` see an extra epic with a `kinetic` Annotation. That is the intended central listing; check it on a Board shared with another tool.
- **Two copies of the spec.** An edit made on the Board (`orbit update`) is overwritten by the next publish. Document it in the Task body's first line ("generated from spec.md; edit the file").
- **Unverified.** Not read in this plan: `tests/test_e2e_scripts.py` and `docs/smoke/` scripts, which may assume epic slices; check them in Task 7 and update if so.
