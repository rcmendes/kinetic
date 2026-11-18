# Tracker adapter: Orbit

Access: the `orbit` CLI, always with `--json` and `--dir <anchor>`. Optional setting: `orbit_dir` (`tracker_settings.orbit_dir`), the Board anchor or Board folder. Without it the anchor is the project folder (the one holding `.kinetic/`, `bare-repos/` and `runs/`). Use `orbit capabilities` to negotiate the contract and enabled modules before accessing Issues. Install the native workflow with `orbit setup kinetic`; doctor checks the workflow and the configured `board.hierarchy`.

The run's published tickets live on the Orbit Board. During decompose, follow the Local steps (ticket files and `NN-<repo>-<slug>` IDs); approval of the breakdown gate publishes staged tickets in one `orbit batch`. Published ticket references are Orbit Issue IDs. Never edit Issue files by hand or run `orbit ready --claim` for a kinetic ticket: kinetic schedules its own tickets.

A run started from an existing user Issue never claims, moves or closes that Issue. It creates its own top-level spec epic and a non-blocking `relates-to` link to the user's Issue. The spec, slice and ticket use the native hierarchy: `epic` > `task` > `sub-task`. Parentage is separate from blocking links.

After publication, later tickets (human work or review fixes) are staged durably and published before the state write finishes. Idempotent batches recover interrupted publication; active statuses are applied after Orbit resolves the new Issue IDs. Provisional IDs remain valid with `kinetic state` commands. Tool-specific metadata belongs in the `kinetic` Property. Native status, assignee, lease, parent and links remain authoritative for those fields.

## Operations

### create spec

`kinetic state publish-spec` publishes `spec.md` as a top-level Issue of type `epic`, using Source Key `kinetic:<run id>:spec`. Its body identifies the file as generated: edit `spec.md` and publish again. A Source Key makes retries find the same Issue. The spec gate and run lifecycle use the native workflow and the `kinetic` Property for phase metadata.

### create slice parent

Publishing creates an Issue of type `task` with the spec epic as its parent. Direct CLI equivalent: `orbit add <title> --type task --parent <spec id> --source-key <key> --body-file -`. The configured hierarchy must allow this relationship.

### create child

`kinetic state set-ticket` stages a ticket; publishing creates an Issue of type `sub-task` under its slice. Direct CLI equivalent: `orbit add <title> --type sub-task --parent <slice id> --source-key <key> --body-file -`.

### link blocker

Record `blocked_by` with its edge type using `set-ticket`. Store a blocking `blocks` link to the prerequisite, with the edge type (`snapshot`, `merge` or `release`) in its `edge` attribute. CLI equivalent: `orbit link add <ticket> --type blocks --to <prerequisite> --attribute edge=snapshot`.

### link after

Record `after` with `set-ticket`. Store an `after` link to the other Issue with `--no-blocking`; ordering does not create a blocking dependency.

### link ships-with

Record `ships_with` with `set-ticket`. Store non-blocking `ships-with` links between the grouped Issues, with the group in a link attribute.

### link related

Record `related` with `set-ticket`. Store non-blocking `relates-to` links. The run's spec links to an existing user Issue this way.

### set kind

Record `kind` with `set-ticket` in the `kinetic` Property. Kind does not change the Issue's workflow status.

### move status

Record `status` with `set-ticket` (or `human-done`). The ticket store runs the appropriate native workflow transition in the state batch. Status names are `todo`, `running`, `needs_decision`, `done_with_concerns`, `blocked`, `done`, `merged` and `released`; they are not labels or aliases for `in_progress`. Inspect `orbit show` for `allowed_transitions`; the direct command is `orbit transition <id> <transition> --by <identity>`. Review uses `submit` and `review`, with evidence, rather than setting a terminal status directly.

### assign to user

Use `orbit assign <id> --to <person>` for an assignee without a lease. Implementers and reviewers use distinct identities for work and reviews; `orbit heartbeat <id> --by <identity>` renews the active worker's lease. A human completes work through the Board's workflow.

### fetch

`orbit show <id> --json --dir <anchor>` returns the Issue body, status, assignee, lease, properties, links, allowed transitions and comments. `orbit changes --since <cursor>` refreshes cached state after the initial snapshot.

### comment

`orbit comment add <id> --by <identity> --body-file - --dir <anchor>`, with the comment on stdin. Writes use optimistic versions and idempotency keys in `orbit batch` so retries cannot silently overwrite another writer or duplicate work.

### ticket reference

The Orbit Issue ID or project key returned by Orbit, for example `orb-a7b2c9` or `ORB-12`. IDs do not encode hierarchy; use `parent` and links to navigate.

## Default names

These names describe native links and statuses. Run/spec labels identify kinetic content; they do not control readiness.

```json
{
  "kind": {"agent": "", "human": "ready-for-human"},
  "edge": {"snapshot": "snapshot", "merge": "merge", "release": "release"},
  "after": "after",
  "ships_with": "ships-with",
  "related": "relates-to",
  "status": {"todo": "todo", "running": "running", "needs_decision": "needs_decision", "done_with_concerns": "done_with_concerns", "blocked": "blocked", "done": "done", "merged": "merged", "released": "released"},
  "run": "kinetic-run-<run id>",
  "spec": "kinetic-spec"
}
```

## Expected paths and scheduling

Record `touches` as a list of repo-relative paths with `set-ticket`. The store writes the native `touches` Property as `{"paths": [...]}` when publishing and updating a ticket; it stays outside the `kinetic` Property. `[]` clears the declaration. Ready hints use `critical-path,touches`, placing candidates whose paths overlap assigned or in-review work behind safe candidates. Hints only reorder kinetic's ready tickets; the pool cap and per-repo dispatch exclusion remain in force. Tickets without declared paths keep their existing behavior.
