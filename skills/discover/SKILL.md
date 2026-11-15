---
name: discover
description: Settle which repos a kinetic run touches. Asks the user once where to look (base folders of checkouts, other sources, the GitHub org), searches them, confirms the list with the user, clones new repos into the project's bare-repos folder (or creates them on GitHub), registers them and cuts each repo's integration worktree. Use at the discover phase of a kinetic run.
model: sonnet
---

# Discover

Find the repos this run touches, get the user to confirm them, and make each one usable: a bare clone in `<project>/bare-repos/`, registered in the project config (`<project>/.kinetic/config.json`), with its integration worktree under the run directory. `<project>` is what `kinetic project find` prints. This skill never invokes another skill.

If the orchestrator passed no `Run:`, run `kinetic next_step`, tell the user its `command` and `reason` in one line, and stop.

The orchestrator passes:

- `Run: <run_dir>` (the run directory, `<project>/runs/<run-id>/`) and the ref;
- `Candidates:` the repos known so far, each with where it came from (the ref text, the tracker issue, the repo `/kinetic:run` was started from, the grill), or none;
- `Spec:` the spec location, if there is one;
- `Mode: full` or `Mode: confirm-only` (tickets already in hand, or a repo added later by the decompose phase).

Run scripts as `kinetic <module> ...`. Ask the user with the harness's question tool when the profile names one (`kinetic harness` prints it under `Question tool:`; `none` means chat).

Each step below is either **agent** (you work alone and ask nothing) or **you** (the user answers). On a **you** step, never answer for the user, never guess their answer, and ask every question of that step in one message.

Never write `<project>/.kinetic/` or `~/.config/kinetic/` files with the edit tools: the isolation hook denies them during a run. Config changes go only through `kinetic discover remember` (machine-level `repo_roots` and `github_orgs`) and `kinetic discover register` (the project's repos). The network calls are the step 4 GitHub calls, made with the user's permission; fetching the project's own bare clones that match a candidate, which needs no permission; and the clones and GitHub creates of step 7, made only on the user's yes. Reading any other source, the catalog included, sends nothing off the machine.

## Mode: confirm-only

Skip steps 2 to 6 and run step 7 on the candidates. A candidate known only by name that is not under `repos` in the project config: match it against `kinetic bare list` (the project's bare clones), then run `kinetic discover settings` and `kinetic discover scan --root <each repo_root>`, and match by directory name, then by the repo name at the end of the remote URL. A repo found both in a base folder and in `bare-repos/` counts as one match: the bare clone. A match in a base folder only is cloned from its remote in step 7.1; a checkout is never registered in place. One match: use it. None or several: ask the user for the GitHub `<org>/<name>` or URL, in the same message as any verify question from step 7; a repo that does not exist yet goes to step 7.2. Before step 7's worktrees, refresh every confirmed repo (step 7.5).

## Mode: full

### 1. Is the set already clear? (agent)

The set is clear when every candidate is registered in the project config, came from a known place (named in the ref text, named by the tracker issue, the registered repo the run was started from, or confirmed by the user in the grill), and nothing in the grill notes (`<run_dir>/docs/`) or the spec names a component that no candidate owns.

Clear: print one line, for example `Repos: api, web (from the grill)`, and go to step 7. Not clear: continue.

### 2. Look for base folders on your own (agent)

- `kinetic discover settings` prints the saved `repo_roots` and `github_orgs`.
- `kinetic bare list` lists the project's bare clones in `<project>/bare-repos/` with name, path, remote and `default_branch`. Step 3 never asks about them.
- Read the instruction files the harness loads (the user-level and project-level `CLAUDE.md` and `AGENTS.md`) and the README and `AGENTS.md` of each candidate repo. Look for a folder where the user keeps checkouts, and for a GitHub organization name. Note where you found each.

### 3. Ask once (you)

One message, all three questions:

1. **Base folders.** List the saved ones (pre-selected) and the ones found in step 2, each with where it was found. Ask which to use and whether there is another folder where they keep checkouts.
2. **Other sources.** "Is there anything else that shows which repos this touches, such as a service catalog, an architecture doc, a wiki page or a CODEOWNERS file?" Free text; empty is fine.
3. **GitHub.** "May I search the GitHub org(s) <saved or found>?" with the choices yes, no, or another org. When `gh auth status` fails, skip this question and say in one line that GitHub search is off until `gh` is logged in.

Save the approved folders and orgs right away: `kinetic discover remember --root <path> ... --org <org> ...`.

### 4. Search (agent)

Search every approved source, asking nothing:

- **Catalog.** Only when `kinetic catalog show` exits 0 (it prints the catalog folder). Local files: no permission needed, nothing leaves the machine. Read `<catalog>/index.md` and match the run's scope (the ref, grill notes, spec, glossary terms) against each recipe's when-to-use text and each repo's terms; open the matching `recipes/<slug>.md` files. A recipe repo marked **always** is strong, **usually** is medium, **sometimes** or **seen once** is weak; a repo matched only by its terms is medium at most. The reason line names the recipe, e.g. `recipe onboard-asset-class-warehouse-pledge: always (3 of 3)`. Keep each approved repo's what-for lines for `discovery.md`. A catalog match never skips step 6, and it never makes step 1's set clear on its own.
- **Bare clones.** The project's clones from `kinetic bare list`. Match them like base folders, reading the README with `git -C <clone> show origin/<default_branch>:README.md`. For every candidate that matches a bare clone, run `kinetic bare refresh --name <n> ...` (repeat `--name`) before reading its contents; with no matching names skip the command, and never run it without `--name`, which fetches every clone in the folder. Ask nothing: the clones belong to the project. The command prints `{"results": [{name, ok, advanced, error}]}` and exits 1 when any fetch failed. A failure does not stop this step; keep its `error` for step 6. A repo found both in a base folder and in `bare-repos/` uses the bare clone.
- **Base folders.** `kinetic discover scan --root <path> ...` lists the git repos one level deep, with name, path, remote and whether each is registered. Match them against the ref, the grill notes, the glossary terms and the interfaces named so far, using the repo name, the first lines of its README and its remote.
- **Other sources.** Read what the user named (files, or URLs they gave, with the harness's fetch tool) and pull out repo names.
- **GitHub.** `gh repo list <org> --json name,description,repositoryTopics,url --limit 1000` for names and descriptions. For each interface the grill or spec names (an endpoint, a client method, an event, a schema), `gh search code --owner <org> "<interface name>"`: a hit is the strongest sign that a repo produces or consumes it. Search at most 10 interfaces, the ones the run produces or consumes first.

Give every candidate a confidence and a one-line reason with its source:

- **strong**: named by the user, the ticket or the spec; or a code hit on an interface the run produces or consumes;
- **medium**: its name, description or topics match the run's domain terms;
- **weak**: a keyword match only.

### 5. Is it enough? (agent, then maybe you)

The result is not enough when any of these holds:

- there is no strong candidate;
- an interface the grill or spec names has no repo that produces it, or none that consumes it;
- a source failed (not authenticated, unreachable, unreadable).

Then ask one follow-up message: what you found, why it is not enough, and what would help. Example: "I found terramigo-api, but nothing that serves the web app. Is there another org or folder?" Save any new folder or org with `kinetic discover remember` and go back to step 4 with the new sources.

At most two follow-up rounds. After the second, go to step 6 with what you have and say what is still missing.

### 6. Confirm (you)

One message, in two groups:

- **Candidates:** name, reason, source, confidence, and where it is: `registered`, `bare clone, not registered`, `bare clone, not refreshed: <error>` (the refresh failed; its contents may be out of date), `on disk at <path>, not in the project`, `GitHub only, not cloned`, or `not found` (a new repo, or one the user must name).
- **Suspected, unconfirmed:** the weak matches, for the user to add or ignore.

Always name a repo; never show only a path or URL. The user edits and approves the list. Record the approval as a ruling:

```
kinetic state add-ruling --run <run_dir> --what "repos: <names>" --why "<one reason per repo>" --cost "a missing repo is found at tickets and costs a confirm-only pass; an extra repo costs an unused worktree"
```

## 7. Make every repo usable (agent; asks only when needed)

Ask the questions of this step together, in one message, and only those that apply.

1. **Not in the project yet** (GitHub only, or on disk at a checkout). One question for all of them: "Clone them into the project's bare-repos?" (recommended) or "I'll clone them myself". On yes: `kinetic bare clone --repo <org>/<name>` for a GitHub repo, and `kinetic bare clone --url <its origin URL> --name <name>` for a checkout (`git -C <checkout> remote get-url origin`; a checkout with no remote is not supported, so ask for the URL). On "I'll clone them": print those commands, wait for the user to say they are done, then recheck with `kinetic bare list`. Repo names never carry `.git`.
2. **Not found.** For each repo that does not exist yet or that the user could not place, ask: "Create it on GitHub, or give me the URL of one already created?" Offer create only when `kinetic bare gh-ready` prints `{"ready": true}`; otherwise ask only for the URL and say that `gh` is missing or not logged in. URL: `kinetic bare clone --url <url> --name <name>`. Create: confirm the owner, the name and the visibility (`private`, `internal` or `public`; recommend `private`) in one question, and only on the user's yes run `kinetic bare create --owner <owner> --name <name> --visibility <visibility>`. It creates the repo with a README, so the base branch exists, then clones it into `bare-repos/`.
3. **Bare clone, not registered.** `kinetic detect_repos <clone path> ...` proposes each verify command. In one message ask, per repo: the verify command when it is null (confirm it when it looks unusual); an optional focused verify for one ticket, for a repo whose full verify is slow; an optional setup command run in every new worktree (install dependencies, copy `.env`); and, only for a repo that publishes snapshot builds other repos consume, the `publish_snapshot` command and the downstream `pin` command (it reads `KINETIC_UPSTREAM_REF`). Never guess a command. Then, for each: `kinetic discover register --name <name> --path <clone path> --verify "<cmd>"`, adding `--verify-ticket "<cmd>"`, `--setup "<cmd>"`, `--publish-snapshot "<cmd>"` and `--pin "<cmd>"` only when given. A repo is always registered at its bare clone. To change a registered repo's commands later, run `kinetic discover register --name N --path P [--verify ...] [--setup ...] [--verify-ticket ...]` with its registered name and path: it updates only the commands given and keeps the rest of the entry. Never edit `repos` with `kinetic project update`, which replaces the whole object.
4. **Lane.** Mode full only, and only when the lane is not `quick`: one repo is `feature`, two or more is `epic`: `kinetic state set-lane --run <run_dir> --lane <lane>`.
5. **Worktrees.** Run `kinetic bare list` for each clone's path and `default_branch`. First, refresh every confirmed repo that was not refreshed earlier in this run of the skill: `kinetic bare refresh --name <n> ...` (skip it when there are none; never run it without `--name`), asking nothing; a failure does not stop the step and is reported with the result. Then, for each repo not yet in `state.repos` (read it with `kinetic state load --run <run_dir>`): its base branch is `base_branch` in its project config entry, else the clone's `default_branch`, never the literal `main`. Then:
   - `kinetic gitops worktree --repo <clone path> --dest <run_dir>/worktrees/<repo> --branch kinetic/<run-id>/integration --base <base>`
   - `kinetic gitops setup --run <run_dir> --repo-name <repo> --path <run_dir>/worktrees/<repo>`: it runs the repo's `setup` command, if any. Exit 1 means setup failed: show the user the `log_path` from its JSON and ask how to go on; never continue past it silently.
   - `kinetic state set-repo --run <run_dir> --name <repo> --path <run_dir>/worktrees/<repo> --integration-branch kinetic/<run-id>/integration`

   A repo already in state keeps its worktree. The integration branch is never just `kinetic/<run-id>`: git cannot hold both that and `kinetic/<run-id>/<id>`.
6. **Record.** Write or update `<run_dir>/discovery.md` with:
   - the sources used, and the ones the user declined;
   - the approved repos with their reasons and sources, and, for a repo a catalog recipe matched, that recipe's what-for lines;
   - the suspected repos the user did not take;
   - each follow-up round: what was missing and what the user added.

   The specify, decompose and ship phases read this file.

## Rules

- Never add a repo to the run that the user has not seen with its reason.
- Never clone, search GitHub, or read a source the user did not approve. Fetching the project's own bare clone that matches a candidate needs no approval.
- Never create a GitHub repo without the user's yes on its owner, name and visibility.
- One message per **you** step; never one question per source or per repo.
- Never loop past two follow-up rounds.
