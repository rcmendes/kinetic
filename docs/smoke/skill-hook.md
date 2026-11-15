# Skill hook per harness (smoke)

The `skill_guard` hook can refuse a skill only where the harness fires a pre-tool hook for a skill call. The harness profile names that tool in `tools.skill` (`scripts/kinetic/harness.py`); `skill-ownership` is Enforced where it is set and Instructed elsewhere. Set `tools.skill` for a harness, and add `skill-ownership` to its profile's `enforced` list, only after its probe below is recorded here.

## Claude Code (settled)

Date: 2026-11-15. Run with `claude -p --settings <file>`, where the settings file registers a PreToolUse hook on matcher `Skill` that appends its stdin to a log file.

- A skill the model invoked produced `{"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "kinetic:tdd"}}`.
- A prompt starting with the slash command `/kinetic:tdd` loaded the skill and produced no hook event.

Result: `tools.skill` is `Skill`. The hook sees only skills the model invokes; a skill the user types as a slash command is never refused.

## Open facts

For each harness, register a pre-tool hook on every tool (matcher `.*` or the harness's equivalent) that appends its stdin, or its input object, to a log file. Then run two prompts in a scratch folder with kinetic installed: one that leads the model to invoke a skill by itself ("use the kntc-tdd skill to ..."), and one that invokes the skill the way a user types it. Record the date, the command, the logged event for each prompt (tool name and input shape), and the `tools.skill` value that follows.

- **Codex CLI**: a hook in `~/.codex/hooks.json` (trusted through `/hooks`), run with `codex exec "<prompt>"`. Open: whether a skill load is a tool call that fires PreToolUse, and under which tool name.
- **OpenCode**: a file-dropped plugin in `~/.config/opencode/plugins/` whose `tool.execute.before` appends `input` and `output.args` to the log, run with `opencode run "<prompt>"`. Open: whether the skill tool passes through `tool.execute.before`, and the shape of its arguments.
- **Devin CLI**: a PreToolUse hook in the Devin hooks config that `kinetic install --harness devin` writes, run non-interactively with a prompt. Open: whether a skill invocation fires the hook, and under which tool name.

Until a harness is probed, its `tools.skill` stays `None` and `skill-ownership` stays Instructed there.
