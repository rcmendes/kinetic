# Orbit kinetic profile

`profiles/kinetic.toml` is the user-owned Orbit profile for kinetic. It requires
Orbit contract 1.0.0 and the native workflow, policy, hierarchy, links,
properties, assignee, lease, batch, idempotency and profiles capabilities.
Index and hooks are optional Orbit modules.

When `orbit` is on PATH, `kinetic install --harness <claude|codex|opencode|devin>`
registers the profile at `~/.config/orbit/profiles/kinetic.toml`. An install
without Orbit creates no profile; install Orbit and rerun the installer before
applying the named profile. It refuses to overwrite a foreign
or edited profile. Uninstall removes the registered file only if it still
matches the installed content; the Board's applied workflow remains in place.

On a new Board, apply it from the Board's anchor:

```sh
orbit setup kinetic
orbit setup kinetic --check
orbit capabilities --json
orbit doctor
```

Without installing a harness, apply the source file by its absolute path:
`orbit setup /path/to/kinetic/profiles/kinetic.toml`. Applying a profile changes
the Board's workflow; existing runs need migration before using the new model.
It does not migrate old issue statuses automatically.

The Ready status is `todo`; kinetic dispatch claims work into `running`.
`needs_decision`, `blocked` and `in_review` have the active category. `done`,
`done_with_concerns`, `merged` and `released` have the done category;
`cancelled` has the canceled category. Each native status is visible directly
in `orbit show`, without a status label or a hold label.

Review uses `submit`, `approve` and `request_changes`. Approval requires evidence
and a reviewer distinct from the submitter. Requesting changes also requires
a distinct reviewer. `ask`/`escalate` enter `needs_decision`, `block` enters
`blocked`, and `resume` returns either hold to `running`. `merge` moves a done
issue to `merged`, and `release_artifact` moves it to `released`. The lifecycle
`release` command returns a running issue to `todo`.

The automated smoke test applies the profile twice to a temporary Board,
checks every native status and category, submits verification evidence, rejects
self-approval, accepts an independent review, and runs Orbit doctor:

```sh
PYTHONPATH=scripts python3 -m unittest tests.test_orbit_profile -v
```

## Review through kinetic

A successful implementer report is submitted while its Issue is still running:

```sh
kinetic state submit-review --run <run_dir> --id <ticket-id>
kinetic state record-review --run <run_dir> --id <ticket-id> --axis spec --verdict approve
kinetic state record-review --run <run_dir> --id <ticket-id> --axis standards --verdict approve
```

Both ticket review axes must approve before the Issue becomes `done`. Until
then `in_review` occupies the repo and counts toward the parallel cap. Approval
also closes a slice whose last ticket passed review. A report with concerns can
then use `set-ticket --field status=done_with_concerns` before merging.

A blocking finding uses `--verdict request-changes --reason "<finding>"`.
Orbit restores the submitting implementer's identity, increments the profile's
native `kinetic-reviewer-changes` attempt counter, and leaves the Issue running.
After each fix, submit again with `--scope fix` and record `--axis fix`. Submit
rounds are visible under `properties.orbit.review.rounds`; the profile permits
the initial rejection plus three fix rejections. After the
third fix rejection, set `status=needs_decision` explicitly for adjudication.
Accepting all remaining concerns requires a new fix submission and an approval
with the ruling as its reason before setting `done_with_concerns` and merging.

`kinetic verify` records the executed command, exit code, timestamp and log in
run state. Published tickets also retain the latest result in the
`kinetic-verify` Property, including failures. The run state is saved before
the store write, which runs without the state lock. If the store is unavailable,
the command reports that the local record survived; retry `record-verify` with
the same evidence to finish the store write without adding a duplicate.
Orbit reserves its `evidence`
Property: successful submission appends native evidence there. Failed or stale
verify cannot be submitted, and each new submit clears prior review axes.

For the final integration review, run full verify without `--ticket` for each
repo's integration worktree. Then submit the run's owned spec Issue with
`--scope integration` and record `--axis integration`. Ticket verifies do not
substitute for integration verifies. The spec closes only after the independent
integration reviewer approves, and an input user Issue is never the target.

Run `PYTHONPATH=scripts python3 -m unittest tests.test_orbit_review -v` to
exercise native evidence, both ticket axes, fix attempts and integration review
identities against a temporary Orbit Board.

Each accepted axis records its role identity alongside its verdict in the
`kinetic-reviews` Property. Only the aggregate verdict completes the native
review transition, so one axis cannot approve on behalf of the other.

Interrupted writes are journaled with their original request and idempotency
key. The next store read or workflow action replays the request before reading
status. A crash after Orbit commits a submit does not create a second evidence
item or review round. A definitive policy rejection clears the journal so a
corrected request can proceed; uncertain transport failures retain it.

Verify accepts a provisional ticket ID after publication and records the result
under its native Issue ID. Reworking a completed descendant can reopen its spec
to `todo`; integration submission restarts that owned container with a workflow
transition before submitting the new evidence, without acquiring a worker lease.
