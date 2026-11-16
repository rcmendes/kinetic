# Orbit tracker smoke checklist

Manual acceptance test for `tracker: orbit`. Run it once against a real Orbit
Board with `orbit` 0.10.16 or later (Batches, Annotations and nested SubTasks),
and record the date, `orbit --version`, and pass/fail per step at the bottom.

## Setup

1. A scratch kinetic project (`/kinetic:start`) with one repo and its verify command registered by discover.
2. `orbit init` in the project folder (the Board anchor); in the Board's
   `config.toml` add `"kinetic"` to `non_ready_labels` and
   `max_subtask_depth = 2` under `[board]`.
3. From the project folder: `kinetic project update --json '{"tracker": "orbit"}'`
   (add `tracker_settings.orbit_dir` if the Board is not at the project folder).
4. `kinetic doctor`: `orbit-binary`, `orbit-board`, `orbit-schema`,
   `orbit-depth` and `orbit-hold-label` all pass.

## Run

5. Start a feature-lane run with a spec and two tickets in one slice, the
   second blocked by the first.
6. After specify, `kinetic state publish-spec` files the spec Task:
   `orbit list --type epic` shows it and `orbit ready` never offers it;
   `orbit show <spec>` shows the `kinetic` Annotation `{"role": "spec",
   "phase": "awaiting-approval"}` and `waiting_on` "kinetic: spec approval".
7. Approve the spec gate: `orbit show <spec>` has no `waiting_on` and the
   Annotation's `phase` is `approved`.
8. Before the breakdown gate: `orbit list --label kinetic-run-<run id>` shows
   only the spec and `<run_dir>/.kinetic/tickets.staged.json` holds both tickets.
9. Approve the breakdown gate: `orbit tree <spec>` shows the spec epic, the
   slice Task under it and both ticket SubTasks (three levels); the second
   ticket depends on the first; both carry `kinetic`; the spec's `phase` is
   `building`.
10. When the first ticket dispatches: `orbit show <id>` is `in_progress`,
    claimed by `kinetic-<run id>`, without the `kinetic` label;
    `orbit ready` (from another shell) never offers it.
11. While the implementer works, `/kinetic:status` shows no stuck ticket and
    `lease_until` moves forward at least every 5 minutes.
12. After both tickets are done, the slice Task is `done` and the spec epic
    is `done`.
13. Mark a human ticket done with `orbit done <id>`; `/kinetic:status` shows
    it done.

## Results

| Date | `orbit --version` | Steps passed | Notes |
|---|---|---|---|
