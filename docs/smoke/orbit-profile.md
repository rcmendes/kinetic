# Orbit kinetic profile

`profiles/kinetic.toml` is the user-owned Orbit profile for kinetic. It requires
Orbit contract 1.0.0 and the native workflow, policy, hierarchy, links,
properties, assignee, lease, batch, idempotency and profiles capabilities.
Index and hooks are optional Orbit modules.

`kinetic install --harness <claude|codex|opencode|devin>` registers the profile
at `~/.config/orbit/profiles/kinetic.toml`. It refuses to overwrite a foreign
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
