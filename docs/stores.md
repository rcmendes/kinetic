# Writing a ticket store

A ticket store is where a run's tickets live. Kinetic ships two: the built-in one (tickets in the run's `state.json`) and the Orbit store. A new tracker (Linear, Jira, GitHub Issues) is a third. Kinetic's core talks only to the interface in [`scripts/kinetic/ticket_store.py`](../scripts/kinetic/ticket_store.py) and never names a store, so adding one touches no core code.

## What a store is

A module `kinetic.<name>_store` that exposes `make(config) -> TicketStore`. `ticket_store.for_tracker` picks it when the project's `tracker` setting is `<name>` and the module exists. The tracker name must also be one of `tracker.TRACKERS` and have its adapter file `trackers/<name>.md` (the status names and edge names the store maps to). Until the module exists, that file is the prose adapter the agent follows by hand.

## The core: every store implements these

| Method | Meaning |
|---|---|
| `read(run_dir, st)` | Every ticket of the run, staged ones included, as a dict keyed by ticket id. |
| `write(run_dir, st, before, after)` | Persist the change from `before` to `after`, fully or not at all. |
| `stage_path(run_dir)` | Where staged tickets live before publishing, or `None`. |
| `publish(run_dir, st)` | Publish staged tickets; returns provisional id to final id. |
| `publish_spec(run_dir, st, title, body)` | Publish the run's spec; returns `{"ref", "published"}`. |
| `sync_spec(run_dir, st, event)` | Mirror a spec lifecycle event: `gate-approved`, `gate-rejected`, `building`. |
| `touch(run_dir, st, ticket_id)` | Signal that a running ticket's implementer is alive. |
| `health(settings)` | Doctor rows this store needs. |

A ticket is a dict with at least `repo`, `slice`, `status`, `blocked_by` (a list of `{id, type}`), `kind` (`agent` or `human`) and the other fields in `state.ticket_defaults()`. Statuses are kinetic's: `todo`, `running`, `in_review`, `needs_decision`, `blocked`, `done`, `done_with_concerns`, `merged`, `released`.

## Capabilities: declare only what you do

`capabilities()` returns the names a store supports. Kinetic calls a capability's methods only after `ticket_store.has(store, name)`. A store with none still works: kinetic does what it did before the capability existed.

| Capability | A store that declares it | Without it |
|---|---|---|
| `review-states` | `record_verify`, `submit_review`, `record_review`: reviews and verify evidence live in the store | the status changes only |
| `checkpoint` | `checkpoint`, `last_checkpoint`: a ticket's handoff survives a crash | resume from `state.json` |
| `schedule-hints` | `hints(run_dir, st, ready_ids)`: a preferred order for ready tickets; a hint only reorders | kinetic's own order |
| `query` | `rollup(run_dir, st)` counts and `changes(run_dir, st, cursor)` ids that may have changed (never too few) | counts from reading every ticket |
| `idempotent-write` | a repeated `write` is safe and may carry a version guard | none needed |
| `placement` | a ticket's `repo` and its slice are held as native, filterable fields | `repo` stays a ticket field |
| `priority` | the rank 0 to 3 from `graph.priorities` is held natively | the status column computes it |
| `human-question` | `ask`, `answer`, `open_questions` | `needs_decision` is a plain status change |
| `external-ref` | a ticket's `refs` (`{system, id, url?}`) are held natively | refs stay on the ticket |

The words are kinetic's. The store maps them to whatever the tracker calls them. A capability the tracker cannot honour should be left undeclared rather than faked.

## Passing the contract

`tests/test_store_contract.py` runs one core section and one section per capability against every store. A section for a capability the store does not declare is skipped. To test a new store, subclass `StoreContract` with `unittest.TestCase`, give it a `store_record()` (the run's `ticket_store` record) and a `prepare()` (any setup the tracker needs), and run it. The Orbit class in that file is the model; the `FakeStore` there is the smallest store that passes the core section.

Optional hooks let a section check the tracker's own view: `native_placement`, `native_priority`, `native_refs`. Return `None` to skip that part.

## Example: Linear

This is a sketch of the mapping, not a store that exists.

| Kinetic | Linear |
|---|---|
| spec | a project, or a parent issue |
| slice | a parent issue under it |
| ticket | a sub-issue |
| `status` | workflow states, mapped through `trackers/linear.md` |
| `blocked_by` | an issue relation of type blocks; the edge type goes in a label or in the description, since Linear has no relation attributes |
| `human` ticket | an issue assigned to a person, with a label |
| `repo` (`placement`) | a label or custom field; declare `placement` only if it is filterable |
| rank (`priority`) | Linear's four priorities map directly: urgent, high, medium, low |
| `refs` (`external-ref`) | attachments or links |
| `human-question` | a comment plus a state change; declare it only if the answer can be read back reliably |

A first Linear store would implement the core methods and declare `placement`, `priority` and `external-ref`, then add `human-question` and `query` when they are cheap to do correctly. `review-states`, `checkpoint` and `schedule-hints` can stay undeclared: kinetic keeps those in its own state.

## The Orbit store, for comparison

`scripts/kinetic/orbit_store.py` declares every capability above. It publishes a run in one atomic batch with idempotency keys, guards each write with a version check, and reads the Board's change feed instead of rereading it. Its profile, `profiles/kinetic.toml`, carries Orbit-only rules (reviewer is not the author, an attempt limit, and a limit of three tickets running at once that matches kinetic's default parallel cap). Those rules guard other tools on the same Board; kinetic's own parallel cap in `frontier.py` stays the rule for every store.
