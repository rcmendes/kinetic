---
name: run
description: Drive a kinetic run end to end. Picks the lane, enforces the approval gates, runs the build loop and sequences the phase skills (grill, discover, specify, decompose, tdd, ship, retro). The only orchestrator.
model: sonnet
disable-model-invocation: true
triggers: [user]
argument-hint: "<ticket-ref|idea|spec-path> [--from build]"
---

# Kinetic run

You are the orchestrator. Only you set phases, record gates and dispatch agents. Phase skills are loaded by reading their file and following it: "read and follow `<clone>/skills/<name>/SKILL.md`" where `<clone>` is the path `kinetic root` prints, passing `Run: <run_dir>`. These phase skills run inline on your model; `model:` in their frontmatter is not applied.

Run kinetic commands as `kinetic <module> ...` (`kinetic` is on PATH; `kinetic root` prints the clone root for reading files inside it). If `kinetic` is not found — a plugin update without `kinetic install` — use `python3 <plugin-root>/bin/kinetic` in its place (`<plugin-root>` is the directory that holds this skill's `skills/` folder), pass the same fallback to agents in their briefs, and tell the user to run `<plugin-root>/bin/kinetic install --harness claude`.

Clone root: run `kinetic root` once to get the clone's absolute path. Skill, guide, agent and tracker-adapter files are named relative to it (`skills/<name>/SKILL.md`, `skills/run/guides/`, `trackers/<name>.md`, `templates/`); substitute that path as you read them. Agents run the same `kinetic <module>` commands — `kinetic` is on their PATH too. Never edit `<run_dir>/.kinetic/state.json` by hand and never read code or full diffs yourself: scripts emit JSON, agents write reports, you read report headers.

Guides (read each at the point named, under `<clone>/skills/run/guides/`):

- `lanes.md`: pick the lane and the entry phase.
- `gates.md`: the three approval gates.
- `build-loop.md`: the build loop and the review phase.
- `recovery.md`: resuming a run.

## 1. Preflight

1. Run `kinetic harness` and tell the user which guarantees it lists as Instructed on the active harness — those hold only through skill text, so the user knows what to watch.
2. Run `kinetic project find`. Exit 2 means there is no kinetic project here: tell the user to run `/kinetic:start` and stop. Otherwise it prints `{"project": <project>}`; the project holds `.kinetic/config.json` (repos, tracker), `bare-repos/` and `runs/`. Never edit files under `<project>/.kinetic/` with the edit tools: project config changes only through `kinetic project update` and `kinetic discover register`.
3. Run `kinetic doctor`; inside a project it checks every repo in the project config by itself. Show warns with their fix (missing reused skills have fallbacks, see `build-loop.md`). Stop on any fail. A new project has no repos yet; the discover phase adds them.
4. Resolve the tracker once with `kinetic tracker resolve` and read the adapter file it names; every tracker operation in a run follows that adapter. With no tracker in the project config it resolves to local; the decompose phase may set one, and then you follow the adapter it names. A run whose `rulings` hold `tracker: local for this run` (decompose records it when the run does not publish) uses `trackers/local.md` everywhere from then on: every `Tracker adapter:` line in an agent brief, every tracker operation in the build loop, review and ship, and every phase skill you follow names `<clone>/trackers/local.md` instead of the adapter `kinetic tracker resolve` prints. Tell each phase skill so when you pass it `Run:`.
5. Find a run to resume. `kinetic project current-run` prints `{"run_dir": ...}`: a run when this session is in a run folder or one of its worktrees, else null. When it is null and the current directory is the project root (or a bare clone), `kinetic project runs` lists the open runs (`run_id`, `run_dir`, `phase`); when there are any, show them and ask whether to resume one or start a new run. A run to resume: read `guides/recovery.md` and follow it instead of initialising. Never pass `--force` to `state init` unless the user explicitly asks to restart.
6. A run folder under `<project>/runs/` that holds `docs/notes.md` but no `.kinetic/state.json` is the one `/kinetic:start` made. When exactly one exists (or the current directory is inside it), use it: its folder name is the run-id, and you initialise it in section 2 with the grill as the entry phase. With several, list them and ask which.
7. Otherwise pick the run-id. A ticket reference is the run-id; do not ask. For an idea or a spec path you create a new run. The recommended id is a kebab slug of the idea plus the date (`<slug>-YYYY-MM-DD`); for a spec path, slug its file name. Before you ask, run `kinetic project run-dir --id <recommended>`; it prints `{"run_dir", "exists", "phase"}`, where `"phase"` is the run's recorded phase, or null when the folder has no state. When `"phase"` is a phase other than `done`, resume that run through `guides/recovery.md`, without asking. When the folder exists and `"phase"` is null or `done`, recommend the id with the next free numeric suffix (`-2`, `-3`, ...) instead. Then ask the run-id question, with the harness's question tool when the profile names one, else in chat.

   ```
   Run id?
   1. <recommended id>   (recommended)
   2. Another id: type it
   ```

   Check a typed id with `kinetic project run-dir --id <id>`: exit 1 (not a single safe folder name) or `"exists": true` (that run folder exists already) asks the question again with the reason. The run dir is the `run_dir` that `kinetic project run-dir --id <run-id>` prints (`<project>/runs/<run-id>`). A run dir that already holds `.kinetic/state.json` is an existing run: resume it through `guides/recovery.md`, without asking.

## 2. Start

Read `guides/lanes.md`, pick the lane and entry phase, then:

```
kinetic state init --run <run_dir> --lane <lane> --ref "<ref>" --ticket-store "$(kinetic tracker store-record)"
```

Bind this session to the run, always with exactly this command:

```
kinetic project use-run --run <run_dir>
```

The hooks recognise that command and claim the run for this session; never bind it any other way. A run started from `<run_dir>` or one of its worktrees is found from the current directory; the binding covers a session working from the project root.

## 3. Phases

Set each with `kinetic state set-phase --run <run_dir> --phase <phase>` just before starting it. Order: intake, grill, discover, specify, decompose, build, review, ship, retro, done. Skip the phases the lane skips (`lanes.md`).

| Phase | Do |
|---|---|
| grill | Read and follow `skills/grill/SKILL.md`. |
| discover | Read and follow `skills/discover/SKILL.md` with `Mode: full`, passing the candidate repos and where each came from (the ref text, the tracker issue, the registered repo the run was started from, the grill). Every lane runs it: it registers the repos, cuts their integration worktrees and sets the final lane. |
| specify | Read and follow `skills/specify/SKILL.md`, then run the spec gate (`gates.md`). |
| decompose | Read and follow `skills/decompose/SKILL.md`. If it needs a repo that is not in the run, run `skills/discover/SKILL.md` with `Mode: confirm-only` for that repo without changing the phase, then return to decompose. Then run the breakdown gate (`gates.md`). |
| build | `guides/build-loop.md`, build loop. |
| review | `guides/build-loop.md`, review phase. |
| ship | Read and follow `skills/ship/SKILL.md`, run the ship gate (`gates.md`) first. Offer `dev-fleet:release` only if the user asks and the repo is k8s-hosted. |
| retro | Read and follow `skills/retro/SKILL.md`. Then `set-phase done`. Ticket worktrees and branches were kept until now for recovery: read and follow `skills/cleanup/SKILL.md` with this run directory (it asks before removing anything). |

A rejected gate returns to the previous phase skill, never forward. Deploy is hook-confirmed; git pushes and `gh pr create|merge|edit|ready` are hook-confirmed (as are Atlassian MCP writes); other `gh pr` writes (close, comment, review), `gh issue`, `gh label` and `gh api` tracker writes, and other MCP tracker writes such as Linear, are covered only by the grant. Never try to work around a hook decision.

## Rules

- At most 3 implementers at once, at most 1 running per repo; `kinetic.frontier` enforces both.
- Human tickets are never dispatched: each round print `waiting_on_you`, and stop when `only_human_left` while agent tickets remain un-merged (see `build-loop.md`); once every agent ticket is merged the loop ends and open human tickets go on to review and ship.
- Never dispatch an agent from inside an agent; you are the only dispatcher.
- A reused skill that is missing: use the fallback named in the guide, or ask the user. Never fail silently.
- Record every user decision that changes scope with `kinetic state add-ruling --run <run_dir> --what ... --why ... --cost ...`.
