# Releasing kinetic

kinetic is distributed as a Claude Code plugin marketplace that is this repository: `.claude-plugin/marketplace.json` lists one plugin whose `source` is `./`. Publishing a release is therefore merging to `main`. Everything else on this page keeps that merge safe and lets people find the new version.

## What carries the version

Three places say the version, and a test (`tests/test_release.py`) fails when they differ:

- `.claude-plugin/plugin.json` (`version`): the one Claude Code installs by.
- `.claude-plugin/marketplace.json` (`plugins[0].version`): the marketplace listing.
- `README.md`: the line `kinetic is at <version>` in the status section.

The `version-bump` workflow (`.github/workflows/version-bump.yml`) runs on a pull request to `main` that changes something that ships in the plugin: `.claude-plugin`, `agents`, `bin`, `hooks`, `profiles`, `scripts`, `skills`, `templates` or `trackers`. Documentation, tests and the repository's own files (the README, `docs/`) do not trigger it. When it runs it fails unless `plugin.json` is higher than on `main`, and unless `marketplace.json` matches `plugin.json`. A test (`tests/test_release.py`) fails if a shipped directory is missing from the workflow's list, so the list cannot go stale.

## Choosing the number

kinetic is before 1.0. Raise the minor number (`0.7.0`) for new features or for a change that asks users to do something: a higher minimum version of a dependency such as Orbit, a migration, a changed command. Raise the patch number (`0.7.1`) for fixes that need no action.

## Steps

1. Make sure `main` has everything the release should carry, and run the test suite on a supported Python:

   ```
   PYTHONPATH=scripts python3 -m unittest discover -s tests -t .
   ```

   Tests that need the `orbit` command are skipped without it; run them with `orbit` 0.11.0 or later on PATH before a release that touches the Orbit store. Applying a run migration needs Python 3.11 or later.

2. On a branch, bump the version in the three places above. Describe what changed for users in the pull request.
3. Open the pull request to `main`; the `version-bump` check must pass. Merge it.
4. Tag the merge commit and create the release notes:

   ```
   git tag v<version> <merge commit>
   git push origin v<version>
   gh release create v<version> --title "kinetic <version>" --notes "<what changed, what users must do>"
   ```

   The tag is for people and for rollbacks; Claude Code reads the marketplace from the default branch, not from tags.

   Write the release notes for the person who will read them in `kinetic update`, which prints them. Use three parts, in this order:

   - **What users must do**: the actions, or "nothing". For example a higher minimum version of a dependency, a migration command, a setting that changed, a harness that must re-trust hooks. `/kinetic:update` tells the user this part plainly, so write it as instructions.
   - **What is new**: the changes in a few lines.
   - **Not verified**: what you did not test, so nobody assumes it works.

5. Check that it published. On your own machine:

   ```
   claude plugin marketplace update kinetic
   claude plugin update kinetic@kinetic
   ```

   then look at `~/.claude/plugins/installed_plugins.json` for the new `version`, run `kinetic install --harness claude`, and `kinetic doctor`.

## How users learn about a release

- `kinetic doctor` has two rows. `version-skew` warns when the plugin a harness loaded and the checkout the `kinetic` command runs from differ (only Claude Code records its plugin version where kinetic can read it). `update-available` warns when a newer release exists.
- At session start the `orient` hook adds one line, at most once a day, when the cached release is newer than the checkout. It reads only local files and starts the lookup in a detached process, so it never waits for the network.
- Both look up GitHub's latest release through `gh`, cache the answer for a day in `~/.config/kinetic/update-check.json`, and say nothing offline. `"update_check": false` in `~/.config/kinetic/settings.json` turns the lookup and the session line off; `version-skew` stays because it reads only local files.
- `/kinetic:update` (or `kinetic update` in a terminal) shows the notes, then pulls the checkout, reinstalls every harness and refreshes the Claude Code plugin, one step at a time, asking before each. It refuses to pull a checkout that has local changes or is not on `main`.

A release is only seen by this machinery once it is a GitHub release, not just a tag: create it with `gh release create` as in the steps above.

## What users do

Marketplace users run the two commands above and re-run `kinetic install --harness claude`. Checkout users run `git pull` and re-run `kinetic install --harness <h>` for each harness they use. State any extra step (a new minimum Orbit version, a migration) in the release notes and in the README's update section.

## Rolling back

Do not move or delete a published tag. Revert the change on `main` and release a higher version with the revert, because the installed copy is keyed by version.

## Not covered

- Devin, Codex and OpenCode installs come from a checkout, not the marketplace. Their live smoke checklists in `docs/smoke/` have not been run, so a release does not claim them.
- There is no changelog file; the GitHub release notes are the record.
