---
name: start
description: Start kinetic from a conversation. Inside a kinetic project it hands off to the run skill. Elsewhere it talks the idea through, and once the talk has a goal, the systems involved and references, offers once to turn it into a project folder (repos, config and runs inside), creates it on the user's yes with the first run's notes, and sends the user to a new session inside the project to run it. Use when the user brings an idea, a feature or a ticket to build with kinetic.
model: sonnet
argument-hint: "[idea|ticket-ref]"
---

# Kinetic start

Turn a conversation into a kinetic project, or hand off to the run skill when there is one already. Run scripts as `kinetic <module> ...`; `kinetic root` prints the clone root that skill files are named relative to. Ask the user with the harness's question tool when the profile names one (`kinetic harness --harness <id>` prints it under `Question tool:`; `none` means chat). `<id>` is the id on the `kinetic harness:` line of your session context; leave `--harness <id>` out when there is no such line.

## 1. Mark this conversation

Run `kinetic project start-session` first, before anything else, wherever you are. It prints `{"ok": true}`; outside a project the hooks then mark this conversation as kinetic's, so other process skills ask before they take it over (inside a project it does nothing).

## 2. Already in a project?

Run `kinetic project find`.

- Exit 0: it prints `{"project": <project>}`. Say "This folder is in the kinetic project <project>." Never offer to make a project here. Read and follow `<clone>/skills/run/SKILL.md` with the user's idea or ticket as the ref, and stop following this skill.
- Exit 2: there is no project here. Continue.

## 3. Talk (write nothing)

Talk the idea through with the user. Keep what you learn in this conversation only: write no file, no folder and no config, and run no `kinetic` command that writes. Track three things as they come up:

- **Goal**: what the work should achieve.
- **Systems**: the repos, services or products involved, by name.
- **References**: links, documents, tickets and files the user points at.

## 4. Offer once

When the conversation has a goal, the systems involved and the references, ask exactly once:

"Turn this into a project?"

- No: keep talking. Ask again only when the conversation changes materially (a new goal, new systems).
- Yes: continue with step 5. Nothing before this point writes to disk.

## 5. Create the project (only after the yes)

1. **Name and folder.** Propose a short kebab-case name from the conversation. The default folder is `<cwd>/<name>`. When `git rev-parse --is-inside-work-tree` prints `true` in the current directory, a project there is refused: propose `<parent of the repo's top level>/<name>` instead (`git rev-parse --show-toplevel` gives the top level). Ask the user to confirm or change the name and the parent folder, in one question.
2. **Create.** `kinetic project create --parent <parent> --name <name>`. It prints `{"project": <project>}` and makes `<project>/.kinetic/`, `<project>/bare-repos/` and `<project>/runs/`. Exit 1 (inside a git work tree, or the folder exists and is not empty): show the error and go back to 1.
3. **First run.** This session stays outside the project, so run every later `kinetic` command as `cd <project> && kinetic ...` in one shell call. The recommended run-id is the ticket reference when the conversation started from one, else a kebab slug of the idea plus the date (`<slug>-YYYY-MM-DD`). Ask the run-id question, with the harness's question tool when the profile names one, else in chat:

   ```
   Run id?
   1. <recommended id>   (recommended)
   2. Another id: type it
   ```

   Check a typed id with `cd <project> && kinetic project run-dir --id <id>`: exit 1 (not a single safe folder name) or `"exists": true` (that run folder exists already) asks the question again with the reason. The run dir is the `run_dir` that `cd <project> && kinetic project run-dir --id <run-id>` prints for the chosen id.
4. **Notes.** Write `<run_dir>/docs/notes.md`: raw notes from the conversation, not a transcript and not a spec. Sections: Goal, Systems (each repo or service named, with what the user said about it), Decisions so far, Open questions, References. List every link under References as a link.
5. **References.** Copy each file the user pointed at into `<run_dir>/docs/refs/`, keeping its file name, and list it under References in `notes.md` with its original path.
6. **Hand over.** Stop here; do not start the run in this session (it was opened outside the project, so the run would not be bound to it and the hooks would not guard it). In one short message, tell the user where the project is and to open a new session inside `<project>` (or `cd <project>` in their terminal and start the agent there) and run `/kinetic:run`: it finds this run and starts it at the grill, from `notes.md`.

## Rules

- Never write anything before the user says yes to the project.
- Never run `kinetic project create` inside a folder that is already a project, or inside a git work tree.
- Never edit files under `<project>/.kinetic/` with the edit tools: project config changes only through `kinetic project update` and `kinetic discover register`.
- Ask no tracker, repo-path or verify question here: the discover phase settles the repos and their commands, and the decompose phase the tracker.
