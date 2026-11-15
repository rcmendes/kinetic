---
name: status
description: "Render the state of a kinetic run: phase, tickets, frontier, gates. Uses the run of the current folder unless a run directory is given."
model: haiku
disable-model-invocation: true
triggers: [user]
argument-hint: "[run-dir]"
---

# Kinetic status

1. Pick the run directory: the argument if given, else the run of the current directory (`kinetic project current-run` prints its `run_dir`, or null). When that is null, `kinetic project runs` lists the project's open runs: use the only one, or, with several, list them and ask which. `kinetic project` exits 2 outside a project: say so and suggest `/kinetic:start`. Stop.
2. With no run, say there is no open run and suggest `/kinetic:run <ref>`. Stop.
3. Run `kinetic status --run <dir>` and print its output unchanged; that includes the ticket kind column and the "Waiting on you" line for human tickets.
4. If it fails, show the error and stop. Do not reconstruct state by hand.
