# Kinetic: keeping a conversation with kinetic, end to end

Status: draft for approval. Builds on [kinetic-project-workspace-design.md](kinetic-project-workspace-design.md). Brainstormed 2026-11-15. "Decided by the user" holds only what the user said; "My calls" holds what I chose and the user has not yet confirmed.

## Why

A user who wants kinetic to drive a conversation has other plugins installed (Superpowers, the Matt Pocock skills) whose skills also claim that conversation. Superpowers injects a mandate at session start to invoke one of its skills before any reply, so "let's build X" is taken by its brainstorming skill before kinetic is considered. Today kinetic has nothing that answers this:

- Its SessionStart hook (`orient`) injects a few lines only, on purpose ([kinetic-sdlc-pipeline-design.md](kinetic-sdlc-pipeline-design.md), section 7).
- No kinetic hook looks at skill invocations.
- A project folder carries no instruction file.
- A phase skill started on its own (a user typing `/kinetic:grill` with no run) has no run folder to write to and says nothing useful about it.

A skill cannot fix this. A skill is one more candidate the model may pick, on equal footing with the others, and its text cannot stop another skill from being called.

## Goals

1. **Inside a project, the harness is told kinetic owns the workflow**, on every harness, through the instruction files the harness already reads.
2. **During a run, competing process skills are refused** by a hook, where the harness lets a hook see skill invocations.
3. **A conversation opened with `/kinetic:start` stays with kinetic** for the whole session, before any project exists, and also when the user never turns it into a project.
4. **A phase skill started in the wrong place tells the user which command to use**: `/kinetic:start` outside a project, `/kinetic:run` inside one.
5. **The user chooses the run id.** `start` shows the id it recommends and lets the user type another.

## Non-goals

- Owning a session the user did not start with kinetic and that is outside every project. Nothing marks it as kinetic's.
- Stopping what another plugin injects at session start. A hook cannot block another plugin's hook.
- Detecting `/kinetic:start` from the raw prompt (a UserPromptSubmit hook). The mark is written by the skill's first step instead; see My calls.
- A new `/kinetic:chat` skill. `/kinetic:start` is the conversation entry.

## Anti-goals

- A session where the user is locked out of a skill they typed themselves, with no way through.
- Refusing a skill that kinetic's own skill text names (`superpowers:receiving-code-review`).
- Copying the "invoke a skill at 1% chance" mandate into kinetic's own injected text.
- Writing an instruction file inside a repo or a worktree.
- A mark or binding file that is never removed.

## Decided by the user

- Do both: the instruction file in the project folder and the hook that refuses competing skills.
- The user can open a session by typing a kinetic skill followed by the prompt, and that session must stay with kinetic even if the user decides not to turn the conversation into a project.
- The entry skill keeps the name `start`. No `chat` skill.
- `grill` must guide a user who runs it outside the right layout or step toward the right command, using `start`.
- `start` shows a prompt with the recommended run id and a second option where the user defines it.

## My calls

- **The start mark is written by the skill's first step**, not by a prompt hook: `start` runs `kinetic project start-session`, and the existing post-shell hook (`claim_run`), which already recognises `kinetic project use-run`, writes the mark. This is the mechanism already proven for run bindings and it exists on all four harnesses. The command prints `{"ok": true}` and writes nothing itself.
- **The mark lives at machine level**, `~/.config/kinetic/sessions/<session_id>`, because no project exists yet. Inside a project the command is a no-op: `start` hands off to `run` there.
- **Ask in a marked conversation, deny in a run.** On Claude the hook only sees skills the model invokes; a slash command the user types bypasses it. The model may still invoke a skill because the user asked for it in plain words, so in a session that only carries the start mark the hook answers `ask` and the user can let it through. In a session working on a run it answers `deny`.
- **A deny list, not an allow list.** Only named process skills are refused; everything else, including the skills kinetic names, passes untouched. The default list ships in `hooks/skill_guard.json`. A project may replace it with `skill_guard.deny` in its config.
- **Default deny list:** `superpowers:brainstorming`, `superpowers:writing-plans`, `superpowers:executing-plans`, `superpowers:subagent-driven-development`, `superpowers:using-git-worktrees`, `superpowers:finishing-a-development-branch`, and the Matt Pocock process skills `to-spec`, `to-tickets`, `implement`, `implement-spec`, `wayfinder`, `grill-with-docs`, `grilling`, `grill-me`, `triage`, each also under its `mattpocock-skills:` prefix. Debugging, code review, research and writing skills are not on it.
- **The guard for phase skills goes on every phase skill, not only `grill`**: `grill`, `discover`, `specify`, `decompose`, `ship`, `retro`. They share the same problem. The answer comes from `kinetic next_step`, not from prose in each skill.
- **`run` asks the same run-id question** when it creates a new run inside a project, so the first run and later runs behave alike. A run resumed from `start`'s notes, or started from a ticket reference, is not asked again.
- **Both instruction files are written**, `AGENTS.md` and `CLAUDE.md`, with the same text: the four harness profiles read one or both.
- **Old marks and bindings are swept** whenever one is written. A start mark is deleted 7 days after it was written. A run binding is deleted when the run it names no longer exists or has reached phase `done`, never by age: a long run keeps its binding. This closes the gap left by the project-workspace work.
- **A marked session uses the default deny list.** `skill_guard.deny` is a project key, so it applies only to sessions working on a run of that project.
- **The guarantee is reported per harness.** The harness profile names the skill tool a hook can see (`tools.skill`, `Skill` on Claude, none where unknown), and the guarantee `skill-ownership` joins the list `kinetic harness` prints as Enforced or Instructed. "`skill_hook` is true" below means the profile names a skill tool.

## Approaches considered

1. **A skill whose text claims the conversation (`/kinetic:chat`).** Rejected: a skill is a peer of the skills it wants to exclude, and `start` already is the conversation entry.
2. **A louder session-start injection, like Superpowers'.** Rejected: it is still only Instructed, it starts an arms race between plugins, and the pipeline design ruled it out.
3. **Instruction files, a hook on skill invocations, a start mark, and a guard in phase skills (chosen).** The first covers every harness; the hook is the only part that does not depend on the model obeying; the mark covers the time before a project exists; the guard covers the user who starts in the wrong place.

## Design

### 1. Project instruction files (Instructed, all harnesses)

`kinetic project create` writes `<project>/AGENTS.md` and `<project>/CLAUDE.md`. The kinetic text sits between `<!-- kinetic:begin -->` and `<!-- kinetic:end -->`:

```
This folder is a kinetic project. Kinetic owns the workflow here.

- Start or resume work with the kinetic run skill (/kinetic:run, or kntc-run where kinetic's
  skills are installed as kntc-*). Use the implement skill for a ticket that is already specified.
- Do not start another plugin's process skill for work in this folder: no other brainstorming,
  planning, spec-writing, ticket-breakdown or implementation-loop skill. Use one only when a
  kinetic skill names it.
- Never edit files under .kinetic/ by hand.
```

`kinetic project instructions` rewrites the block for an existing project: it replaces the text between the markers, or appends the block, and keeps everything else in the file. `kinetic doctor` gains a `project-instructions` check (warn when a file or the block is missing).

### 2. Skill guard hook (Enforced where `skill_hook` is true)

New hook `hooks/skill_guard.py`, registered on the pre-tool event for the skill tool (`Skill` on Claude). It reads the skill name from the tool input and strips a leading `/`.

```text
skill_guard(call)
  skill is kinetic's own, or not on the deny list   -> say nothing
  session works on a run (same rule as isolation)    -> deny: "kinetic run <id> is active (phase <p>). Next: <next_step command>."
  session carries the start mark                     -> ask:  "This conversation was started with /kinetic:start. Allow <skill>?"
  otherwise                                          -> say nothing
```

It decides through the existing `_common.pre_tool`. A call whose skill name it cannot read passes untouched: this hook never fails closed, because a wrong refusal here blocks ordinary work.

### 3. Start mark

`skills/start/SKILL.md` gains a first step, before the project check: run `kinetic project start-session`. `claim_run` recognises that command (any spelling of the `kinetic` executable, as for `use-run`) and, when the call's working directory is in no project, writes `~/.config/kinetic/sessions/<session_id>`. The session id goes through the same checks as the run binding's.

The mark lasts for the session. After `start` creates a project, this session keeps the mark; the new session opened inside the project is covered by the instruction files and, once the run starts, by its binding.

### 4. Phase skills started in the wrong place

`kinetic next_step` gains one case: outside every project it answers `{"command": "/kinetic:start", "reason": "no kinetic project here"}`. Inside a project with no run it keeps answering `/kinetic:run <ref>`; with a run in another phase it already names the right command.

Each phase skill (`grill`, `discover`, `specify`, `decompose`, `ship`, `retro`) starts with the same line:

> If the orchestrator passed no `Run:`, run `kinetic next_step`, tell the user its `command` and `reason` in one line, and stop.

### 5. The run id is the user's choice

Today `start` and `run` pick the run id themselves: the ticket reference, or a kebab slug of the idea plus the date. Both now ask, with the harness's question tool when the profile names one, else in chat:

```
Run id?
1. <recommended id>   (recommended)
2. Another id: type it
```

The recommended id follows today's rule. `start` asks it right after the project is created, before the notes are written. `run` asks it only when it creates a new run from an idea or a spec path; a ticket reference is used as the id without asking, and a run that already exists is resumed.

A typed id is checked with `kinetic project run-dir --id <id>`, which already refuses an id that is not a single safe folder name. An id whose run folder already exists is refused too. Either way the question is asked again with the reason.

### Testing

- `test_skill_text`: `start` and `run` ask the run-id question, name the recommended id first and offer a typed one.
- `test_project`: `create` writes both files with the block; `instructions` replaces the block and keeps other text; `start-session` prints ok and writes nothing.
- `test_hooks`: skill guard denies a listed skill in a run session, asks in a marked session, is silent for an unlisted skill, for a kinetic skill, for an unmarked session and for an unreadable skill name; `claim_run` writes the mark outside a project and nothing inside one; the sweep deletes a mark older than 7 days and a binding whose run is done or gone, and keeps a newer mark and the binding of an open run however old.
- `test_next_step`: the no-project answer.
- `test_skill_text`: every phase skill carries the guard line; `start` runs `kinetic project start-session` before `kinetic project find`.
- `test_harness`: `skill_hook` per harness and the Enforced / Instructed line.

## Left to the builder

- **Claude is settled (probed 2026-11-15 with `claude -p` and a logging hook):** a skill the model invokes fires the pre-tool hook with `tool_name: "Skill"` and the name in `tool_input.skill` (`{"skill": "kinetic:tdd"}`). A slash command the user types (`/kinetic:tdd ...`) loads the skill without a `Skill` tool call, so the hook never sees it: a skill the user types is never refused. `skill_hook` is true on Claude.
- Whether Codex, OpenCode and Devin fire a pre-tool hook for skill invocations, and under which tool name and input shape. Settle each with a live probe like the Claude one, record it in `docs/smoke/`, and set `skill_hook` from the result. Until a harness is checked, its `skill_hook` is false.
- Whether each harness reads an instruction file in a parent folder when the session starts inside a worktree under `runs/`. Record it in `docs/smoke/project.md`.
- The exact names other plugins' skills carry on each harness (Claude passes `superpowers:brainstorming`; Codex stubs differ).
