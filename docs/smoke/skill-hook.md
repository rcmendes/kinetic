# Skill hook per harness (smoke)

The `skill_guard` hook can refuse a skill only where the harness fires a pre-tool hook for a skill call. The harness profile names that tool in `tools.skill` (`scripts/kinetic/harness.py`); `skill-ownership` is Enforced where it is set and Instructed elsewhere. Set `tools.skill` for a harness, and keep `skill-ownership` in its profile's `enforced` list, only after its probe below is recorded here.

## Claude Code (settled)

Date: 2026-11-15. Run with `claude -p --settings <file>`, where the settings file registers a PreToolUse hook on matcher `Skill` that appends its stdin to a log file.

- A skill the model invoked produced `{"hook_event_name": "PreToolUse", "tool_name": "Skill", "tool_input": {"skill": "kinetic:tdd"}}`.
- A prompt starting with the slash command `/kinetic:tdd` loaded the skill and produced no hook event.

Result: `tools.skill` is `Skill`. The hook sees only skills the model invokes; a skill the user types as a slash command is never refused.

## OpenCode (settled)

Date: 2026-11-15, OpenCode 2.2.5. Probed with a project-local plugin (`<project>/.opencode/plugins/`) whose `tool.execute.before` hook appended each event to a log, then end to end with the plugin that `kinetic install --harness opencode` generates.

- A skill the model loaded produced `{"tool": "skill", "input": {"id": "kntc-ask"}, ...}`: the tool is `skill` and the skill name is in `input.id`, not `input.skill`. The normaliser maps it to `tool_name: "Skill"` and `tool_input.skill`.
- End to end, in a temp project with an open run, run from the run folder: a skill that is not on the list loaded; a listed skill (`brainstorming`) was refused with `kinetic run r1 is active (phase intake). Next: /kinetic:run.`
- End to end, in a folder outside any project with a start mark written for the session (`opencode run -s <id>`): the listed skill raised OpenCode's own permission prompt (`permission requested: skill (brainstorming)`), which a non-interactive run auto-rejects; a skill that is not listed loaded without a prompt.
- Not probed: a skill typed as a slash command. Not probed: OpenCode 1.x (the v1 entrypoint registers the same tool name, unverified).
- Note for probes: `XDG_CONFIG_HOME` and `OPENCODE_CONFIG_DIR` did not redirect plugin loading; a plugin in the project's own `.opencode/plugins/` did. A plugin with the same `id` as an installed one is shadowed by it: give a probe plugin its own `id`.

Result: `tools.skill` is `skill`, and `skill-ownership` is Enforced on OpenCode. The installed plugin must be regenerated with `kinetic install --harness opencode` to route skill calls to `skill_guard.py`.

## Claude Code matcher

The hook is registered with matcher `^Skill$`. Re-checked on 2026-11-15 with `claude -p --settings <file>`: a model-invoked skill still fires the hook with that matcher.

## Open facts

For each harness below, register a pre-tool hook on every tool that appends its stdin to a log file, then run one prompt that leads the model to load a skill by itself. Record the date, the command, the logged event (tool name and input shape), and the `tools.skill` value that follows.

- **Codex CLI**: not probed. Attempted 2026-11-15 and blocked by the account's usage limit (`codex exec` refused to start a turn). Register a hook in `~/.codex/hooks.json` (trusted through `/hooks`) and run `codex exec "<prompt>"`. Open: whether a skill load is a tool call that fires PreToolUse, and under which tool name and input shape.
- **Devin CLI**: not probed. Attempted 2026-11-15 with `devin --config <temp file> -p "<prompt>"`; Devin refused to run in an untrusted workspace, and the probe did not lower that check. Trust the scratch folder once by starting `devin` in it, or run the probe in an already trusted folder. Open: whether a skill invocation fires the hook, and under which tool name.

Until a harness is probed, its `tools.skill` stays `None` and `skill-ownership` stays Instructed there.
