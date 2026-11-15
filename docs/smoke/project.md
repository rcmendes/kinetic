# Orbit Board outside a git repo (smoke)

Date: 2026-11-14. `orbit` found on PATH (`~/.cargo/bin/orbit`).

In a fresh temp folder that is not a git repo (`git rev-parse` fails there):

```
orbit init                              # creates .orbit/, exit 0
orbit list --json --dir <project>       # {"data":{"count":0,"tasks":[]},"meta":{"count":0},"ok":true}
```

Result: `ok` is true. An Orbit Board can live in the project folder, so the default
anchor stays the project folder. No change to `orbit_store`, no fallback test.

## Instruction files from a parent folder (probed 2026-11-15)

`kinetic project create` writes `<project>/AGENTS.md` and `<project>/CLAUDE.md`. Probe: a scratch project whose `CLAUDE.md` says "The secret word is PELICAN-CLAUDE." and whose `AGENTS.md` says "The secret word is HERON-AGENTS.", with a git repo at `runs/r1/worktrees/api`; ask the agent, with no tool, to list the secret words in its loaded instruction files.

| Harness | Session started in the worktree | Session started at the project root (not a git repo) |
|---|---|---|
| Claude Code (`claude -p`) | PELICAN-CLAUDE: reads `CLAUDE.md` from the parent folders, not `AGENTS.md` | not run |
| OpenCode 2.2.5 | NONE: reads neither file | HERON-AGENTS: reads `AGENTS.md`, not `CLAUDE.md` |
| Codex CLI | not run: usage limit reached | not run: usage limit reached |
| Devin CLI | not run | not run |

What follows: the instruction files reach a session started at the project root (OpenCode) or anywhere under it (Claude Code, through `CLAUDE.md`). They do not reach an OpenCode session started inside a worktree, because the worktree is its own git root. Kinetic does not write instruction files into a worktree. The `skill_guard` hook covers that case on Claude Code and OpenCode.
