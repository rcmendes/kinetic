from __future__ import annotations

import re
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FORBIDDEN = ("jira mode", "Jira key:", "getJiraIssue")

# Harness-neutral text (spec Testing Decisions): no skill or agent names a
# Claude-only tool. ${CLAUDE_PLUGIN_ROOT} is deliberately absent here: the
# launcher sweep owns those invocation lines and is still in flight.
CLAUDE_ONLY_TOOLS = ("AskUserQuestion", "SendMessage", "run_in_background")

# No skill or agent body names a concrete model; model: stays in frontmatter
# as the Claude/Devin default.
CONCRETE_MODEL = re.compile(r"\b(sonnet|opus|haiku|gpt-[\w.-]+|claude-[\w.-]+)\b")

# The user-only skills carry both invocation keys; no other skill may
# carry triggers: (doctor keeps only disable-model-invocation, per rulings).
USER_ONLY_SKILLS = ("ask", "bare-clone", "bare-refresh", "catalog-setup", "cleanup",
                    "implement", "run", "status")

# start replaces init; bare-setup is gone (the project holds bare-repos/).
SKILLS = {"ask", "bare-clone", "bare-refresh", "catalog-refresh", "catalog-setup", "cleanup",
          "decompose", "discover", "doctor", "grill", "implement", "retro", "run", "ship",
          "specify", "start", "status", "tdd"}

# The phase skills that stop and point to kinetic next_step when no Run: is passed.
PHASE_SKILLS = ("grill", "discover", "specify", "decompose", "ship", "retro")
GUARD_LINE = ("If the orchestrator passed no `Run:`, run `kinetic next_step`, tell the user "
              "its `command` and `reason` in one line, and stop.")

# Global config and bookkeeping paths the project layout replaced.
STALE = ("~/.config/kinetic/repos.json", "repos.json", "workspace_root", "active-run", "bare_dir",
         "kinetic bare show", "kinetic bare set", "kinetic bare detect", "/kinetic:bare-setup",
         "/kinetic:init", "<run_dir>/reports", "<run_dir>/logs", "<run_dir>/state.json",
         "<run_dir>/tickets.staged")

# Spec model tiers: implementer and fix-reviewer are standard; the reviewers and
# implementer-strong are strong. model: stays as the Claude/Devin default
# (sonnet for standard, opus for strong).
AGENT_TIERS = {
    "fix-reviewer": "standard",
    "implementer": "standard",
    "implementer-strong": "strong",
    "integration-reviewer": "strong",
    "spec-reviewer": "strong",
    "standards-reviewer": "strong",
}
TIER_MODELS = {"standard": "sonnet", "strong": "opus"}


def _text_files(*dirs):
    for d in dirs:
        for p in sorted((ROOT / d).rglob("*.md")):
            yield p


def _frontmatter(path):
    m = re.match(r"---\n(.*?)\n---\n", path.read_text(), re.S)
    return m.group(1) if m else None


def _body(path):
    text = path.read_text()
    m = re.match(r"---\n.*?\n---\n", text, re.S)
    return text[m.end():] if m else text


class SkillTextTest(unittest.TestCase):
    def test_no_jira_outside_adapters(self):
        bad = []
        for p in _text_files("skills", "agents"):
            text = p.read_text()
            for needle in FORBIDDEN:
                if needle in text:
                    bad.append("%s: %s" % (p.relative_to(ROOT), needle))
        self.assertEqual(bad, [])

    def test_no_claude_plugin_root_or_module_invocations(self):
        bad = []
        for p in _text_files("skills", "agents", "trackers"):
            text = p.read_text()
            for needle in ("${CLAUDE_PLUGIN_ROOT}", "PYTHONPATH",
                           "python3 -m kinetic"):
                if needle in text:
                    bad.append("%s: %s" % (p.relative_to(ROOT), needle))
        self.assertEqual(bad, [])

    def test_implementer_has_needs_human(self):
        text = (ROOT / "agents" / "implementer.md").read_text()
        self.assertIn("NEEDS_HUMAN", text)
        self.assertIn("Manual step:", text)
        self.assertIn("Ticket ref:", text)
        self.assertIn("Tracker adapter:", text)

    def test_build_loop_mentions_only_human_left_and_human_done(self):
        text = (ROOT / "skills" / "run" / "guides" / "build-loop.md").read_text()
        for needle in ("only_human_left", "waiting_on_you", "human-done",
                       "NEEDS_HUMAN", "board drift", "-human-N"):
            self.assertIn(needle, text)
        human = text.index("- `only_human_left` is true")
        stalled = text.index("the loop is stalled on edges")
        self.assertLess(human, stalled)

    def test_all_frontmatter_parses(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("yaml not importable")
        paths = list((ROOT / "skills").glob("**/SKILL.md")) + list((ROOT / "agents").glob("*.md"))
        self.assertTrue(paths)
        for p in paths:
            m = re.match(r"---\n(.*?)\n---\n", p.read_text(), re.S)
            self.assertIsNotNone(m, str(p))
            self.assertIsInstance(yaml.safe_load(m.group(1)), dict, str(p))

    def test_agents_declare_tier_beside_model(self):
        agents = sorted((ROOT / "agents").glob("*.md"))
        self.assertEqual({p.stem for p in agents}, set(AGENT_TIERS))
        for p in agents:
            fm = _frontmatter(p)
            self.assertIsNotNone(fm, str(p))
            tier = re.search(r"^tier:\s*(\S+)\s*$", fm, re.M)
            self.assertIsNotNone(tier, f"{p.name}: no tier: in frontmatter")
            self.assertEqual(tier.group(1), AGENT_TIERS[p.stem], p.name)
            model = re.search(r"^model:\s*(\S+)\s*$", fm, re.M)
            self.assertIsNotNone(model, f"{p.name}: no model: default")
            self.assertEqual(model.group(1), TIER_MODELS[tier.group(1)], p.name)

    def test_no_claude_only_tools(self):
        bad = []
        for p in _text_files("skills", "agents"):
            text = p.read_text()
            for needle in CLAUDE_ONLY_TOOLS:
                if needle in text:
                    bad.append("%s: %s" % (p.relative_to(ROOT), needle))
        self.assertEqual(bad, [])

    def test_no_concrete_model_in_body(self):
        bad = []
        for p in _text_files("skills", "agents"):
            for m in CONCRETE_MODEL.finditer(_body(p)):
                bad.append("%s: %s" % (p.relative_to(ROOT), m.group(0)))
        self.assertEqual(bad, [])

    def test_user_only_skills_have_both_invocation_keys(self):
        skills = sorted((ROOT / "skills").glob("**/SKILL.md"))
        self.assertTrue(skills)
        for p in skills:
            fm = _frontmatter(p)
            self.assertIsNotNone(fm, str(p))
            triggers = re.search(r"(?m)^triggers:", fm)
            if p.parent.name in USER_ONLY_SKILLS:
                self.assertIsNotNone(
                    re.search(r"(?m)^disable-model-invocation:\s*true\s*$", fm),
                    f"{p}: missing disable-model-invocation: true")
                self.assertIsNotNone(
                    re.search(r"(?m)^triggers:\s*\[user\]\s*$", fm),
                    f"{p}: missing triggers: [user]")
            else:
                self.assertIsNone(
                    triggers, f"{p}: triggers: belongs only on the user-only five")

    def test_implementer_strong_shares_implementer_body(self):
        self.assertEqual(
            _body(ROOT / "agents" / "implementer-strong.md"),
            _body(ROOT / "agents" / "implementer.md"))

    def test_discover_uses_bare_folder(self):
        text = (ROOT / "skills" / "discover" / "SKILL.md").read_text()
        for needle in ("kinetic bare list", "kinetic bare refresh",
                       "kinetic bare clone --repo", "kinetic bare clone --url", "not refreshed:",
                       "default_branch", "never run it without `--name`", "counts as one match",
                       "recheck with `kinetic bare list`", "kinetic bare gh-ready",
                       "kinetic bare create --owner", "--verify-ticket", "--setup",
                       "kinetic gitops setup", "owner, the name and the visibility"):
            self.assertIn(needle, text)
        self.assertNotIn("only the GitHub calls in step 4 reach the network", text)

    def test_skill_set(self):
        self.assertEqual({p.parent.name for p in (ROOT / "skills").glob("*/SKILL.md")}, SKILLS)

    def test_no_stale_global_config_or_run_paths(self):
        bad = []
        for p in [*_text_files("skills", "agents", "trackers"), ROOT / "README.md"]:
            text = p.read_text()
            for needle in STALE:
                if needle in text:
                    bad.append("%s: %s" % (p.relative_to(ROOT), needle))
        self.assertEqual(bad, [])

    def test_start_creates_the_project_only_after_the_yes(self):
        p = ROOT / "skills" / "start" / "SKILL.md"
        text = p.read_text()
        self.assertIn("kinetic project create", text)
        self.assertLess(text.index('"Turn this into a project?"'),
                        text.index("kinetic project create"))
        self.assertNotIn("disable-model-invocation", _frontmatter(p))
        for needle in ("kinetic project find", "docs/notes.md", "docs/refs/",
                       "open a new session inside `<project>`", "cd <project> && kinetic"):
            self.assertIn(needle, text)
        # After creating the project start never runs the run skill in this session.
        after = text[text.index("kinetic project create"):]
        self.assertNotIn("skills/run/SKILL.md", after)
        self.assertNotIn("kinetic project use-run", text)

    def test_run_binds_and_recovers_through_the_project(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text()
        for needle in ("kinetic project find", "/kinetic:start", "kinetic project run-dir --id",
                       "kinetic project use-run --run <run_dir>", "kinetic project current-run",
                       "kinetic project runs", "docs/notes.md` but no `.kinetic/state.json`",
                       "tracker: local for this run"):
            self.assertIn(needle, text)
        self.assertIn("kinetic project use-run --run <run_dir>",
                      (ROOT / "skills" / "run" / "guides" / "recovery.md").read_text())

    def test_build_loop_setup_scopes_and_report_paths(self):
        loop = (ROOT / "skills" / "run" / "guides" / "build-loop.md").read_text()
        for needle in ("kinetic gitops setup", "--scope full", "<run_dir>/needs-human/<id>-<N>.md",
                       "<run_dir>/.kinetic/reports/<id>.md", "proceed without setup",
                       "trackers/local.md"):
            self.assertIn(needle, loop)
        for agent in ("implementer", "implementer-strong"):
            self.assertIn("--scope ticket", (ROOT / "agents" / f"{agent}.md").read_text())

    def test_decompose_asks_whether_to_publish(self):
        text = (ROOT / "skills" / "decompose" / "SKILL.md").read_text()
        for needle in ("kinetic project update", "kinetic state set-ticket-store",
                       "never claims that task"):
            self.assertIn(needle, text)

    def test_discover_reads_catalog(self):
        text = (ROOT / "skills" / "discover" / "SKILL.md").read_text()
        for needle in ("kinetic catalog show", "index.md", "never skips"):
            self.assertIn(needle, text)

    def test_cleanup_offers_catalog_after_report(self):
        text = (ROOT / "skills" / "cleanup" / "SKILL.md").read_text()
        self.assertIn("catalog-refresh", text)
        self.assertGreater(text.index("catalog-refresh"), text.index("## 5. Report"))

    def test_catalog_refresh_is_approve_first(self):
        text = (ROOT / "skills" / "catalog-refresh" / "SKILL.md").read_text()
        self.assertIn("kinetic catalog apply", text)
        self.assertIn("writes nothing", text)
        self.assertNotIn("git commit", text)

    def test_phase_skills_guard_line(self):
        for name in PHASE_SKILLS:
            self.assertIn(GUARD_LINE, _body(ROOT / "skills" / name / "SKILL.md"), name)
        for name in sorted(SKILLS - set(PHASE_SKILLS)):
            self.assertNotIn(GUARD_LINE, _body(ROOT / "skills" / name / "SKILL.md"), name)

    def test_start_marks_session_before_project_check(self):
        text = (ROOT / "skills" / "start" / "SKILL.md").read_text()
        self.assertIn("kinetic project start-session", text)
        self.assertLess(text.index("kinetic project start-session"),
                        text.index("kinetic project find"))

    def test_start_and_run_ask_run_id(self):
        for name in ("start", "run"):
            text = (ROOT / "skills" / name / "SKILL.md").read_text()
            for needle in ("(recommended)", "Another id: type it", "kinetic project run-dir --id <id>",
                           '"exists": true', "the harness's question tool when the profile names one"):
                self.assertIn(needle, text, name)

    def test_run_resumes_or_suffixes_an_existing_recommended_id(self):
        text = (ROOT / "skills" / "run" / "SKILL.md").read_text()
        for needle in ("kinetic project run-dir --id <recommended>", "resume it without asking",
                       "next free numeric suffix (`-2`, `-3`, ...)"):
            self.assertIn(needle, text)
        self.assertLess(text.index("kinetic project run-dir --id <recommended>"), text.index("Run id?"))


if __name__ == "__main__":
    unittest.main()


class OrbitSkillTextTest(unittest.TestCase):
    def test_decompose_has_generic_publish_sentence(self):
        text = (ROOT / "skills/decompose/SKILL.md").read_text()
        self.assertIn("When the tracker adapter says it publishes staged tickets, follow the Local steps; "
                      "kinetic publishes them when the breakdown gate is approved.", text)
        self.assertNotIn("orbit", text.lower())

    def test_run_skill_records_the_ticket_store(self):
        text = (ROOT / "skills/run/SKILL.md").read_text()
        self.assertIn('--ticket-store "$(kinetic tracker store-record)"', text)
