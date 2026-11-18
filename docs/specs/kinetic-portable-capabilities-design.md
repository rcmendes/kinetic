# Kinetic: portable ticket-store capabilities, round 2

Status: draft for approval. Base: kinetic `main` (`4658d9c`), Orbit 0.11.0. Builds on [kinetic-ticket-store-capabilities-design.md](kinetic-ticket-store-capabilities-design.md), which introduced `capabilities()`.

## Why

Kinetic now uses three Orbit features through capabilities (`review-states`, `checkpoint`, `schedule-hints`). Orbit offers more that is worth using, but each addition must keep two things true: the built-in (local) tracker behaves as it does today, and a future store (Linear, Jira, GitHub Issues) can implement the same interface without knowing Orbit.

## Rules for every addition

1. **Neutral vocabulary.** A capability and its data use kinetic words (`repo`, `priority`, `refs`, `question`), never Orbit words. The Orbit store maps them to Orbit fields; another store maps them to its own.
2. **Optional and declared.** A store lists what it supports in `capabilities()`. Core calls it only through `ticket_store.has(store, cap)`.
3. **One path in core.** Where it is cheap, the built-in local store implements the capability itself (in `state.json`), so core has one code path instead of "capability / fallback" branches. Where it is not cheap, core keeps today's behavior when the capability is absent.
4. **Contract tests.** One suite, `tests/store_contract.py`, runs against every store. Each capability's section runs only for stores that declare it. A new store passes the suite for what it declares; nothing else is required of it.
5. **Orbit words stay in `orbit_store.py`, `trackers/orbit.md` and `profiles/kinetic.toml`.**

## What is adopted

| Capability | Neutral contract | Orbit mapping | Built-in local store |
|---|---|---|---|
| `query` (declare; exists) | `changes(...)` cursor reads, `rollup(run_dir, st, spec_id) -> {done, blocked, open, total}` | change feed, `digest` rollup | computes both from `state.json` |
| `idempotent-write` (declare; exists) | a write may carry a key and a version guard; a repeat returns the first result | `--idempotency-key`, `if_version` | not needed; declares nothing |
| `placement` | tickets keep `repo` and `slice`; the store may expose them as native filterable fields | `repository`, `component` | none; `repo` stays a ticket field |
| `priority` | core computes `priority` (0 highest to 3) from the critical path as a pure function; a store may persist it | named priorities (`highest` to `low`) | stored on the ticket; shown in `kinetic status` |
| `human-question` | `ask(run_dir, st, ticket_id, question)`, `answer(...)`, `open_questions(...)` | `ask`, `answer`, status `needs_decision` | stored in `state.json`, same state machine |
| `external-ref` | tickets and the spec carry `refs: [{system, id, url}]` | `external_id`, external links | stored on the ticket |

Policy (reviewer-not-author, attempt limit, a WIP limit equal to kinetic's parallel cap) stays in `profiles/kinetic.toml`. It is Orbit-only and kinetic's own cap in `frontier.py` remains the rule for every store.

## Not adopted now

- **Event hooks.** They run shell commands on writes and need `ORBIT_TRUST_HOOKS=1`. Opt-in later, documented.
- **`orbit prime` at session start.** Possible later through the harness profile, not a store capability.
- **Board host and sync, `orbit remote`.** Team and GitHub features; their own project.
- **`claim --worktree`.** Kinetic owns worktrees (locking, grants, cleanup).

## Adding a store

A tracker is a module `kinetic.<name>_store` with `make(config)`. It implements the seven core methods, declares a subset of `CAPABILITIES`, and passes `tests/store_contract.py` for what it declares. `ticket_store.for_tracker` already picks the module when it exists. A short guide (`docs/stores.md`) walks through a Linear example: which fields map to which capability, and what to leave undeclared. Until a `linear_store` exists, `trackers/linear.md` stays the prose adapter.

## Non-goals

No Linear, Jira or GitHub store in this round. No change to the local tracker's behavior for anything it does today.

## Delivery order

1. Capability vocabulary and the contract suite (local and Orbit pass it).
2. `query` and `idempotent-write` declared with contract sections.
3. In any order: `placement`, `priority`, `human-question`, `rollup` in `kinetic status`, `external-ref`.
4. Store-author guide and the profile WIP limit.

## Risks

- A capability that changes a shared type (a new status or required ticket field) would reach local. Mitigation: ticket fields added here are optional, and the existing suite is the gate.
- Local implementing capabilities adds code to the built-in tracker. Accepted where it removes a fallback branch from core, rejected where it does not.
