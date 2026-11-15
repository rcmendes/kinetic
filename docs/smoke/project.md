# Orbit Board outside a git repo (smoke)

Date: 2026-11-14. `orbit` found on PATH (`~/.cargo/bin/orbit`).

In a fresh temp folder that is not a git repo (`git rev-parse` fails there):

```
orbit init                              # creates .orbit/, exit 0
orbit list --json --dir <project>       # {"data":{"count":0,"tasks":[]},"meta":{"count":0},"ok":true}
```

Result: `ok` is true. An Orbit Board can live in the project folder, so the default
anchor stays the project folder. No change to `orbit_store`, no fallback test.

## Open fact: instruction files from a parent folder

`kinetic project create` writes `<project>/AGENTS.md` and `<project>/CLAUDE.md`. Open: whether each harness (Claude Code, Codex, OpenCode, Devin) reads an instruction file from a parent folder when the session starts inside a worktree under `<project>/runs/<run-id>/worktrees/<repo>`. Probe: start a session in such a worktree and ask the agent what the kinetic block in its instructions says. Record the date, the command and the answer per harness here.
