# Kinetic: ticket-store capabilities and Orbit 0.10.17

Status: implemented (see Delivered). Base: kinetic `main` (`e05f495`), Orbit `origin/main` (0.10.17, Issue model, contract `1.0.0`, ADR 0022). Builds on [kinetic-orbit-tracker-design.md](kinetic-orbit-tracker-design.md).

## Why

Kinetic already keeps tickets behind one interface (`TicketStore` in `scripts/kinetic/ticket_store.py`) with two implementations: local (in `state.json`) and Orbit. The interface is a fixed list of methods, so a store can either do everything or nothing. Orbit 0.10.17 can do more than the interface lets kinetic ask for (checkpoints, atomic batches with idempotency, review and ask/answer states, scheduling hints, search, an event feed), and it also changed its data model, which breaks the current Orbit store.

The goal: kinetic delegates to a store whatever the store does well, finds out what that is through a capability handshake, and falls back to its own behavior when a store lacks something. Orbit becomes the strong implementation. The local store stays the reference implementation and the fallback.

## Decisions

1. **Orbit target is `origin/main` (0.10.17) only.** No support for 0.10.16 or older, no dialect switch. `MIN_ORBIT` becomes the first release that carries this model; until that release exists, doctor accepts a binary whose `orbit capabilities` reports contract `1.x`.
2. **GitHub, Jira and Linear are out of scope.** Their `trackers/*.md` prose adapters stay exactly as they are. The interface is designed so they can become stores later.
3. **Kinetic keeps scheduling.** The frontier, edge types (`snapshot`, `merge`, `release`), the parallel cap, fix rounds and the merge pipeline stay in kinetic. A store may only offer hints.

## Goals

1. A store declares what it can do; kinetic asks the store, never names it.
2. With Orbit, use the 0.10.17 features that make a run more robust, resumable and visible.
3. A store without a capability behaves exactly as today. Local and `other` runs are unchanged step for step.
4. Only the Orbit store and `trackers/orbit.md` name Orbit commands or concepts.

## Non-goals

- Backward compatibility with older Orbit.
- GitHub, Jira, Linear stores.
- Orbit choosing which kinetic ticket runs (`orbit ready --claim` is still never used for kinetic tickets). Hints only.
- Moving run-level state (phase, repos, verify records, worktrees, owner session) out of `state.json`.

## Interface

`TicketStore` keeps its seven core methods. It gains one:

```python
def capabilities(self) -> frozenset[str]:
    """Names from CAPABILITIES this store provides."""
```

Each capability is a named group of optional methods. Kinetic calls the group only after `cap in store.capabilities()`. The local store returns `frozenset()`, so nothing changes for it. A helper `ticket_store.has(store, cap)` keeps call sites short.

| Capability | Methods (all optional unless the capability is declared) | Kinetic uses it to | Without it |
|---|---|---|---|
| `checkpoint` | `checkpoint(run_dir, st, ticket_id, progress, next=None, blocker=None)`, `last_checkpoint(...)` | Record each implementer handoff and resume a ticket after a crash or takeover from its last checkpoint | Resume from `state.json` and the report file only |
| `review-states` | `submit(...)`, `review(...)`, `ask(...)`, `answer(...)` | Show a ticket in review, or waiting on a human question (`NEEDS_HUMAN`), as first-class store states | Labels and comments, as today |
| `schedule-hints` | `hints(run_dir, st, ready_ids) -> list[str]` | Order ready tickets by critical path; refuse tickets whose declared file paths overlap a running one | Kinetic's own frontier order |
| `query` | `digest(...)`, `changes(run_dir, st, cursor)` | Cheap `kinetic status` and change-driven refresh instead of a full read per step | Full `read` |
| `idempotent-write` | writes carry an idempotency key and `if_version` | Safe retry of any write; detect a concurrent edit instead of overwriting | Source Key matching (already present) |
| `handshake` | `negotiate(settings) -> dict` | Doctor reports binary, contract, workflow and enabled modules from one call | Version check by hand |

`checkpoint`, `review-states`, `schedule-hints` and `handshake` are the first delivery. `query` and `idempotent-write` follow. The event hooks, the read index and `claim --worktree` are deliberately not mapped: hooks run arbitrary commands and need `ORBIT_TRUST_HOOKS=1`; the index is an Orbit-internal speedup that needs no kinetic code; kinetic already owns its worktrees.

## Orbit store on the 0.10.17 model

The model changed; the Orbit store translates once, in one place, so nothing above it notices.

| Kinetic concept | 0.10.16 shape (today) | 0.10.17 shape |
|---|---|---|
| Spec | top-level Task, type `epic` | Issue, type `epic` |
| Slice | Task under the spec | Issue, type `task`, `parent` = spec |
| Ticket | SubTask | Issue, type `sub-task`, `parent` = slice (three levels, the default hierarchy; `board.max_subtask_depth` is gone) |
| Kinetic data | Annotation `kinetic` | Property `kinetic` (batch field `properties`, `orbit property set`) |
| `blocked_by` | dependency (`depends_on` batch field) | `link` op, type `blocks`; edge type stays in the `kinetic` Property |
| `waiting_on` | field | Link type `waiting-on` with `--external`, or workflow status `needs_human` for a human question |
| Status | `todo` / `in_progress` / `done` plus labels | Workflow statuses `ready`, `todo`, `in_progress`, `in_review`, `needs_human`, `done`, `cancelled` (the `standard` template); kinetic maps `todo→ready`, `running→in_progress`, `done/merged→done`, human ticket `→needs_human` |
| Priority | 0–3 | `highest`/`high`/`medium`/`low` |
| Ids | `orb-a7b2c9` | internal id plus project key (`ORB-12`); kinetic stores the internal id |
| Hold | label `kinetic` in `non_ready_labels` | unchanged (`non_ready_labels` still exists in `config.toml`) |
| Export | `schema_version` 3, `tasks` | new schema; the doctor row `orbit-schema` pins the new value after it is read from a real export |
| Batch file | JSON list | `{"operations": [...]}` object |

Doctor rows change: `orbit-binary`, `orbit-board`, `orbit-contract` (replaces `orbit-schema`, from `orbit capabilities`, must be `compatible`), `orbit-hold-label`. `orbit-depth` is removed; a new row checks that the Board hierarchy has three levels. A Board without `contract` fails with `MODEL_NOT_MIGRATED`; the fix text is `orbit migrate model --apply`.

Kinetic's own Board (`.orbit/board`) has no `contract` yet, so it needs `orbit migrate model --apply` before the new store can be exercised against it. That is a one-time step, run by the user.

## Delivery order

1. **Interface.** Add `capabilities()` and `has()`; local returns empty. No behavior change; tests prove the local path is untouched.
2. **Orbit model port.** Rewrite the Orbit store's reads, batch writes, doctor rows and `trackers/orbit.md` for 0.10.17; add `handshake`. Existing Orbit store tests move to the new shapes.
3. **`checkpoint`**, then **`review-states`**, then **`schedule-hints`**, one at a time, each with an Orbit-backed test and a local-fallback test.
4. **`query`** and **`idempotent-write`**.

## Reconciliation with the existing Orbit epic

The Orbit Board already holds an open epic, `orbit-zd1krr` "Refactor kinetic onto the new Orbit platform", with nine open tasks (all `todo`, no body beyond goal and acceptance criteria). It predates this spec. They overlap, so this spec absorbs them: each task becomes an acceptance target of one delivery step below, and the epic is the tracking container.

| Existing task | Lands in | Reconciliation |
|---|---|---|
| `xp8spy` Version handshake and capability negotiation | step 2, `handshake` | Same. Unsupported Orbit is rejected with the missing capabilities from `orbit capabilities`; optional modules map to this spec's optional capabilities. |
| `z9bn99` Rewrite tracker adapter on canonical vocabulary | step 2 | Same. Adopts its rule: drop the `names.hold` / `non_ready_labels` requirement. The hold label goes away because `needs_human` and `todo` (not ready) are native statuses. This supersedes the "Hold unchanged" row of the mapping table. |
| `acvzbb` Native kinetic workflow and profile | step 2 | The profile ships **in the kinetic repository** (`profiles/kinetic.toml`, applied with `orbit setup ./profiles/kinetic.toml`), because Orbit must stay free of kinetic vocabulary. Needs no Orbit change: Orbit already loads a profile from a path and accepts a custom workflow. |
| `cdgsrj` Typed links replace Annotation edges | step 2 | Same. `snapshot`/`merge`/`release` become link attributes; `after` and `ships-with` become non-blocking link types. |
| `pn9m4y` Hierarchy: slices as epics, tickets as issues | step 2 | **Conflict.** The task says slices are epics; Orbit allows `epic` only at the top level, and the spec is already the epic. This spec keeps spec=`epic`, slice=`task`, ticket=`sub-task`. The task's goal (a run on an existing issue nests beneath it) is dropped: kinetic still never claims or moves a user's Task, and links to it with a non-blocking link instead. Edit the task text accordingly. |
| `9ezx6z` Per-role identities, assignee and leases | step 3, `liveness` | Same. Distinct `--by` per implementer and reviewer; the existing heartbeat hooks keep the lease alive. |
| `bm5pmb` Review and evidence integration | step 3, `review-states` | Same. Spec, standards and integration review map to `submit` / `review`; the verify command result is stored as `submit` evidence (`--command`, `--exit-code`, `--commit`). |
| `gpqcr5` Change feed, optimistic writes and idempotency | step 4, `query` + `idempotent-write` | Same. |
| `tvv4w4` Migrate existing runs and release | final step | Same, plus the one-time `orbit migrate model --apply` of kinetic's own Board. |

New work in this spec with no existing task: `capabilities()` on the interface (step 1), `checkpoint` (step 3), `schedule-hints` (step 3). File them under the same epic.

## Orbit side: what must happen first

Checked against `origin/main` (0.10.17) by running the built binary: the Orbit API this spec needs already exists. Confirmed working: `orbit batch` with `add`, `link`, `property`, `claim`, `submit`, `review`; `capabilities`; `changes --since`; `submit` evidence flags; `--if-version` and `--idempotency-key`; `checkpoint`; custom workflow and profile loading from a path; `ready --strategy`. No new Orbit feature is required.

What blocks using it is release state, not code:

1. **Nothing is released.** The latest tag is `v0.10.15`, which has the old model. `origin/main` carries the new model under "Unreleased" with `Cargo.toml` at 0.10.17.
2. **`orbit-c8gab3` is open:** "Review remaining 14 subtasks of orbit-t2c704 against acceptance". It has no body. Finish it before releasing, since the release becomes kinetic's floor.
3. **Version number.** The change set is breaking (Issue vocabulary, typed links, workflow, `orbit github` removal, `MODEL_NOT_MIGRATED`). A patch bump (0.10.17) understates that; recommend `0.11.0`.
4. **Doc drift to fix in the same release:** `docs/commands.md` says `changes --limit` keeps the newest events; `CHANGELOG.md` says it keeps the oldest. Pick one and align.

Then `MIN_ORBIT` in kinetic is that release, and doctor checks the contract from `orbit capabilities`. The three open P3 performance tasks on the Orbit Board do not block it.

Updated delivery order: **0** Orbit release (items 2–4 above, then tag) → 1 interface → 2 Orbit model port, `handshake`, profile → 3 `checkpoint`, `review-states`, `liveness`, `schedule-hints` → 4 `query`, `idempotent-write` → 5 migration and docs.

## Status update (2026-11-18): what `integration/orbit-zd1krr` already delivers

Another session implemented eight of the nine original tasks on branch `integration/orbit-zd1krr` (worktree `.worktrees/integration-orbit-zd1krr`, tip `cccf753`, not yet on `main`). Checked by reading the diff and running the suite with Python 3.14: 787 tests, all passing. (System `python3` is 3.9 and lacks `tomllib`; the suite needs 3.11 or later.) This spec is reconciled to that work; what it already covers is not redone.

| Spec item | State on the integration branch |
|---|---|
| Orbit store on the 0.11 model (Issues, properties, links, hierarchy, workflow, batch) | Done. `orbit_store.py` rewritten; `trackers/orbit.md` rewritten; hold label dropped. |
| `handshake` | Done. `negotiate()` reads `orbit capabilities` and checks `REQUIRED_CAPABILITIES`; `MIN_ORBIT = "0.11.0"`; doctor row `orbit-capabilities`. |
| Kinetic profile | Done in kinetic: `profiles/kinetic.toml` (statuses `todo`, `running`, `needs_decision`, `blocked`, `in_review`, `done`, `done_with_concerns`, `merged`, `released`, `cancelled`, reviewer-not-author policy, `max_attempts = 3`). |
| `review-states` | Done, but as fixed Protocol methods: `record_verify`, `submit_review`, `record_review` (local store returns `None`). |
| `idempotent-write` and `query` | Done: change cursors and journaled guarded batch writes (ORBIT-130), replay of interrupted batches. |
| Per-role identities and leases | Done (ORBIT-128). |
| Hierarchy | Done: native spec epic linked to the user's Issue (ORBIT-127). |
| Run migration and release | In progress (`tvv4w4`, claimed by `kinetic-release`). |

What is **not** there, and stays in this spec:

1. **Capability interface.** The Protocol has no `capabilities()`. The branch grew the fixed method list instead (`record_verify` and the two review methods). Those three methods become the `review-states` capability group: same signatures, called only when `has(store, "review-states")`. Task `orbit-b9fx8t`.
2. **`checkpoint`.** No checkpoint code in `scripts` or `skills`. Task `orbit-gyq6ae`.
3. **`schedule-hints`.** No `--strategy` use. Task `orbit-kgshxj`.

Sequencing consequence: the three open tasks start only after `integration/orbit-zd1krr` merges to `main`, because they edit the same `ticket_store.py` and `orbit_store.py`. The interface task goes first; `checkpoint` and `schedule-hints` then run independently.

## Delivered (2026-11-18)

All of the epic `orbit-zd1krr` is on `main`: the native Orbit port and profile (kinetic#17), `capabilities()`/`has()` with `review-states` (#18), `checkpoint` (#19), `schedule-hints` (#20), and the follow-ups (#21): `touches` paths (ORBIT-139), the installer registering the Orbit profile only when Orbit is on PATH, and `record_verify` calling the store after the state lock is released.

Capabilities a store can declare today: `review-states`, `checkpoint`, `schedule-hints`. Not built, because kinetic has no call site for them yet: `query` (change-feed reads exist inside the Orbit store only) and `idempotent-write` (the Orbit store uses idempotency keys internally).

Findings from the whole-epic review: touched paths are stored scoped by repo (`<repo>/<path>`), because Orbit compares them across the whole Board and kinetic paths are repo-relative. Under the current rule of one running ticket per repo, `touches` cannot change an ordering within a repo, so the feature only starts to matter once kinetic allows several tickets per repo.

## Risks

- **Moving target.** Orbit `origin/main` is unreleased (0.10.17) and its hierarchy, workflow and Property shapes can still change. Mitigation: pin by contract version through the handshake, and keep every Orbit call inside the one store module.
- **Hierarchy depth.** Spec, slice and ticket use all three default levels, so a ticket cannot have its own sub-tasks. Kinetic never nests deeper today.
- **Hints must stay hints.** `schedule-hints` can only reorder or skip ready tickets, never schedule one kinetic considers blocked.

## Open questions

- Which Orbit release becomes `MIN_ORBIT`, and does it include the `checkpoint` and `batch` fields exactly as on `origin/main`?
- ~~Who writes the checkpoint?~~ Decided: the orchestrator, from the implementer's report, as the implementer's identity (Orbit lets only the claim holder write it). Implemented on `ticket/orbit-gyq6ae`.
