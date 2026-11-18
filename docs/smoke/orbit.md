# Native Orbit tracker smoke checklist

Use Orbit 0.11.0 or later with contract 1. Scripted checks run with
`PYTHONPATH=scripts python3 -m unittest tests.test_orbit_hierarchy tests.test_orbit_native_links tests.test_orbit_identity tests.test_orbit_feed tests.test_orbit_migration`.

## Fresh Board

1. Create a scratch kinetic project and register one repository and its verify command.
2. Run `orbit init`, then `orbit setup <kinetic-clone>/profiles/kinetic.toml` at the Board anchor. Alternatively, install kinetic for the harness and use `orbit setup kinetic`.
3. Set the project tracker to `orbit`, with `tracker_settings.orbit_dir` when needed. Doctor's `orbit-binary`, `orbit-board`, `orbit-contract`, `orbit-capabilities` and `orbit-profile` rows pass.
4. Publish a feature spec, approve its gate and publish two tickets under one slice. `orbit tree <spec>` shows an epic, task slice and sub-task tickets. The spec initially uses `needs_decision`; approval moves it to `todo`, and build starts it as `running`.
5. A run started from a user Issue links its owned spec to that Issue. The user Issue's status, assignee and timestamps remain unchanged.
6. Dispatch a ticket. Its native status is `running`; its assignee and lease holder are `kinetic-<run-id>-implementer-<ticket-id>`. Post-tool activity renews the exact owner at most every five minutes. Expiry appears in status as “implementer may be stuck.”
7. Verify and submit the ticket for review. Inspect evidence and attempt metadata. Spec and standards reviewers use separate identities; both approvals complete the ticket. A request for changes resumes the implementer and advances the attempt. Reviewers never take the author's lease.
8. Merge completed tickets. Slice completion leaves the spec open; integration review approves the spec. Human completion remains manual.
9. Status resumes the change feed. A repeated write after a lost response reuses its idempotency key; a concurrent version conflict refreshes and retries up to three times.

## Existing run

1. Preview `orbit migrate model`, then apply it at the existing Board anchor. It moves legacy Annotations into Properties.
2. Open the old run's status before kinetic migration; its recorded statuses and relationships remain visible.
3. Preview `kinetic orbit_store migrate --run <run_dir>` and check the operations. Preview changes no Board records or workflow.
4. Pause active implementers, then repeat with `--apply`. Owned Issues use native status and links; execution metadata remains in the `kinetic` Property. Retrying reports no further migrated tickets.
5. Confirm unrelated Issues retain their stored fields. Migration preserves their legacy status names and transition routes (`legacy_` names for collisions), while native `todo` becomes the ready state. Historical hierarchy/IDs remain; new runs use three levels.
6. Keep generated profile sources with the run's `.kinetic` folder. If interrupted, rerun migration before starting another state write.

## Live harness results

| Date | Harness | Orbit version | Steps passed | Notes |
|---|---|---|---|---|

Scripted tests do not substitute for running an actual implementer and reviewer through a supported harness.
