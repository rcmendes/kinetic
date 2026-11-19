---
name: update
description: "Update kinetic: shows what is new, then pulls the checkout, reinstalls every harness and refreshes the Claude Code plugin, asking before each step."
model: sonnet
disable-model-invocation: true
triggers: [user]
---

# Kinetic update

Run scripts as `kinetic <module> ...`. Nothing here changes anything until the user says yes to that step. Ask with the harness's question tool when the profile names one (`kinetic harness --harness <id>` prints it under `Question tool:`; `none` means chat). `<id>` is the id on the `kinetic harness:` line of your session context; leave `--harness <id>` out when there is no such line.

1. Run `kinetic update`. It only reads, and prints JSON: `current` and `latest` versions, `update_available`, the `checkout` (branch, whether it is clean, how many commits it is behind), the installed `harnesses`, `open_runs`, the `requirements` (a tool a ticket store needs, with its `minimum`), the release `notes` and a `steps` list saying which of `pull`, `install` and `marketplace` can run and why not when one cannot.
2. Tell the user, in a few lines: the version they have and the latest, and the release notes (they hold a "what users must do" part: say it plainly). If `update_available` is false and `checkout.behind` is 0, say kinetic is current and stop.
3. If `open_runs` is not empty, warn that updating while a run is open swaps the scripts under it, and ask whether to continue. If a `requirements` entry has `ok: false`, say that tool is older than its `minimum` and must be upgraded; the release notes say whether a migration follows.
4. For each step in `steps` order whose `can` is true, say what it does (`does`) and ask whether to run it. On a yes, run `kinetic update --apply <step>` and show the result. On a no, skip it and go on. For a step whose `can` is false, say `why_not` once; never work around it (no forced pull, no switching branches, no stashing).
5. Stop at the first step whose result has `ok: false` and show its `output`. Do not retry or run other commands to repair it.
6. At the end: `kinetic doctor --all` (add nothing else) and report its warnings. If the `marketplace` step ran, say the Claude Code session must be restarted. For Codex, say that changed hook files must be trusted again in `/hooks`.
