# Kinetic: a project workspace folder, created from a conversation

Status: draft for approval. Builds on [kinetic-bare-repos-design.md](kinetic-bare-repos-design.md) and [kinetic-repo-discovery-design.md](kinetic-repo-discovery-design.md). Brainstormed 2026-11-14. "Decided by the user" holds only what the user said; "My calls" holds what I chose and the user has not yet confirmed.

## Why

Today kinetic keeps everything in two global places: `~/.config/kinetic/` (repo registry, verify commands, tracker, settings, the active-run pointer) and `<workspace_root>/<run-id>/` (default `~/workspace/kinetic-runs`). `/kinetic:init` is the required first step and writes the global config. Consequences:

- Nothing about one product lives with that product. Two projects share one tracker setting, one repo registry and one active-run pointer.
- A run's files sit far from the repos it changes.
- `init` asks questions (tracker, verify commands) before the user knows what the work is. The scope of the work, which repos and how many, is only settled later, by the grill and `discover`.

The user wants the opposite order: talk first, and when the talk has taken the shape of a project, give that project its own folder, with its repos, its config and its runs inside.

## Goals

1. **One folder per project**, monorepo or multirepo. The folder is not a git repo. Everything kinetic keeps about the project lives inside it.
2. **The folder is created from a conversation.** Kinetic notices when a conversation has become a project, offers to make it one, asks where to store it, and creates the structure.
3. **Repos are bare clones inside the project**, and every worktree of every run is cut from them.
4. **A run is one folder** holding its documents and its worktrees.
5. **Inside a project, the offer never appears.** A session started anywhere under a project folder just works.
6. **Worktrees are the default** for monorepo and multirepo alike.
7. **A new repo is a supported case**: kinetic asks whether to create it on GitHub or to be given its URL.

## Non-goals

- One run spanning several projects. The user dropped that scenario.
- A repo that exists only on the user's machine, with no remote. Not offered in the first version.
- Replacing `~/.config/kinetic/` entirely. Machine-level data stays there (see My calls).
- Sparse checkout for monorepos. Add it only if disk or time becomes a real problem.
- Making the tracker selectable per repo. One tracker per project.

## Anti-goals

- Writing anything to disk before the user confirms the project.
- Asking "make this a project?" inside a folder that already is one.
- A special case for the new repo that breaks `refresh`, `ship` or PRs because there is no `origin`.
- Kinetic creating a GitHub repo without the user confirming owner, name and visibility.
- Two projects sharing state by accident (one active-run pointer, one registry).

## Decided by the user

- A **project** is a folder that is not a repository. It holds a hidden `.kinetic/` (configuration, definitions), a folder of **bare repositories**, and a folder of **runs**.
- A project is one monorepo or one multirepo product. The multi-project demand is dropped.
- A project has **one tracker** (Orbit, for example). A run is a demand and may or may not have a ticket; that ticket may or may not be in the tracker. A run can generate tasks for the tracker, and kinetic can also execute a task that already is in the tracker.
- Each **run** is a folder holding all information about that run and the worktrees of the repos it involves. Those worktrees derive from the bare repos in the project folder.
- Worktrees are the default for both monorepo and multirepo.
- The flow starts as a conversation in which the user gives ideas and references resources. Kinetic watches for when the conversation has become enough of a project, and asks whether to turn it into one.
- On yes, kinetic asks where to store the project, then creates the folder.
- The question appears **only** when the session runs in a folder that has no kinetic project structure.
- Repos are cloned from GitHub. For a new repo, kinetic asks the user what they want: create it on GitHub now, or give the URL of one already created. A local-only option is not offered in the first version.
- Run ids: the ticket reference, or a slug of the idea plus the date (as today).
- Language-agnostic: each repo declares its own commands (as today).

## My calls

Marked for the user to confirm or change.

- **Names.** `bare-repos/` and `runs/` inside the project folder; `.kinetic/` for the project's own files.
- **The project is found by walking up** from the current directory to the nearest `.kinetic/`. A worktree under `runs/<id>/worktrees/` finds its project the same way, with no configuration.
- **The conversation is held in context until confirmed**, then written as the start of the project's **first run**: its notes become the run's `docs/`, its references are recorded in the run. The run's grill continues from there. No temporary draft on disk.
- **`/kinetic:init` stops being the first step.** What it still owns is machine-level data in `~/.config/kinetic/`: the active harness, the path of the kinetic clone, the install manifest. Project data (repos, verify commands, tracker, names, PR template, workspace location) moves to `<project>/.kinetic/`.
- **Run internals split in two.** Documents the user reads (`spec.md`, `discovery.md`, `issues/`, `reviews/`, `docs/`, `needs-human/`) sit at the top of the run folder; kinetic's bookkeeping (state, rulings, implementer reports, staged tickets) sits in `<run>/.kinetic/`.
- **Worktree layout inside a run:** `worktrees/<repo>/` (integration) and `worktrees/<repo>-<ticket>/` (one per ticket), as today.
- **Monorepo is multirepo with one repo.** There is no "package" unit. Slices stay vertical; a monorepo run has one integration branch and one PR. Package-sized batches appear only for a wide mechanical refactor, as expand then migrate then contract, the way the Matt Pocock skills do it.
- **Verify has two levels** for large repos: an optional focused command per ticket and the full command on the integration branch.
- **An optional per-repo `setup` command** (install dependencies, copy `.env`), run right after a worktree is cut. Worktrees are bare of everything git does not track, and today nothing prepares them.
- **A run started from an existing tracker task** makes its generated tickets SubTasks of that task. Whether the run claims the original task, and who closes it, is not decided (see Left to the builder).
- **Publishing to the tracker is chosen per run.** Without it, tickets stay as files in the run folder.
- **New repo, option "I already created it":** the same path as an existing repo, a bare clone from the URL. Option "create it": `gh repo create` with the user's confirmed owner, name and visibility and an initial commit (a README), so the base branch exists; then the same bare clone.

Settled after the builder check (all my calls, for the user to confirm):

- **Active run.** The run is inferred from the current directory under `runs/<id>/` (the run folder or any worktree inside it). There is no pointer file. From the project root or a bare repo there is no run: a command that needs one lists the project's open runs and asks which. Two runs at once therefore work.
- **The first run is written from the conversation as raw notes plus references**, not a verbatim transcript and not a finished spec. The notes go to `docs/`, links are recorded as links, and files the user pointed at are copied into `docs/refs/`. The grill then runs on those notes and writes `spec.md`.
- **Config precedence.** `<project>/.kinetic/config.json` overrides `~/.config/kinetic/` key by key; global values are defaults. Project keys: `repos` (path, verify, setup, publish_snapshot, pin, base_branch), `tracker`, `tracker_settings`, `tracker_names`, `tracker_description`, `pr_template`, `catalog_dir` use. Machine keys, global only: `harness`, kinetic clone path, install manifest, `repo_roots`, `github_orgs`, `catalog_dir`. `workspace_root` and `bare_dir` are dropped inside a project (the project folder replaces them). Outside any project only `init` and the intake skill run; every other command says "no project here" and points to the intake. `.kinetic/config.json` carries a `version`; an unknown newer one is rejected.
- **Orbit Board.** `tracker_settings.orbit_dir` is set to the project folder, which the Orbit adapter already accepts as "the Board anchor or Board folder". The builder verifies that a Board can live outside a repo; if it cannot, the Board is created inside `<project>/.kinetic/board/`.
- **The catalog stays machine-level.** A repo is always cloned fresh from its remote into the project's `bare-repos/`, never referenced from another place, so the project is self-contained.
- **Project folder location.** The name is proposed from the conversation and confirmed by the user. If the current directory is inside a git repo, kinetic refuses to create the project there and proposes a sibling folder.
- **`gh` missing or not authenticated:** the "create on GitHub" option is withdrawn and kinetic asks for a URL.
- **A failing `setup` command** blocks that ticket and reports the failure; it does not silently continue.

## Approaches considered

1. **Project folder with bare repos, runs inside (chosen).** Everything about a project in one place; the worktrees' source is next to them. Costs a migration of the five modules that hard-code `~/.config/kinetic/`.
2. **Keep the global config, add only a runs folder per project.** Smallest change, but the registry, tracker and pointer stay shared between projects, which is the problem.
3. **Config committed inside the repo, as the Matt Pocock skills do.** Shareable with the team and natural for a monorepo. It does not fit a multirepo, where no repo owns the project, and the project folder is not a repo. Not chosen; the question of sharing `.kinetic/` with a team is left open.

## Design

### Layout

```
<project>/
  .kinetic/                     project config: repos, verify, setup, tracker, names, pr_template
  bare-repos/
    <repo>.git/
  runs/
    <run-id>/
      spec.md  discovery.md  issues/  reviews/  docs/  needs-human/
      worktrees/
        <repo>/                 integration worktree
        <repo>-<ticket>/        ticket worktree
      .kinetic/                 run state, rulings, reports
```

### Entry flow

1. Every command and the intake skill resolve the project by walking up from the current directory to `.kinetic/`.
2. **Found:** work continues in that project. No project question.
3. **Not found:** the intake skill (new) converses freely. When the conversation has a goal, named repos or systems, and referenced resources, it asks once: "turn this into a project?" Declined: it asks again only if the conversation changes materially.
4. **Confirmed:** ask where to store the project (default: a folder named after it under the current directory; the user may name another). Create `.kinetic/`, `bare-repos/` and `runs/`, write the first run from the conversation.
5. **Repos:** for each repo the conversation names, `discover` confirms the list with the user, then clones bare into `bare-repos/` (reusing `kinetic bare clone`). Unknown repo: ask "create on GitHub, or give me the URL?". Verify (and setup) commands are asked here, per repo, as today.
6. **Tracker:** asked when the run reaches decompose, not at creation. Default `local`.

### What changes in the code (outline)

- The five modules with a hard-coded settings path (`config`, `discover`, `tracker`, `catalog`, `harness`) learn to read the project's `.kinetic/` first. Machine-level keys stay global.
- `bare` operates on the project's `bare-repos/`; its saved `bare_dir` setting is superseded inside a project.
- `skills/run` stops reading `workspace_root` and computes `<project>/runs/<run-id>/`.
- A new intake skill; `init` is reduced.
- Hooks and the active-run pointer find the project by walking up (see Left to the builder for the pointer).

### Testing

Temp project folders and local bare repos as remotes, the way `test_bare.py` does it: project resolution by walking up (from the project root, from a run folder, from a worktree, from outside any project), creation of the structure, bare clone into `bare-repos/`, worktree cut from it, the new-repo paths (URL given; `gh` stubbed for create), and `skill_text` checks that the intake skill never writes before confirmation.

## Left to the builder

- **No backward compatibility (decided by the user).** No project uses kinetic yet, so there is no legacy mode and no migration. Reads of `~/.config/kinetic/repos.json`, the project-level keys of `~/.config/kinetic/settings.json`, `workspace_root`, `bare_dir` and the global `active-run` pointer are removed, along with their tests. `~/.config/kinetic/` keeps only the machine-level keys listed above.
- Whether the run claims a task that already exists in the tracker, and who closes it. The Orbit adapter today says kinetic never claims its own tickets.
- Detection heuristics for "this conversation has become a project".
- Whether a repo with a single ticket still gets an integration worktree (it adds nothing).
- Sharing `.kinetic/` with a team (committing it) is out of scope for now.
