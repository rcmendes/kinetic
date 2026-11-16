# Orbit Nested SubTasks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let an Orbit Board opt in to SubTasks that own SubTasks, so a Board can hold three levels (a top-level Task, its SubTasks, and their SubTasks) the way Jira (epic, story, sub-task) and Linear (sub-issues) do. A Board that does not opt in behaves exactly as today.

**Architecture:** One Board setting, `board.max_subtask_depth`, defaults to 1 (today's rule). `Graph` carries it, the way it already carries `non_ready_labels`, and every place that enforces "one level" compares a Task's depth (its `parent` hops from a top-level Task) with it. Hierarchical IDs extend to more segments (`orb-a0b1c2.d4b3df.e5f6g7`): a Task's parent is its ID minus the last segment. Readiness, `delete` and slug migration already work for a chain. `finish` (`done --force`, `cancel --force`) and `reopen` stop at one level and need to walk the chain.

**Tech Stack:** Rust (edition per `Cargo.toml`), clap, serde/serde_json/serde_yaml_ng, the `tests/common` `TestRepo` harness.

**Repository:** run this plan in the Orbit repository (`../orbit`), in a worktree as its `AGENTS.md` requires, branched from the line that has Batches, Source Keys and Annotations (`feat/batch-annotations`, or `main` once it is merged; `main` at v0.10.15 has no `src/ops.rs`). It is kept in kinetic's `docs/plan/` only because it was written from kinetic's needs, like [orbit-batch-annotations-plan.md](orbit-batch-annotations-plan.md).

**Spec:** none. Task 1 writes the ADR. The kinetic side is the last section and is a separate plan.

## Global Constraints

- No kinetic vocabulary (kinetic, slice, run) anywhere in Orbit code, help, docs, ADRs, tests or examples. Use Task, SubTask and Parent Task; examples talk about a release, a feature and a step. The word "ticket" already appears in Orbit's generated agent guides, so it is allowed there.
- Introduce no new structural noun. A SubTask that has SubTasks is a SubTask and a Parent Task at once; **Parent Task** becomes a role, not a kind. **Kind** stays `task` or `subtask`, derived from `parent`. `type` stays metadata, and `epic` stays top-level only. The glossary entries to avoid ("story", "Epic as a synonym for every Parent Task") stay.
- Setting name, exactly: `board.max_subtask_depth`, an integer from 1 to 4, default 1. Depth counts `parent` hops: a top-level Task is depth 0, its SubTask depth 1.
- The setting is omitted from `config.toml` when it is 1 (`skip_serializing_if`), so a default Board's config file stays byte-for-byte the same after any rewrite.
- Nothing readable today stops being readable: no change to `parent`, `depends_on`, the export `schema_version` (stays 3) or existing JSON fields. New JSON fields are additive.
- Reparenting stays unsupported.
- A default Board keeps rejecting a SubTask under a SubTask with the same error code (`INVALID_ARGUMENT`) and a message that still contains "one level" (`tests/subtasks.rs::add_parent_rejects_missing_nested_and_terminal_parents` asserts it) plus the setting name.
- Docs travel with each commit: `docs/GLOSSARY.md`, `docs/SPEC.md`, `docs/commands.md`, CLI `--help` in `src/help.rs`, `CHANGELOG.md`, and the managed AGENTS block template in `src/setup.rs`, whose "one level deep" wording becomes "one level deep by default".
- No new CLI command, so no new MCP tool (ADR 0004). MCP tool descriptions that say "Parent Task" for `--parent` filters stay accurate.

## Review Focus

- A default Board is unchanged in behavior: every existing test passes without edits.
- `done --force`, `cancel --force` and `reopen` over three levels.
- The ID grammar: a dot still marks a SubTask, and a legacy ID is still a Task that can parent SubTasks.
- Generated agent guides stay accurate for a default Board.

## What the code does today (verified by reading `feat/batch-annotations`)

| Area | Today | Needs change |
|---|---|---|
| `graph.rs` `validate` | `NESTED_SUBTASK` when a parent is itself a SubTask | yes: compare depth with the setting |
| `ops.rs` `add` | refuses a parent that is a SubTask (`"... SubTasks are one level deep"`) | yes |
| `task.rs` `is_valid_id`, `parent_id_from_id` | accept exactly one dot | yes: any number of segments |
| `task.rs` `generate_subtask_id` | `<parent>.<rand>` | no: already nests |
| `board.rs` `Board::parent_id`, `health.rs` `parent_id_agreement` | use `parent_id_from_id` | no: follow once it is generalized |
| `board.rs` `task_path` (`{id}.md`) | file name per Task ID, `file_stem` discovery | no: `a.b.c.md` works the same as `a.b.md`; add a test |
| `graph.rs` `is_ready`, `open_children`, `eligible_for_close`, `claim` gate | direct children only | no: a chain holds because a parent cannot finish with open children |
| `ops.rs` `finish` | `done --force` and `cancel --force` cancel the direct open children only | yes: cancel every open descendant |
| `ops.rs` `reopen`, `mutation.rs` `reopen` | reopens one Done parent, returns `Option<String>` | yes: walk every ancestor |
| `commands/crud.rs` `delete_subtree` | recursive over `children` | no: add a test |
| `commands/query.rs` `cmd_tree` | root must be top-level, lists direct children only | yes |
| `commands/query.rs` `list --parent`, `ready --parent` | reject a SubTask as the parent filter | yes: accept any Task; still filter by direct children |
| `commands/render.rs` list grouping | groups rows under their direct parent | no: add a test |
| `migrate.rs` `map_id` (`slug migrate`) | strips the old prefix, keeps the rest | no: segment-agnostic; add a test |
| `commands/slug.rs` SubTask count | counts IDs containing `.` | no |
| `github/`, `board_sync.rs` | SubTask maps to a GitHub sub-issue; `parent` is merged field by field; no depth logic found | no code change expected; add a sync test at depth 2 |
| MCP (`mcp.rs`) | tool descriptions only | no |

## Files

- `src/board.rs` (config field, validation), `src/graph.rs`, `src/ops.rs`, `src/mutation.rs`, `src/task.rs`, `src/health.rs`, `src/help.rs`, `src/cli.rs`, `src/setup.rs`.
- `src/commands/query.rs` (`tree`, `list`, `ready`), `src/commands/render.rs`, `src/commands/batch.rs`.
- Tests extend `tests/subtasks.rs` and add to `tests/doctor.rs`, `tests/batch.rs`, `tests/export.rs`, `tests/slug_migrate.rs`, `tests/remote.rs`.

---

### Task 1: ADR and glossary

**Files:** `docs/adr/0021-nested-subtasks-behind-a-board-setting.md`, `docs/GLOSSARY.md`, `docs/adr/0007-hierarchical-task-and-subtask-ids.md`, `docs/adr/0009-keep-task-vocabulary.md`

- [ ] Write ADR 0021. Context: Board owners want a Task, SubTask and sub-SubTask hierarchy like Jira and Linear, and the one-level rule blocks it. Decision: add `board.max_subtask_depth`, default 1, maximum 4. State that it partly reverses ADR 0007 ("one level deep") and the part of ADR 0009 that says Orbit deliberately has no arbitrary-depth hierarchy. Why: opt-in keeps every existing Board and tool valid; no new noun, so nothing in storage, JSON, MCP or Rust types is renamed. Alternatives: a new Task kind for the middle level (rejected: new schema and vocabulary for a name), unlimited depth (rejected: no use case, harder IDs and trees), a typed link instead of nesting (rejected: `parent` already carries roll-up and readiness).
- [ ] Add a "Superseded in part" status line to ADR 0007 and an "Amended" note to ADR 0009, each pointing to 0021. Do not rewrite their bodies.
- [ ] Glossary: reword **Parent Task** as a role ("A Task that has SubTasks"), keep **SubTask**, add **Depth** and **Max SubTask Depth**. Keep the "Avoid: story" entry.
- [ ] Commit: `docs: nested SubTasks behind a Board setting (ADR 0021)`.

### Task 2: The Board setting

**Files:** `src/board.rs`, `src/graph.rs`, `docs/SPEC.md`

- [ ] Write failing tests: a config with `max_subtask_depth = 2` loads and exposes 2; an absent field reads 1; 0, 5 and a non-integer fail with a config error that names `board.max_subtask_depth`; saving a config whose value is 1 does not write the key.
- [ ] Add the field to the `board` config struct next to `non_ready_labels` (`#[serde(default = ..., skip_serializing_if = ...)]`) and validate the 1 to 4 range where the other `board.*` values are validated.
- [ ] Add `max_subtask_depth` to `Graph`, set from the Board config in `Graph::load`, defaulting to 1 in `Graph::from_tasks` and `from_tasks_with_holds` so every existing unit test is unaffected.
- [ ] Run `cargo test`, then the whole suite.
- [ ] Commit: `feat(board): add max_subtask_depth setting`.

### Task 3: Depth and ID grammar

**Files:** `src/task.rs`, `src/graph.rs`

- [ ] Write failing tests. `parent_id_from_id`: `orb-a0b1c2.d4b3df` gives `orb-a0b1c2` (unchanged); `orb-a0b1c2.d4b3df.e5f6g7` gives `orb-a0b1c2.d4b3df`; any segment with a bad length or alphabet gives `None`; a legacy ID such as `legacy-a7b2c9.d4b3df` still works. `is_valid_id` accepts the same three-segment ID and rejects an empty segment.
- [ ] Generalize both functions: split the part after the prefix on `.`, validate every segment, and take the parent as everything but the last segment. The one-dot case stays a special case of the general one.
- [ ] Add `Graph::depth(id)` (parent hops, with a guard against a cycle), `Graph::ancestors(id)` (nearest first) and `Graph::descendants(id)` (depth first).
- [ ] Add a test that a Board with a Task file named `orb-a0b1c2.d4b3df.e5f6g7.md` loads, lists and shows, and that `slug migrate` maps all three segments and rewrites the `parent` fields (`tests/slug_migrate.rs`).
- [ ] Commit: `feat(task): hierarchical IDs with more than one SubTask segment`.

### Task 4: Enforce depth, not one level

**Files:** `src/graph.rs`, `src/ops.rs`, `src/health.rs`, `tests/subtasks.rs`, `tests/doctor.rs`

- [ ] Write failing tests with the default Board: adding a SubTask under a SubTask still fails with `INVALID_ARGUMENT`, a message containing "one level" and `board.max_subtask_depth`; `validate` still reports `NESTED_SUBTASK` for a depth-2 file. With `max_subtask_depth = 2`: that add succeeds, a depth-3 add fails, `validate` is clean for depth 2 and reports `NESTED_SUBTASK` for depth 3, and `doctor` reports no `PARENT_MISMATCH` for a three-segment ID with a matching `parent`.
- [ ] Replace the check in `ops.rs` and `graph.rs` (`parent.front.parent.is_some()`) with `depth(parent) + 1 > max_subtask_depth`. Keep the `NESTED_SUBTASK` code, the parent-is-terminal check, and the rule that `epic` is refused on any SubTask.
- [ ] Commit: `feat(graph): enforce Max SubTask Depth`.

### Task 5: `done --force`, `cancel --force` and `reopen` over a chain

**Files:** `src/ops.rs`, `src/mutation.rs`, `src/commands/batch.rs`, `tests/subtasks.rs`

- [ ] Write failing tests on a three-level chain (A, A.b, A.b.c) with `max_subtask_depth = 2`:
  - `done A` is refused while `A.b` is open; `done A.b` is refused while `A.b.c` is open; finishing `A.b.c`, then `A.b`, makes `A` Ready and a close candidate.
  - `cancel --force A` and `done --force A` cancel `A.b` and `A.b.c`, and `cancelled_subtasks` lists both. `done --force` still cancels nothing when a dependency is unfinished (the existing rule, `done_force_does_not_cancel_subtasks_when_a_dependency_is_unfinished`).
  - `reopen A.b.c` reopens a Done `A.b` and a Done `A`; with a Cancelled ancestor it fails with `INVALID_TRANSITION` naming that ancestor.
  - `delete A` without `--force` is refused and with `--force` deletes all three and reports `deleted_ids` (no code change expected: `delete_subtree` already recurses).
- [ ] `finish`: collect every open descendant (`Graph::descendants`) instead of `open_children`, cancel each with the same call, and return them all in `cancelled_subtasks`. The refusal without `force` still reports the direct open children in `details.subtasks`, as today.
- [ ] `reopen`: walk `Graph::ancestors`. A Done ancestor is reopened (with the same done-dependents check); a Cancelled ancestor stops with the existing error; an active ancestor ends the walk. Return `Vec<String>` of reopened ancestors, nearest first. `mutation::reopen` writes each one and emits one `reopened parent <id>` warning per ancestor; `commands/batch.rs` ignores the value as it does today.
- [ ] Commit: `feat(ops): cancel, done --force and reopen across nested SubTasks`.

### Task 6: Queries and output

**Files:** `src/commands/query.rs`, `src/commands/render.rs`, `src/graph.rs`

- [ ] Write failing tests with depth 2: `orbit tree A` shows every level, indented, and its `--json` `data.tasks` stays root first and lists descendants depth first, with `count` matching; `orbit tree A.b` works (a SubTask with SubTasks is a valid root); on a default Board `tree` of a SubTask keeps its current error. `list --parent A.b` and `ready --parent A.b` work and still return direct children only. `list` with nested Tasks does not panic and groups each row under its direct parent. `orbit ready` on the chain offers a Task only when its SubTasks are resolved, as for any Parent Task today.
- [ ] `cmd_tree`: replace the "must be top-level" check with "must have SubTasks or be a Task" (keep the error for a Task that has none and is a SubTask, so a default Board's message does not change), recurse with `Graph::children`, indent two spaces per level in the human output.
- [ ] `list` and `ready` `--parent`: keep the "expects a Parent Task" rejection of a SubTask while `max_subtask_depth` is 1, and drop it only when the Board allows nesting; keep the direct-children filter (`query.rs` filters on `task.front.parent == Some(parent)`). The test `list_parent_filters_and_group_layout` keeps asserting the rejection on a default Board.
- [ ] Add `depth` as an additive field in `show` and `tree` JSON (`graph::task_summary` or `task_detail`).
- [ ] Commit: `feat(tree): show every level of nested SubTasks`.

### Task 7: Batch

**Files:** `src/ops.rs`, `src/commands/batch.rs`, `tests/batch.rs`

- [ ] Write failing tests: a Batch that adds a SubTask under another SubTask added earlier in the same Batch (a `key:` parent) succeeds with `max_subtask_depth = 2` and a depth-3 add rolls the whole Batch back with no files written; on a default Board the first one rolls back.
- [ ] The depth check reads the in-memory graph that `batch` mutates, so it should work without change. If it reads the stored Board anywhere, fix that.
- [ ] Commit: `test(batch): nested SubTasks in one Batch`.

### Task 8: Remote sync, compatibility and text

**Files:** `tests/`, `src/help.rs`, `src/cli.rs`, `src/setup.rs`, `docs/`, `CHANGELOG.md`

- [ ] `tests/remote.rs`: sync a Board with `max_subtask_depth = 2` and a depth-2 Task against the test provider; the `parent` of the nested Task survives a round trip. If the provider rejects it, record the limit in the ADR and in `docs/github-sync.md` instead of changing the sync code in this plan.
- [ ] Compatibility test: load a Board fixture written by the previous release (one level, no `max_subtask_depth`) and run `validate`, `doctor`, `list`, `tree`, `ready` and `export`; the output equals the previous release's. `export` reports `schema_version` 3, and a Board at depth 2 exports the nested Task with its normal `parent`.
- [ ] Shared Board test: two agents on one Board at depth 2, one holding a hold-labelled Task; `orbit ready` never offers a hold-labelled Task or a Task with open SubTasks.
- [ ] Update every "one level deep" string (`help.rs`, `cli.rs`, `setup.rs`, the generated guides) to "one level deep by default; raise `board.max_subtask_depth` to nest further", regenerate the managed blocks, and update `CHANGELOG.md` under Unreleased.
- [ ] Run the full suite, `cargo clippy` and `cargo fmt --check`.
- [ ] Commit: `docs: nested SubTasks, remote sync check and compatibility tests`.

## Kinetic side (separate plan, after the Orbit release)

Not part of this plan; recorded so the Orbit work is shaped for it.

- Kinetic's Orbit tracker publishes the spec as a top-level `epic` Task, each slice as its SubTask, and each ticket as the slice's SubTask. Slices stop being `epic` and become ordinary Tasks.
- New adapter operations `create spec` and `fetch spec`; the spec's Task ID goes into `state.spec`; slices and tickets carry a spec label and the `kinetic` Annotation.
- `kinetic doctor` checks that the Board sets `max_subtask_depth` to at least 2, and `orbit_store.py` raises its minimum Orbit version.
- `kinetic trace` reads the chain from the Board instead of from state when the tracker is Orbit.
- A slice whose tickets are all done becomes Ready as any Parent Task does today; kinetic already closes the slice in the Batch that finishes its last ticket, so no agent can claim it in between.

## Risks

- **Cascades.** `done --force` and `cancel --force` now cancel several levels. The tests in Task 5 and a message that lists every cancelled Task are the guard.
- **ID length.** Three segments make IDs about 20 characters longer than a top-level ID; file names carry two dots before `.md`. Documented in ADR 0021; nothing breaks.
- **Remote sync.** Only checked by search, not by running it. Task 8 has the test and the fallback.
- **ADR conflict.** This reverses a stated Orbit position (ADR 0007 and part of 0009). If the maintainers prefer to keep that position, the alternative is a typed link, rejected here because it would duplicate roll-up and readiness.
