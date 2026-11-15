---
name: bare-clone
description: Make bare clones in the project's bare-repos folder, from ORG/NAME on GitHub or from a URL plus a name. Reports each clone's path and default branch.
model: sonnet
disable-model-invocation: true
triggers: [user]
argument-hint: "<org>/<name> ... | <url> <name>"
---

# Kinetic bare-clone

Clone repos as bare clones into the project's `bare-repos/`. Run scripts as `kinetic <module> ...`.

## 1. Check the project

Run `kinetic project find`. If it exits 2, there is no kinetic project here: tell the user to run `/kinetic:start` first, and stop.

## 2. Clone

For each argument:

- `<org>/<name>`: `kinetic bare clone --repo <org>/<name>`. The clone is named `<name>`.
- `<url> <name>` (a URL and the repo name to give it, without `.git`): `kinetic bare clone --url <url> --name <name>`.

Do the arguments one at a time. A failure (the name already exists, the clone fails) is reported and does not stop the others. A failed clone leaves nothing behind, so it can be retried.

## 3. Report

For each argument, the clone's `path` and `default_branch`, or the error. The clones are bare: they hold no working files. Worktrees for a run are cut from them later by `discover`.
