# kinetic

kinetic is a plugin that drives a software change from idea to draft PRs across one or more repositories: grill the idea, write a spec, split it into tickets with typed dependency edges, build each ticket with an isolated implementer, review, ship, and run a retro. It runs inside Claude Code. Installers also exist for Devin CLI, Codex CLI and OpenCode, but their live smoke checklists (`docs/smoke/`) have not been run, so treat those three as unverified (see [Status](#status-and-known-limitations)).

The split of work is deliberate:

- **Scripts** (`scripts/kinetic/`, Python 3.9 stdlib, no LLM calls) own every deterministic decision: run state, the dependency graph, which tickets are ready, merge order, verify results.
- **Skills and agents** do the judgment work: interviewing, writing specs and tickets, implementing, reviewing, writing PR bodies.
- **Hooks** enforce the rules no prompt should be trusted with: no writes outside the run, no pushes to protected branches, no "done" with unverified edits.

It merges ideas from three plugins: Matt Pocock's skills (grill, spec, tickets, tdd, PR and retro writing), superpowers (subagent-driven development with per-task review) and dev-fleet (hook-enforced safety, multi-repo coordination, architecture review). `docs/sdlc-plugin-comparison.md` explains what each contributes; `docs/specs/kinetic-sdlc-pipeline-design.md` is the design.

## Contents

- [Install](#install)
- [Quick start](#quick-start)
- [How a run flows](#how-a-run-flows)
- [Lanes and entry points](#lanes-and-entry-points)
- [Example runs](#example-runs)
- [Approval gates](#approval-gates)
- [The build loop](#the-build-loop)
- [Ship: what the PRs look like](#ship-what-the-prs-look-like)
- [Trackers](#trackers)
- [Human tickets](#human-tickets)
- [Relationship types](#relationship-types)
- [The local board and drift](#the-local-board-and-drift)
- [The PR template override](#the-pr-template-override)
- [Commands](#commands)
- [Skills and agents](#skills-and-agents)
- [Configuration](#configuration)
- [Where state lives](#where-state-lives)
- [What the hooks enforce](#what-the-hooks-enforce)
- [Recovery and troubleshooting](#recovery-and-troubleshooting)
- [Code layout](#code-layout)
- [Tests](#tests)
- [Status and known limitations](#status-and-known-limitations)
- [License](#license)

## Install

Requirements: macOS or Linux, Python 3.9+, `git`, and `gh` authenticated to GitHub (`gh auth login`). Jira mode also needs the Atlassian MCP server; GitHub issues mode needs `gh` with issue access; Linear mode needs a Linear MCP server.

### 1. Clone and install (all harnesses)

Every harness installs from a checkout, because `kinetic install` links the CLI and generates harness files from it:

```
git clone git@github.com:rcmendes/kinetic.git ~/code/kinetic
```

Then run `kinetic install --harness <claude|devin|codex|opencode>` as described below. Every install:

- links `kinetic` onto your PATH at `~/.local/bin/kinetic` (make sure `~/.local/bin` is on your PATH; the first run uses the full path `~/code/kinetic/bin/kinetic`);
- records the harness and clone in `~/.config/kinetic/settings.json` (the last harness installed is the default for `kinetic doctor`);
- writes `~/.config/kinetic/install-manifest-<harness>.json`, which `doctor` uses to detect staleness and `--uninstall` uses to remove exactly what was written.

You can install for several harnesses side by side. After every `git pull`, re-run the install for each harness you use. To remove one: `kinetic install --harness <h> --uninstall`.

Pass `--harness <h>` to `kinetic doctor` to check a harness other than the default.

### 2. Claude Code

Via the repo's plugin marketplace (`.claude-plugin/marketplace.json`):

```
~/code/kinetic/bin/kinetic install --harness claude
claude plugin marketplace add rcmendes/kinetic
claude plugin install kinetic@kinetic
```

or, without the marketplace, load the checkout directly:

```
~/code/kinetic/bin/kinetic install --harness claude
claude --plugin-dir ~/code/kinetic
```

On Claude Code the installer only links the CLI and records settings; plugin registration (`claude plugin install` / `--plugin-dir`) stays manual. Skills appear as `/kinetic:<name>`.

### 3. Devin (CLI and desktop app)

The Devin desktop app and the Devin CLI share the same plugin system and config (`~/.config/devin/config.json`), and the app bundles the CLI. The installer needs a `devin` command on PATH and a signed-in session.

1. Put `devin` on PATH. If you installed the CLI separately, skip this. If you only have the desktop app (macOS), link its bundled CLI:

   ```
   ln -s /Applications/Devin.app/Contents/Resources/app/extensions/windsurf/devin/bin/devin ~/.local/bin/devin
   devin --version
   ```

2. Sign in (plugin management refuses to run otherwise):

   ```
   devin auth login
   ```

3. Install:

   ```
   ~/code/kinetic/bin/kinetic install --harness devin
   ```

   The installer runs `devin plugins install --local ~/code/kinetic` itself. Devin shows a trust prompt listing kinetic's skills, hooks, rules and subagents; review it and approve (the installer deliberately does not pass `-y`). It also writes Devin-format hooks into the `hooks` key of `~/.config/devin/config.json`, preserving every foreign entry.

4. Verify: `devin plugins list` shows `kinetic`, and `kinetic doctor --harness devin` reports `hooks-registered: pass`. In a Devin session, `/hooks` lists seven kinetic hooks and skills resolve as `/kinetic:<name>`.

Devin support is best effort: plugin hooks fail open, so `kinetic doctor` reports which guarantees are only Instructed. It is unverified until `docs/smoke/devin.md` has been run (it also settles whether Devin double-registers the clone's `hooks/hooks.json`, which is cosmetic if so).

### 4. Codex CLI

Requires `codex-cli` 0.16x or newer (hooks are on by default).

1. Install:

   ```
   ~/code/kinetic/bin/kinetic install --harness codex
   ```

   This is file generation only. It writes:
   - `kntc-<name>` stub skills under `~/.agents/skills` (user-only skills get an `agents/openai.yaml` sidecar with `policy.allow_implicit_invocation: false`);
   - agent definitions under `~/.codex/agents/*.toml`, with the tier resolved to `model` / `model_reasoning_effort` (override via `tiers.codex` in `~/.config/kinetic/settings.json`);
   - kinetic's hooks merged into `~/.codex/hooks.json`, preserving foreign entries.

2. **Trust the hooks.** Open Codex and run `/hooks`, review kinetic's seven hooks and trust them. They stay inactive until trusted, and `kinetic doctor --harness codex` fails its `hooks-trusted` check until Codex records the `trusted_hash` state.

3. Verify: `kinetic doctor --harness codex` should show `hooks-registered`, `hooks-trusted`, `install-manifest` and `tier-mapping` all `pass`. Invoke skills explicitly as `$kntc-run`, `$kntc-init`, and so on.

Known limitations: per-agent tool allow-lists are not honoured (the `tools:` frontmatter has no Codex agent equivalent), and the write-op/deploy confirmation hooks only fire on tool names matching `mcp__.*` patterns. Validated by `docs/smoke/codex.md`.

### 5. OpenCode (CLI and desktop app)

The `opencode` binary does not need to be on PATH: the installer file-drops its files, so it also works for the desktop app and version-managed installs.

1. Install:

   ```
   ~/code/kinetic/bin/kinetic install --harness opencode
   ```

   It writes:
   - flat `kntc-<name>.md` stub skills under `~/.config/opencode/skills` (user-only skills carry `disable-model-invocation: true`);
   - markdown agents under `~/.config/opencode/agents/*.md` with `mode: subagent`, the tier resolved to `model` (override via `tiers.opencode` in `~/.config/kinetic/settings.json`) and `permissions` deny rules translated from each agent's `tools:` allow-list;
   - a dependency-free TypeScript plugin at `~/.config/opencode/plugins/kinetic.ts` that forwards tool and session events to the Python hooks. It denies by throwing in `tool.execute.before`, asks via `permission.evaluate`, records edits in `execute.after`, and re-prompts to verify on `experimental.idle`.

2. Restart OpenCode so it loads the plugin. There is no `opencode.json` edit and no trust step.

3. Verify: `kinetic doctor --harness opencode` should show `hooks-registered: pass`, `hooks-trusted: na`, `install-manifest: pass`. Skills resolve as `kntc-<name>`.

Known limitation: the idle re-prompt is best-effort (`experimental.idle` is experimental and bounded to three rounds per session), so `verify-before-stop` stays Instructed there, while `verify` and `ship` refusing stale verify is Enforced at the CLI layer on every harness. Validated by `docs/smoke/opencode.md`.

### 6. Check the install

```
kinetic doctor --harness <h>
```

With several harnesses installed, `kinetic doctor --all` checks every harness that has an install manifest and prints one result per harness (the default harness alone when nothing is installed). `--all` and `--harness` are mutually exclusive.

Failing checks print the fix. Then continue with the [quick start](#quick-start).

### 7. Update

`kinetic doctor` also compares versions. `version-skew` warns when the plugin a harness loaded and the checkout the `kinetic` command runs from are on different versions (it needs the Claude Code plugin record, so other harnesses show no row). `update-available` warns when a newer release exists on GitHub; it asks `gh` at most once a day, caches the answer in `~/.config/kinetic/update-check.json`, and adds no row when offline or when `gh` fails. Set `"update_check": false` in `~/.config/kinetic/settings.json` to stop the lookup; `version-skew` stays because it reads only local files.

- **Guided:** `/kinetic:update` (or `kinetic update` in a terminal) prints what would change: your version and the latest, the release notes, whether the checkout is clean and on `main`, open runs, and what a ticket store needs from the machine. `kinetic update --apply <pull|install|marketplace>` runs exactly one step: `pull` is a fast-forward and refuses local changes or another branch; `install` re-runs `kinetic install` for every harness that has a manifest; `marketplace` runs the two `claude plugin` commands. The skill asks before each step.
- **Marketplace install (Claude Code):** `claude plugin marketplace update kinetic`, then `claude plugin update kinetic@kinetic`, then `kinetic install --harness claude` again so the generated files match. Claude Code keeps each installed version in its own folder (`~/.claude/plugins/cache/kinetic/kinetic/<version>`), so every release bumps the version in `.claude-plugin/plugin.json`; a change merged without a bump may not reach marketplace users.
- **Checkout install:** `git pull`, then re-run `kinetic install --harness <h>` for each harness you use.
- Check with `kinetic doctor`: it reports a stale install. Releases are described in [docs/RELEASING.md](docs/RELEASING.md).
- Orbit users: version 0.7.0 needs Orbit 0.11.0 or later, and a Board made before it needs `orbit migrate model --apply` (see the Orbit section).

Optional companions that kinetic uses when present and falls back without: the `resolving-merge-conflicts` skill and the `superpowers` and `dev-fleet` plugins, installed wherever the harness keeps skills and plugins (`~/.claude/skills` and `~/.claude/plugins` on Claude Code; `~/.config/devin/skills` or `~/.agents/skills` and `devin plugins install` on Devin; `~/.agents/skills` on Codex). `/kinetic:doctor` reports which are missing.

## Quick start

1. `/kinetic:start` talks through the goal and offers to create a project folder containing its config, bare clones and runs. Open a new session inside that project when prompted. Discovery settles the repos and verify commands; decompose settles the tracker.
2. `/kinetic:run "let partners export statements as CSV"` (or a Jira key, or a path to a spec) starts a run. kinetic picks a lane, says why in one line, and lets you override it.
3. Answer the grill questions, approve the spec, approve the ticket breakdown. From there implementers work in the background; you are asked only when one needs a decision.
4. Approve the ship gate. kinetic pushes the integration branches and opens one draft PR per repo, in merge order.
5. The retro presents improvement candidates and drafts a runbook and release notes under the run directory.

At any point `/kinetic:status` shows where the run is, and `/kinetic:ask` prints the next step.

## How a run flows

```text
  idea / Jira key / spec
          |
       [intake] -- pick lane when the repos are known
          |
       [grill] -- interview; GLOSSARY.md and ADR drafts in <run_dir>/docs/
          |
     [discover] -- candidate repos, approved sources, worktrees; final lane
          |
     [specify] -- spec_lint must pass ---------------> GATE: spec
          |
   [decompose] -- slices, per-repo tickets, typed edges
          |      graph validate must be clean ------> GATE: breakdown (records the grant)
          |
       [build] -- frontier -> implementer per ticket (background, own worktree)
          |      -> DONE only with a passing verify on record
          |      -> spec + standards reviewers -> scoped fix rounds -> merge into integration
          |      -> pin downstream repos on snapshot edges
          |
      [review] -- architecture and security review per repo, fix tickets
          |      verify every changed repo ----------> GATE: ship
          |
       [ship] -- merge order, PR bodies, draft PRs (push and PR hooks confirm)
          |
       [retro] -- retro, runbook.md, release-notes.md, cleanup offer
          |
        [done]
```

`/kinetic:run` is the only orchestrator. It sets the phase in state before each step, reads the phase skill and follows it. No skill calls another skill and no subagent dispatches another subagent.

## Lanes and entry points

| Lane | When | Phases |
|---|---|---|
| `quick` | One small ticket, one repo known at intake, no design decisions | intake, discover, build, review, ship, retro |
| `feature` | One repo, needs a spec | grill, discover, specify, decompose, build, review, ship, retro |
| `epic` | Several repos | all phases; per-repo integration branches, typed edges |

The quick lane has no grill, spec or decompose phase. Its breakdown gate is a single question that also asks which seams the tests may use, because a background implementer has no one to ask.

A run starts at the first missing artifact:

| You have | The run starts at |
|---|---|
| An idea or a bare ticket | grill |
| A spec, no tickets | discover |
| A spec and tickets with edges | build |
| One small ticket, no spec | the quick lane |

A spec brought in from outside still goes through the spec gate, and tickets brought in still go through the breakdown gate. `--from build` (and `/kinetic:implement <ref>`) skips straight to build for an already specified ticket; the breakdown gate still applies.

## Example runs

These walk through one run per lane. Repo names, ticket text and commands are illustrative; the phases, gates, ids, branches and paths follow what kinetic does. Each example assumes local tracker mode and a project at `~/workspace/kinetic`, so the run directory is `~/workspace/kinetic/runs/<run-id>/`.

### Quick lane: one small fix in one repo

```
/kinetic:run "api: reject statement exports with an end date before the start date"
```

1. **Intake.** kinetic finds one repo in play (`api`) and no design decisions: "quick lane: one small ticket in api, no spec needed." You can override it. Run id: `reject-inverted-export-dates-2026-11-11`.
2. **Breakdown gate (one question).** kinetic registers the single ticket `01-api-reject-inverted-dates` with no blockers and asks you to approve it. The question spells out the grant and asks which test seams the implementer may use, for example "the export request validator, through its public validate method". Your answer goes into `issues/01-api-reject-inverted-dates.md`.
3. **Build.** The integration worktree `worktrees/api` is created on branch `kinetic/<run-id>/integration`. The ticket gets `worktrees/api-01-api-reject-inverted-dates` on `kinetic/<run-id>/01-api-reject-inverted-dates`. One implementer writes the failing test, the fix and runs `mvn -q verify`, then reports `DONE`. The spec and standards reviewers check the diff, and the ticket merges into the integration branch.
4. **Review.** Architecture and security review run on `api` if those skills are installed. `api` is verified again.
5. **Ship gate, then ship.** You see the branch, base `main` and the passing verify. On approve, kinetic opens one draft PR in `api`, titled `<run-id>: <title>` (local mode has no Jira key).
6. **Retro.** It presents improvement candidates for the repo and its checks, ordered by severity, and drafts `docs/runbook.md` and `docs/release-notes.md` for you to review.

Starting from a ticket file instead: `/kinetic:implement ~/notes/fix-dates.md` registers that one ticket and copies the file to `issues/` before dispatch.

### Feature lane: one repo, needs a spec

```
/kinetic:run "web: let partners download their monthly statement as CSV"
```

1. **Intake.** One repo (`web`), but there are design choices to settle, so: "feature lane: one repo, needs a spec."
2. **Grill.** An interview, one question at a time: which partners can download, which columns, how large a month gets, what must never appear in the file (threat model), how fast it must be (non-functional requirements). Agreed terms go into `docs/GLOSSARY.md`; a hard-to-reverse choice, such as "generate on request rather than nightly", becomes an ADR draft in `docs/adr/0001-on-request-csv.md`.
3. **Spec, then spec gate.** `spec.md` is written and must pass `spec_lint` (non-empty Threat model, Non-functional requirements and Acceptance criteria sections). You approve it, or revise and see it again.
4. **Tickets, then breakdown gate.** Two vertical slices, each a ticket in `web`:
   - `01-web-csv-endpoint`, no blockers;
   - `02-web-download-button`, blocked by `01-web-csv-endpoint` with a `merge` edge.

   `graph validate` must be clean before you see the list and approve the grant.
5. **Build.** Round 1 dispatches only `01-web-csv-endpoint` (one implementer per repo at a time). After it is reviewed and merged, round 2 cuts the worktree for `02-web-download-button` from the integration branch, so it already contains the endpoint.
6. **Review, ship gate, ship, retro.** As in the quick lane: one draft PR in `web`, whose body lists both tickets.

### Epic lane: several repos with typed edges

```
/kinetic:run INV-4321
```

Here the tracker is Jira, so ticket ids are Jira keys and the run id is `INV-4321`.

1. **Intake.** kinetic reads the Jira issue. It names only `api`, so the repo set is not known yet: provisional `feature` lane, start at the grill.
2. **Grill, then discover.** The grill settles what the change is and names the interface `StatementClient.export`. Discover asks once: use the saved base folder `~/code`, any other source, and may it search the GitHub org `acme`? It scans `~/code`, and a code search for `StatementClient.export` finds `statement-client` (a library) and `web`. You confirm all three; discover cuts their integration worktrees and sets the lane: "Epic lane: three repos."
3. **Spec.** As in the feature lane, but the spec also covers how the repos interact. It is published as a Jira issue under project `INV`, and its key is noted at the top of `spec.md`. Spec gate.
4. **Tickets, then breakdown gate.** One slice, with one child ticket per repo, linked with Jira "blocks" links and edge labels:

   | Ticket | Repo | Blocked by |
   |---|---|---|
   | INV-4322 | statement-client | none |
   | INV-4323 | api | INV-4322 (`snapshot`) |
   | INV-4324 | web | INV-4323 (`merge`) |

   `INV-4322` produces `StatementClient.export`, which `INV-4323` consumes; `graph validate` would reject the breakdown if `INV-4323` were not blocked by `INV-4322`. The `snapshot` edge is allowed because `statement-client` has `publish_snapshot` and `api` has `pin` in the project's repo config. Without them, the decompose phase would use a `merge` edge. You approve the breakdown and the grant.
5. **Build.** Each repo has its own integration branch `kinetic/INV-4321/integration`, cut at discover.
   - Round 1: only `INV-4322` is ready. Its implementer adds the client method. After review it merges into `statement-client`'s integration branch, and `kinetic.gitops pin` publishes the snapshot and pins `api` to it.
   - Round 2: `INV-4323` is ready. Its worktree is cut from `api`'s integration branch, which now carries the pin, so the implementer builds against the new client.
   - Round 3: once `INV-4323` merges into `api`'s integration branch, `INV-4324` starts in `web`.

   If an implementer returns `NEEDS_DECISION` (say, "paginate the export or stream it?"), you answer once. The answer is recorded as a ruling and the same implementer resumes.
6. **Review.** Architecture and security review per repo. A confirmed finding in `api` becomes the fix ticket `api-review-1`, built through the same loop. Every changed repo is verified again.
7. **Ship gate, then ship.** The merge order is `statement-client, api, web`. kinetic opens three draft PRs in that order, titled `INV-4321: <title>`. `api`'s PR lists the `statement-client` PR under Depends on, and has a warning that its snapshot pin must move to a released version before merge. After the last PR exists, the first two are updated so their Related PRs list all three.
8. **Retro.** The runbook gives the rollout and rollback order across the three repos. The release notes call out the pin and the new client version.

Starting from an existing spec instead: `/kinetic:run ~/specs/csv-export.md` starts at discover, then the spec gate and tickets.

## Approval gates

Three gates, each asked with a multiple-choice question and recorded in state (`gates`). A rejected gate sends the run back one phase with your feedback; the run never advances past an unapproved gate.

| Gate | When | What you see |
|---|---|---|
| `spec` | After the specify phase | The spec location and a three-line summary. The spec must already pass `spec_lint`. |
| `breakdown` | After tickets | Every ticket with its repo and blockers by edge type. Approving records the **grant**. |
| `ship` | After review | Per repo: integration branch, base branch, merge order, latest verify result. |

The grant is what the build agents may do without asking again: commit and push to ticket branches, make Jira transitions, open draft PRs. It never covers pushes to `main`, `master` or `Release*`, force-push after review, deploys, or production data writes. A hook denial is final: git pushes and `gh pr create|merge|edit|ready` are hook-confirmed (as are Atlassian MCP writes); other `gh pr` writes (close, comment, review), `gh issue`, `gh label` and `gh api` tracker writes, and other MCP tracker writes such as Linear, are covered only by the grant.

## The build loop

### Branches and worktrees

Each repo gets an **integration branch** `kinetic/<run-id>/integration`, checked out at `<run_dir>/worktrees/<repo>` by the discover phase. Until the breakdown gate is first approved, everything under `<run_dir>/worktrees/` is read-only (isolation hook). Once given, the grant stays: rejecting a re-asked breakdown gate later does not make the repos read-only again. Each ticket gets its own branch `kinetic/<run-id>/<ticket-id>` and worktree `<run_dir>/worktrees/<repo>-<ticket-id>`, cut from the integration branch at dispatch time, so it already contains the work of the tickets it depends on.

### Dependency edges

Tickets block each other with exactly three edge types (in Jira, a "blocks" link plus a label `edge:snapshot`, `edge:merge` or `edge:release`):

| Edge | The downstream ticket may start when | Example |
|---|---|---|
| `snapshot` | the upstream repo has published a snapshot build and the downstream repo is pinned to it | `web` consumes a client library `api` publishes |
| `merge` | the upstream ticket is merged into its integration branch | a second ticket in the same repo builds on the first |
| `release` | the upstream version is actually released (marked by hand) | a consumer that can only use a released artifact |

`snapshot` edges need `publish_snapshot` and `pin` configured for the repos involved; without them the decompose phase uses `merge` edges instead.

### Interfaces

Each ticket also lists the interfaces it `produces` and `consumes` (an endpoint, a client method, an event, a schema), with one stable name per interface, such as `StatementClient.export`. `graph validate` treats a ticket that consumes what another ticket produces, without being blocked by it directly or transitively, as an error: it would start before what it needs exists. An interface no ticket produces is a warning shown at the breakdown gate, to confirm it already exists in the code.

### Rounds

1. `kinetic.frontier` lists the ready tickets: blockers satisfied, at most 3 running at once and at most 1 per repo.
2. Each ready ticket gets its ticket file (`<run_dir>/issues/<id>.md`), its worktree and status `running`, then all are dispatched in one message as background `implementer` agents. Each implementer follows `tdd` and saves the failing test run as RED evidence, reviews its own diff, runs the repo's verify command through `kinetic.verify` (doing a root-cause pass if it fails), and writes a report with a status: `DONE`, `DONE_WITH_CONCERNS`, `NEEDS_CONTEXT`, `NEEDS_DECISION` or `BLOCKED`.
3. On `DONE`, the ticket is marked done only if `set-ticket` finds a passing verify for it newer than its last edit; a report is a claim, the verify record is the evidence. Then the `spec-reviewer` (which also checks the RED evidence matches an acceptance criterion) and the `standards-reviewer` review the ticket's diff against the integration branch.
4. Confirmed findings start **fix rounds**, at most 3: rounds 1 and 2 resume the same implementer (it keeps its context), round 3 is a fresh implementer on opus. After each round the `fix-reviewer` checks only the fix: each finding ADDRESSED or NOT, plus new breakage in the fix. Issues outside the fix are saved for the integration review instead of extending the loop. Findings still open after round 3 are adjudicated: blocking ones go to you, the rest are recorded as rulings.
5. The ticket branch is merged into the integration branch (conflicts use `resolving-merge-conflicts` when available, otherwise you are asked). Downstream repos on a `snapshot` edge are pinned to the new snapshot.
6. On `NEEDS_CONTEXT` the orchestrator answers from state, config, the spec or the ticket and resumes the implementer; you are asked only if it cannot. On `NEEDS_DECISION` or `BLOCKED` you are asked, the answer is recorded as a ruling, and the same implementer resumes.

If the frontier is empty while tickets are still `todo` and nothing is running, the run is stalled on edges that will not clear by themselves. kinetic shows each waiting edge and asks whether to change its type, mark a release, or stop; it never dispatches around an edge.

Ticket statuses: `todo`, `running`, `done`, `done_with_concerns`, `needs_decision`, `blocked`, `merged`, `released`.

### Review phase

The `integration-reviewer` (opus) always runs once for the whole run. It reads every repo's integration branch against its base and looks for what no per-ticket review can see: contracts that disagree across repos, wiring that was never connected, tickets that conflict once merged, acceptance criteria no ticket fully delivered, and changes that are unsafe in the merge order. In addition, for each repo, `dev-fleet:architecture-review` and `/security-review` run when available (kinetic says which ran). Confirmed findings become fix tickets named `<repo>-review-N` that go through the same build rounds. Every changed repo is verified again; a failing verify blocks the ship gate.

## Ship: what the PRs look like

The ship phase gets the merge order from `kinetic.gitops merge-order` (upstream repos first). It reuses an open PR already on the branch, skips a repo whose PR was merged or closed, shows one table (repo, branch, title, base, depends on), then pushes each integration branch and opens a **draft** PR. PR titles are `<KEY>: <title>`, where KEY is the Jira key or the run id.

Every PR body has the same section order, so reviewers read every PR of a run the same way:

```markdown
### Tickets:
- <KEY or local ticket id> - <ticket title>

### Description:
<2-4 lines: what changes in this repo and why>
Merge in this order: api, web

#### Design decisions
| Decision | Why |
|---|---|

#### Architecture
<ASCII diagram, * = changed in this PR>

### Attention points:
> [!WARNING]
> <merge blockers: snapshot pins to revert, PRs that must merge first>

- **Risk:** <one-way or two-way door; blast radius>
- **Evidence:** <verify command and result from the run's verify records>
- **Unverified / gaps:** <...>

### Manual steps:
- <ref> - <title> (done by human: <note>)

### Ships with:
<PRs in other repos that must merge and deploy together>

### Related PRs:
| PR | Merge order | Relationship |

### Depends on:
- [ ] <org>/<repo>#<n>
```

Bodies are written for developers: no lists of files touched, every design decision with its reason (or flagged as unverified), evidence taken from the recorded verify runs rather than invented. Each body is re-read as a teammate who has seen neither the diff nor the tickets before it is saved. Once all PRs exist, earlier PRs are updated so their Related PRs list every sibling. No CI directive lines are added; Depends on and the merge order carry the dependency.

## Trackers

The tracker is where tickets live. Decompose selects it in the project's `.kinetic/config.json`; a repo cannot switch it. Change project settings through `kinetic project update --json '{...}'`:

| `tracker` | Needs | Settings |
|---|---|---|
| `jira` | Atlassian MCP | `jira_project`, `slice_issue_type`, `child_issue_type` |
| `github` | `gh` | `github_repo` |
| `linear` | a Linear MCP server | `linear_team` |
| `local` (the default) | nothing | none |
| `orbit` | `orbit` 0.11.0 or later, contract 1 and the kinetic profile | none; optional `orbit_dir` |
| `other` | whatever you describe | `tracker_description` |

Each tracker is one prose file, `trackers/<name>.md`, that tells the skills how to create a ticket, link blockers, set a status and so on in that tracker. The prose adapter describes tracker operations; Orbit also has a native ticket store. Adding a tracker with local ticket storage means adding one adapter file. Skills use roles, not tracker words: `names.kind.human` (the label or type for a human ticket), `names.edge.merge`, `names.after`, `names.ships_with`, `names.related` and `names.status.<role>`.

To rename any of these for your project, set `tracker_names` in the project config:

```json
{
  "tracker": "jira",
  "tracker_settings": { "jira_project": "INV", "slice_issue_type": "Story", "child_issue_type": "Sub-task" },
  "tracker_names": { "kind": { "human": "manual" }, "status": { "done": "Closed" } }
}
```

`tracker_names` is merged key by key over the adapter's defaults; a missing role is an error. `kinetic tracker resolve` prints what kinetic resolved. The local ticket store stays authoritative for remote trackers; Orbit uses its native Board as the ticket store.

### Orbit: tickets on an Orbit Board

The native Orbit workflow is shipped in `profiles/kinetic.toml`. `kinetic install --harness <name>` registers it when Orbit is on PATH; run `orbit setup kinetic` at the Board anchor to apply it. See [the profile smoke check](docs/smoke/orbit-profile.md) for native statuses, review policy and setup verification. Applying the profile changes the Board workflow; migrate existing runs before switching them.

Orbit work stays `in_review` until both spec and standards review approve. `kinetic state submit-review` attaches passing verify evidence; `record-review` records independent verdicts and native fix attempts. Integration review targets the run's owned spec after full repo verifies.

With `tracker: orbit`, a run's tickets live on an [Orbit](https://github.com/rcmendes/orbit) Board. The ticket store reads native Issue status, parent, assignee, lease and typed links; its `kinetic` Property holds only execution metadata. Other trackers keep the local ticket store. To add a native store for another tracker, see [docs/stores.md](docs/stores.md): the interface, the optional capabilities a store declares, and the contract suite it must pass.

- **Hierarchy.** Each run owns a top-level `epic` spec, `task` slices beneath it, and `sub-task` tickets beneath each slice. Starting from a user Issue adds a nonblocking `relates-to` link from the owned spec; kinetic never claims, moves or closes that user Issue. Spec approval answers the owned spec's decision gate. The spec stays open until integration review approves it.
- **Publication.** Tickets are staged in `<run_dir>/.kinetic/tickets.staged.json`, validated, then published in one batch at the breakdown gate. Source Keys make repeated publication safe. Each owned Issue carries `kinetic-run-<run-id>` for discovery.
- **Native workflow.** Tickets use `todo`, `running`, `needs_decision`, `blocked`, `in_review`, `done`, `done_with_concerns`, `merged` and `released`. Labels identify runs and human tickets; they do not translate lifecycle states. Blocking links carry the `snapshot`, `merge` or `release` edge attribute. `after`, `ships-with` and `relates-to` links remain nonblocking.
- **Identities and liveness.** Each ticket implementer uses `kinetic-<run-id>-implementer-<ticket-id>`. Spec, standards, fix and integration reviewers have distinct identities and never take the author's lease. Post-tool hooks heartbeat the active implementer assignee at most every five minutes per worktree. An expired lease appears as “implementer may be stuck.” Human tickets remain manual; `kinetic state human-done` records their completion.
- **Review.** A passing verify becomes submission evidence. Both ticket review axes must approve before the ticket completes; a request for changes starts the next attempt. Integration review completes the owned spec. Review policy requires a reviewer distinct from the author.
- **Reads and writes.** Status and board resume `orbit changes --since` from `.kinetic/orbit-feed.json`. State writes use atomic batches with version guards and a persisted idempotency key in `.kinetic/orbit-write.json`; retrying after a crash does not duplicate the write. Concurrent versions refresh and retry up to three times.
- **Preflight.** `orbit capabilities` must report contract major 1 and the required workflow, policy, issue types, hierarchy, links, properties, assignee, lease, batch, idempotency, profiles and setup capabilities. Missing requirements stop the operation with their names. Optional remote, workspace, scheduler, hooks and index modules may be absent. Doctor checks the native kinetic profile/statuses.

### Existing Orbit runs

First migrate the Board model with `orbit migrate model` (preview), then `orbit migrate model --apply` at its anchor. Existing run metadata remains readable for status reports. Preview the kinetic conversion with `kinetic orbit_store migrate --run <run_dir>`; add `--apply` to convert that run's owned Issues to native statuses, typed links and Properties. Repeat for each existing run. Pause active implementers before applying migration. Applying a migration needs Python 3.11 or later (preview runs on any supported version).

Migration changes the Board workflow. It retains existing status names for unrelated Issues, keeps old transitions with conflicting names under `legacy_<name>`, and uses native `todo` as the ready state. Unrelated Issues keep their stored status, assignee, links and timestamps; their available transitions and readiness follow the new Board workflow. Historical hierarchy and Issue IDs are retained because Orbit has no reparent operation. New runs use the three-level hierarchy. Applied profile sources remain in the run's `.kinetic` directory; keep them with the run. A failed migration can be retried; the Board's Git history records profile and Issue changes.

## Human tickets

Some steps only a person can do: request a credential, flip a flag in a console, get a sign-off. A ticket with `kind: human` models it. It has no repo, is never dispatched to an implementer, and sits in the graph like any ticket, so other tickets can be blocked by it.

- **Waiting on you.** `/kinetic:status` and the board list human tickets whose blockers are satisfied under "Waiting on you".
- **Only human work left.** When nothing is ready or running, agent tickets remain un-merged, and every remaining ticket is human or behind a human ticket you can act on, the run pauses and says so instead of reporting a stall; say `ship` to continue with those human tickets open; ship then lists the agent tickets still behind them as not merged and makes them merge blockers. The build loop ends once every agent ticket is merged, so human tickets still open then are carried into review and ship as open items.
- **`human-done`.** `kinetic state human-done --run <run_dir> --id <id> [--note "..."]` marks it done. `set-ticket` refuses `done` on a human ticket. An edge onto a human blocker is satisfied when it is `done`, whatever its edge type.
- **`NEEDS_HUMAN`.** An implementer that hits a step only a person can do reports `NEEDS_HUMAN`. kinetic creates a new human ticket, blocks the original on it and parks the original until you finish it. The original keeps its repo and worktree.
- **Ship.** Ship still opens draft PRs while a human ticket is open; open human tickets that block a repo's tickets appear as a merge blocker under Attention points and in Manual steps.

## Relationship types

Besides `blocked_by` (with the `snapshot`, `merge` and `release` edge types), tickets can carry three softer relationships:

| Field | Meaning | Effect |
|---|---|---|
| `after` | Prefer to build this after another ticket | Breaks ties in build order only; never blocks; a soft cycle never blocks either and is reported by `graph validate` as a warning only |
| `ships_with` | A group name shared by tickets that must be released together | Merge order puts the group's repos next to each other when blockers allow; PR bodies get a "Ships with" section |
| `related` | Context only | Listed in the ticket; a related human ticket also appears in the PR's Manual steps |

`graph validate` reports unknown `after` or `related` ids, an `after` that contradicts a `blocked_by`, and a `ships_with` group of one ticket. `kinetic gitops ships-with --run <run_dir>` prints the groups by repo. Tickets written by 0.2.0 need no migration: missing fields default to `kind` agent and empty relationships.

## The local board and drift

In local mode tickets are markdown files under `<run_dir>/issues/`, and `issues/README.md` is a board: "Waiting on you" first, then every ticket with its kind, status and blockers. kinetic state is the source of truth; the files are a view of it.

- `kinetic board header --run <run_dir> --id <id>` writes a ticket file's header (Status, Type, Repo, Slice, Blocked by, After, Ships with, Related, Assignee). Comments below the header are kept. Assignee is derived (human: the user, agent: the implementer), not stored.
- `kinetic board render --run <run_dir>` rewrites the board.
- `kinetic trace --run <run_dir> [--id <spec|slice|ticket>] [--json]` prints spec, slices and tickets as a tree, for the local and Orbit stores alike. `specify` records the spec with `kinetic state publish-spec` (or `set-spec` when the ticket store does not publish it).
- `kinetic board drift --run <run_dir>` lists files that disagree with state. A file written by hand shows drift until its first `header`. If you mark a human ticket done by editing its file, kinetic sees the drift and asks before applying it.

## The PR template override

Ship fills one template for every PR. The default is `templates/pr-body.md`. To use your own, set `pr_template` to a file path, in the project config for every repo or in a repo-root `.kinetic.json` for one repo. The first of repo `.kinetic.json`, project config and the plugin default wins; a relative path resolves against the directory of the file that set it, and a configured file that is missing is an error. `kinetic tracker pr-template --repo <path>` prints the path used. Placeholders are `<<TITLE_KEY>>`, `<<TICKETS>>`, `<<DESCRIPTION>>`, `<<DESIGN_DECISIONS>>`, `<<ARCHITECTURE>>`, `<<ATTENTION_POINTS>>`, `<<MANUAL_STEPS>>`, `<<SHIPS_WITH>>`, `<<RELATED_PRS>>` and `<<DEPENDS_ON>>`; the filled placeholders are kept in `<run_dir>/pr/<repo>.placeholders.md` for the backfill once all PRs exist.

## Commands

| Command | What it does |
|---|---|
| `/kinetic:run <JIRA-KEY\|idea\|spec-path> [--from build]` | Orchestrates a run end to end, stopping at each gate |
| `/kinetic:implement <JIRA-KEY\|ticket-file\|spec-path>` | Alias for `run --from build` on an already specified ticket |
| `/kinetic:start [idea\|ticket-ref]` | Talks through a goal and offers a project; inside a project, hands off to run |
| `/kinetic:doctor` | Read-only health check with a one-line fix per failure |
| `/kinetic:status [run-dir]` | Phase, tickets, frontier and gates of a run (the active one by default) |
| `/kinetic:ask` | The lanes and the next step `next_step` computes for the active run |
| `/kinetic:update` | Shows what is new, then pulls the checkout, reinstalls every installed harness and refreshes the Claude Code plugin, asking before each step |
| `/kinetic:cleanup [run-dir]` | Removes the run's local worktrees and branches whose work is merged, after one confirmation |
| `/kinetic:bare-clone <org>/<name> ... \| <url> <name>` | Makes bare clones in that folder |
| `/kinetic:bare-refresh [repo ...]` | Fetches into the bare clones and reports what advanced |
| `/kinetic:catalog-setup [folder]` | Detects and saves the repo catalog folder |
| `/kinetic:catalog-refresh <keys...> \| --query <q> \| --run <dir>` | Learns repo labels and recipes from merged deliverables; writes only what you approve |

### Run-state commands

Skills call these; you can too. All take `--run <run_dir>`.

| Command | What it does |
|---|---|
| `kinetic status --run <dir> [--summary]` | The run's tickets, frontier and gates; each ticket shows a `P0` to `P3` priority, the rank of how much work waits behind it. `--summary` prints only lane, phase, progress counts and gates, asking the ticket store for the counts instead of reading every ticket |
| `kinetic state ask --id <ticket> --question "..."` | Parks a ticket on a person as `needs_decision` and records the question where the ticket store keeps questions |
| `kinetic state answer --id <ticket> --answer "..."` | Closes the question and returns the ticket to the status it had |
| `kinetic state questions` | Lists the run's open questions |
| `kinetic state checkpoint --id <ticket> --progress "..." [--next ...] [--blocker ...]` | Records a ticket's handoff so a crashed or replaced implementer can resume; `last-checkpoint --id <ticket>` reads it. A store without checkpoints reports `recorded: false` and nothing changes |
| `kinetic state submit-review`, `record-review` | Review evidence and verdicts (see the Orbit section) |

A ticket store only offers what its tracker can do. Stores declare optional capabilities and kinetic falls back to its own behaviour for the rest, so the local store and a store written later behave predictably. [docs/stores.md](docs/stores.md) lists the capabilities and how to write a store.

## Skills and agents

| Name | Kind | Model | Role |
|---|---|---|---|
| `run` | command | sonnet | The orchestrator: lanes, gates, phases, build loop |
| `implement` | command | sonnet | `run --from build` |
| `start`, `doctor`, `status`, `ask` | commands | sonnet, haiku, haiku, haiku | Project creation and read-only views |
| `update` | command | sonnet | Reads `kinetic update` and runs its steps one by one, each after the user agrees |
| `cleanup` | command | sonnet | Removes merged worktrees and branches of a run |
| `bare-clone` | command | sonnet | Bare-clones `<org>/<name>` or a URL into that folder |
| `bare-refresh` | command | sonnet | Fetches into the bare clones; local branches never move |
| `catalog-setup` | command | sonnet | Detects, asks and saves the catalog folder (`catalog_dir`) |
| `catalog-refresh` | skill | sonnet | Reads merged deliverables, proposes recipes and repo terms, applies what you approve; `cleanup` offers it |
| `grill` | phase skill | inline | Interview covering threat model and non-functional requirements; keeps the glossary and ADR drafts |
| `discover` | phase skill | inline | Finds the run's repos from approved sources (base folders, your sources, the GitHub org), confirms them with you, clones them into `bare-repos/` (or creates a new one on GitHub), registers them and cuts their worktrees |
| `specify` | phase skill | inline | Spec with testing decisions, linted by `spec_lint`, published through the ticket store (the tracker or `<run_dir>/spec.md`) |
| `decompose` | phase skill | inline | Vertical slices, per-repo tickets, typed edges, validated by `graph validate` |
| `ship` | phase skill | inline | PR bodies, merge order, draft PR set |
| `retro` | phase skill | inline | Retro, runbook, release notes |
| `tdd` | skill | sonnet | Test-first loop the implementer preloads (`skills: [tdd]`) |
| `implementer` | agent | sonnet | One ticket in its own worktree; tools: Read, Edit, Write, Grep, Glob, Bash |
| `spec-reviewer` | agent | opus | Read-only: does the diff do what the ticket asks; is the RED evidence real |
| `standards-reviewer` | agent | opus | Read-only: does the diff meet the repo's standards |
| `fix-reviewer` | agent | sonnet | Read-only, scoped: is each finding fixed, did the fix break anything |
| `integration-reviewer` | agent | opus | Read-only, whole run: cross-repo contracts, wiring, ticket interactions, merge order |

Phase skills run inline in the orchestrator's session, so their `model:` field has no effect; they use whatever model the session runs. Reviewers have no Write tool: they return text and the orchestrator saves it under `<run_dir>/reviews/`.

## Configuration

Project settings live in `<project>/.kinetic/config.json` and are changed through `kinetic project update --json '{...}'`. Discovery registers one entry per repo under `repos` (`verify` is required):

```json
{
  "api": {
    "path": "/Users/me/code/api",
    "verify": "mvn -q verify",
    "publish_snapshot": "mvn -q deploy -DskipTests",
    "pin": "./scripts/pin-api.sh \"$KINETIC_UPSTREAM_REF\"",
    "base_branch": "main"
  },
  "web": {
    "path": "/Users/me/code/web",
    "verify": "npm test"
  }
}
```

- `verify`: the command that proves the repo works; its exit code is the verdict.
- `publish_snapshot`: builds and publishes a snapshot other repos can consume.
- `pin`: runs in the downstream repo and points it at the upstream snapshot; it reads `KINETIC_UPSTREAM_REF`.
- `base_branch`: the branch PRs target (default `main`).

A repo-root `.kinetic.json` with the same keys overrides the central entry key by key:

```json
{ "verify": "make test" }
```

Machine-level discovery defaults in `~/.config/kinetic/settings.json`:

```json
{
  "repo_roots": ["/Users/me/code"],
  "github_orgs": ["acme"],
  "catalog_dir": "/Users/me/code/team-docs/repo-catalog"
}
```

`repo_roots` (folders whose child repos discovery may scan) and `github_orgs` (orgs discovery may search) are written by the discover phase when you approve them, and offered as defaults on the next run. Missing keys mean none.

The project owns `bare-repos/`, one bare clone per repo. Discovery fetches approved sources there and cuts run worktrees from `origin/<base>`.

`catalog_dir` is the repo catalog folder; `/kinetic:catalog-setup` writes it. Unset means no catalog anywhere. It holds `index.md` (generated), `terms.md` (the controlled vocabulary), `repos/<repo>.md` (domain terms per repo, with the deliverables behind each), `recipes/<slug>.md` (one kind of change, its parameters, and per repo what it is needed for, with a strength: always, usually, sometimes or seen once) and `ledger.json` (deliverables already cataloged). `/kinetic:catalog-refresh` fills it through `kinetic catalog apply` after you approve each recipe; `discover` reads it as its first source. Put it inside a git checkout to share it with a team; kinetic never commits it.

The 0.2.0 top-level `jira_project`, `slice_issue_type` and `child_issue_type` keys are still read. With `"tracker": "local"` (or no `tracker` key) tickets are files under `<run_dir>/issues/`. See [Trackers](#trackers) for the other trackers, `tracker_settings` and `tracker_names`, and [The PR template override](#the-pr-template-override) for `pr_template`.

## Where state lives

```text
<project>/
  .kinetic/config.json  project config: repos, verify and setup commands, tracker, names, pr_template
  .kinetic/sessions/    which session is bound to which run
  bare-repos/<repo>.git bare clone per repo; every worktree is cut from it
  runs/<run-id>/        one folder per run (<run_dir>):
    spec.md               the spec; a tracker-published spec is generated from it (its reference is noted at the top)
    discovery.md          repos chosen by discover, with reasons and sources
    docs/                 notes.md and refs/ from /kinetic:start, GLOSSARY.md, adr/, runbook.md, release-notes.md
    issues/<id>.md        ticket files (local mode, quick lane, fix tickets)
    reviews/<id>-<axis>.md  reviewer findings
    needs-human/<id>-<N>.md the implementer report that raised a human ticket
    pr/<repo>.md          PR bodies
    worktrees/<repo>      integration worktree per repo
    worktrees/<repo>-<id> ticket worktree per ticket
    .kinetic/             bookkeeping: state.json (flock-guarded, written atomically),
                          reports/<id>.md (implementer reports), logs/ (verify and setup logs),
                          tickets.staged.json (tickets staged for publishing)
```

`kinetic state load --run <run_dir>` prints the whole state: lane, phase, tickets, slices, repos, gates, rulings, verify results and the owning session.

## What the hooks enforce

| Hook | Event | Rule |
|---|---|---|
| `completion_gate` | Stop | Blocks "done" while a repo has edits newer than its last passing verify, and names the command to run |
| `isolation` | PreToolUse on file writes | Denies writes outside the registered repos and the run directory |
| `confirm_write_ops` | PreToolUse on Bash and GitHub/Jira MCP writes | Denies `git push` to `main`, `master` or `Release*`; asks before other pushes, `gh pr create|merge|edit|ready` and Jira or GitHub MCP write calls |
| `confirm_deploy` | PreToolUse on deploy and Tekton-trigger MCP calls | Asks first |
| `record_edit` | PostToolUse on file writes | Records edits so the Stop gate can compare them with verifies |
| `claim_run` | PostToolUse on Bash | Binds the session to the run selected with `kinetic project use-run` and records its owner |
| `orient` | SessionStart | Names the session's harness (`kinetic harness: <id>`, always, even outside a project), then tells it about the active run and the next step |

The run-scoped hooks (isolation, record_edit, completion_gate) act in the session bound to a run and on calls inside that run's directory, including subagent work there. Other sessions stay independent. The push and deploy hooks always apply.

## Recovery and troubleshooting

- **Resume after a crash or in a new session.** Run `/kinetic:run` again. Inside the project it finds an open run, binds the session with `kinetic project use-run`, and resumes at the recorded phase. Recorded gates stay approved. For a ticket still `running` with no report, kinetic shows what its worktree holds and asks whether to re-dispatch an implementer there or set it back to `todo`.
- **A crash during a fix round.** Recovery cannot yet tell a dead fix agent from a finished first round; check the report's latest `## Fix round` section and `<run_dir>/reviews/<id>-fix-N.md` by hand.
- **`set-ticket` refuses `done`.** The ticket has no passing verify newer than its last edit. Re-run verify in its worktree (`kinetic verify --run <run_dir> --repo-name <repo> --repo-path <worktree> --ticket <id>`) or resume the implementer.
- **Clean up after merge.** The retro offers to remove ticket and integration worktrees and branches whose PRs merged, listing them and asking first. It never force-removes and never deletes the run directory.
- **Leave a run.** Remove only your session's binding under `<project>/.kinetic/sessions/`. The run directory stays for reference.
- **A hook denied something.** A denial is final for that action. Read the reason: an isolation denial names the allowed roots; a push denial means the target is a protected branch.
- **The run is stalled.** The frontier is empty with tickets still `todo`. kinetic shows the waiting edges; usually a `snapshot` edge between repos without `publish_snapshot`/`pin`, or a `release` edge nobody has marked. Mark a release with `kinetic state set-ticket --run <run_dir> --id <upstream-id> --field status=released`.
- **Doctor checks:** `git` and `gh` on PATH, `gh auth status`, readable project config, a resolvable verify command per repo, the optional companion skills and plugins, and a reminder to check the Atlassian MCP with `/mcp`.

**Upgrading an existing Claude Code install:** skills, agents and hooks now call `kinetic ...` instead of `PYTHONPATH=${CLAUDE_PLUGIN_ROOT}/scripts python3 -m kinetic.*`. After updating, run `<clone>/bin/kinetic install --harness claude` once; until then `doctor` warns and skills fall back to `python3 <plugin-root>/bin/kinetic`.

**OpenCode:** the plugin asks for confirmation on every edit or shell call when `python3` is missing or a kinetic hook crashes, rather than allowing it.

**Reviewer read-only check:** the hash covers tracked diffs and untracked files; gitignored files are not covered.

**Stale-verify guard:** on every harness (Claude included), `set-phase ship` and an approved ship gate are refused while a repo's verify is stale — this is intentional.

Run kinetic commands as `kinetic <module> ...` once installed (`kinetic root` prints the clone root). From a checkout without installing, `PYTHONPATH=<kinetic>/scripts python3 -m kinetic.<module> ...` still works.

## Code layout

```text
.claude-plugin/plugin.json   plugin manifest
skills/<name>/SKILL.md       commands and phase skills; skills/run/guides/ holds the
                             build loop, lanes, gates and recovery guides
agents/                      implementer, spec-reviewer, standards-reviewer,
                             fix-reviewer, integration-reviewer
hooks/                       hooks.json and one Python script per hook; _common.py
                             resolves the active run and its session scope
scripts/kinetic/             the deterministic core
tests/                       unittest suite, including a two-repo end-to-end fixture
docs/                        comparison, design spec, implementation plan, rulings
```

| Module | Responsibility |
|---|---|
| `state.py` | Run state: init, phase, slices, tickets, repos, gates, rulings, verify and edit stamps, owner session; flock-guarded atomic writes; CLI (whose `set-ticket` refuses `done` without a passing, current verify) |
| `graph.py` | Validates the ticket graph (missing repo except on human tickets, edge types, unknown blockers, unused slices, cycles, consumers without an edge to their producer, unknown or contradicting `after`/`related`, lone `ships_with` groups; unproduced interfaces as warnings) and gives a topological order that honours `after` |
| `frontier.py` | Ready tickets (never human): satisfied blockers, the cap of 3 running, one per repo; `waiting_on_you` and `only_human_left` for human tickets |
| `gitops.py` | `worktree`, `merge` (ticket into integration), `merge-order` across repos (ships-with neighbours kept together), `ships-with` groups, `pin` downstream repos to a snapshot, `status-hash` and `assert-unchanged` (reviewer read-only check) |
| `verify.py` | Runs a repo's verify command, logs it and records the result in state |
| `config.py` | Resolves a repo's config from project config and `.kinetic.json` |
| `detect_repos.py` | Proposes a verify command from build markers (Maven, Gradle, npm, Go, Cargo, pyproject, Makefile) |
| `tracker.py` | Resolves the tracker adapter, its settings and role names, and the PR template path |
| `ticket_store.py` | The ticket store interface and the local ticket store (tickets in `state.json`) |
| `orbit_store.py` | The Orbit ticket store: tickets as Issues on an Orbit Board, staged tickets, publish, liveness and doctor rows |
| `board.py` | Local ticket headers, the `issues/README.md` board and the drift check |
| `doctor.py` | The health checks behind `/kinetic:doctor`, including tracker access and the PR template |
| `spec_lint.py` | Checks a spec has non-empty Threat model, Non-functional requirements and Acceptance criteria sections |
| `status.py` | Renders `/kinetic:status` |
| `next_step.py` | Computes the next step for `/kinetic:ask` and the SessionStart hook |

## Tests

```
PYTHONPATH=scripts python3 -m unittest discover -s tests -v
```

The suite covers every script module and every hook, plus an end-to-end test that drives the scripts through a two-repo run with real git repositories and a `merge` edge.

## Status and known limitations

kinetic is at 0.7.0 (see `.claude-plugin/plugin.json`). This release uses native Orbit contracts, workflows, links, hierarchy, role identities, review evidence and incremental reads. Existing runs have an explicit migration path; local ticket stores retain their behavior. Scripted temporary-Board integration checks cover publication, migration and reviews. Live harness acceptance remains a separate smoke test. `docs/plan/kinetic-v0-rulings.md` records the original design decisions.

Not yet verified in a live session:

- Devin CLI, Codex CLI and OpenCode: the installers and doctor checks exist, but the results tables in `docs/smoke/devin.md`, `codex.md` and `opencode.md` are empty. Claude Code is the harness that has been run;
- a full run with real agents on the native Orbit store (a scripted run through the public API passed, see `docs/smoke/orbit.md`);
- that `kinetic` is on PATH wherever skills and agents run it;
- that `skills: [tdd]` resolves to kinetic's own `tdd` skill;
- whether subagent hook payloads carry the parent session id (the cwd rule covers implementers either way);
- the Jira, GitHub and Linear adapters against the real services: they are untested live, including whether the installed `gh` supports sub-issues and "blocked by" and which relation tools the Linear MCP exposes (the adapters have fallbacks, but no test checks them).

Known limitations:

- A bare `git push` on a protected branch only asks; it is not denied.
- `gh api` calls (including merges) pass the write-ops hook, and base branches other than `main`, `master` and `Release*` are not protected.
- Isolation cannot tell implementers apart within the run directory.
- Until a session claims the run, or when a hook payload has no session id, the run-scoped hooks act on every session.
- Hook confirmation: git pushes and `gh pr create|merge|edit|ready` are hook-confirmed (as are Atlassian MCP writes); other `gh pr` writes (close, comment, review), `gh issue`, `gh label` and `gh api` tracker writes, and other MCP tracker writes such as Linear, are covered only by the grant.
- No skill evals yet; `/kinetic:status` shows no PR health or Jira transitions.

## License

Some skills are adapted from third-party work (Matt Pocock's skills, MIT); see `THIRD_PARTY_LICENSES.md`.
