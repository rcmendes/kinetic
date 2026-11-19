# Kinetic: the session's harness, and knowing when to update

Status: draft for approval. Base: kinetic `main` (`084a2c9`, 0.7.0).

## Why

Two problems showed up after the 0.7.0 release.

1. **Several harnesses installed, one default.** `kinetic install` can set up Claude Code, Devin, Codex and OpenCode side by side (the user's machine has all four manifests). But the active harness comes from one key, `harness`, in `~/.config/kinetic/settings.json`, and `harness.py` says it is "never sniffed from the environment". Skills call `kinetic harness` with no argument, so a Codex session gets the default harness's profile: its question tool, its model tiers and its list of enforced guarantees. This is read from the code; it has not been reproduced in a live Codex session.
2. **Nothing notices an out-of-date install.** `doctor` compares generated files against the manifest and nothing else. After the 0.7.0 release the plugin was 0.7.0 while the launcher, which runs from a git checkout, was 0.6.0, and no check said so.

## Decisions

- **The session's harness is an id in the session context.** The session-start hook already runs per harness with `--harness <id>` and already emits `additionalContext`. It will always emit a first line, `kinetic harness: <id>`, including outside a project (today it returns without emitting when there is no project and no open run). Skills read that line and call `kinetic harness --harness <id>`; with no such line they call `kinetic harness` as today, so the default still works.
- The environment is still not sniffed. The id comes from the harness's own hook configuration, written by the install.
- Updating is **detect, tell, help**. Nothing updates without the user's confirmation.

## Part 1: the harness id

- `orient.py` emits `kinetic harness: <id>` as the first context line in every case, ahead of the project and run lines.
- Every skill and guide that runs `kinetic harness` (`run`, `start`, `discover`, `catalog-setup`, `catalog-refresh`, `gates.md`, `build-loop.md`, and any other found by search) says to pass the id from the context line. A test fails if a skill runs `kinetic harness` without that instruction.
- `kinetic doctor --all` runs the checks for every harness that has an install manifest, instead of only the default.
- Runs across harnesses: a run is owned by a session id. Whether a run started in one harness can be resumed in another, and whether two harnesses' hooks acting on one run conflict, is not known. First a test and a note; a fix only if it fails.
- Unverified: that the session-start hook injects context in Devin, Codex and OpenCode. The OpenCode smoke step for context injection is empty. Where it does not, the skill falls back to the default, as today, and `doctor --all` reports the gap.

## Part 2: updates

Three versions matter: the **plugin** the harness loaded, the **checkout** the `kinetic` launcher runs from, and the **latest release**.

- **Detect.** A module reads the three: the plugin's `.claude-plugin/plugin.json` (and, on Claude Code, `installed_plugins.json`), the checkout's `plugin.json`, and the latest GitHub release through `gh release view` (the `gh` CLI is already required). The release answer is cached for 24 hours in `~/.config/kinetic/update-check.json`. A failed lookup is silent and never blocks.
- **Tell.** `doctor` gains `version-skew` (plugin and checkout differ) and `update-available`. The session-start hook adds one line at most once a day, for example `kinetic 0.8.0 is available (you have 0.7.0): /kinetic:update`. A setting, `update_check: false`, turns both off.
- **Help.** `kinetic update` and `/kinetic:update`:
  1. show the release notes, which already carry a "what users must do" section;
  2. pull the checkout with `--ff-only`, only when it is clean and on `main`;
  3. re-run `kinetic install --harness <h>` for every harness with a manifest;
  4. for the Claude Code marketplace, run `claude plugin marketplace update kinetic` and `claude plugin update kinetic@kinetic`, and say a restart is needed;
  5. warn when runs are open, and check the minimum Orbit version.
  It asks before each step that changes anything.
- The first release carrying this must be installed by hand; older versions cannot check.

## Non-goals

No automatic update. No update of the harnesses themselves. No change to the Jira, GitHub or Linear adapters.

## Delivery

Part 1 in order: the context line, then the skill text and its test, with `doctor --all` and the cross-harness run test independent of both. Part 2 in order: version detection, then the notice and `kinetic update`, then docs (the release-notes convention goes into `docs/RELEASING.md`).

## Risks

- A skill that ignores the instruction and calls `kinetic harness` bare gets the default harness; this is the current behaviour, not a new failure.
- A lookup at session start must stay fast and offline-safe, so it is cached and bounded.
- Pulling a checkout can fail on local changes; `kinetic update` refuses and says why rather than forcing.
