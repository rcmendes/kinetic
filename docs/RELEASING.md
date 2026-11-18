# Releasing kinetic

kinetic is distributed as a Claude Code plugin marketplace that is this repository: `.claude-plugin/marketplace.json` lists one plugin whose `source` is `./`. Publishing a release is therefore merging to `main`. Everything else on this page keeps that merge safe and lets people find the new version.

## What carries the version

Three places say the version, and a test (`tests/test_release.py`) fails when they differ:

- `.claude-plugin/plugin.json` (`version`): the one Claude Code installs by.
- `.claude-plugin/marketplace.json` (`plugins[0].version`): the marketplace listing.
- `README.md`: the line `kinetic is at <version>` in the status section.

The `version-bump` workflow (`.github/workflows/version-bump.yml`) runs on every pull request to `main`. It fails unless `plugin.json` is higher than on `main`, and unless `marketplace.json` matches `plugin.json`.

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

5. Check that it published. On your own machine:

   ```
   claude plugin marketplace update kinetic
   claude plugin update kinetic@kinetic
   ```

   then look at `~/.claude/plugins/installed_plugins.json` for the new `version`, run `kinetic install --harness claude`, and `kinetic doctor`.

## What users do

Marketplace users run the two commands above and re-run `kinetic install --harness claude`. Checkout users run `git pull` and re-run `kinetic install --harness <h>` for each harness they use. State any extra step (a new minimum Orbit version, a migration) in the release notes and in the README's update section.

## Rolling back

Do not move or delete a published tag. Revert the change on `main` and release a higher version with the revert, because the installed copy is keyed by version.

## Not covered

- Devin, Codex and OpenCode installs come from a checkout, not the marketplace. Their live smoke checklists in `docs/smoke/` have not been run, so a release does not claim them.
- There is no changelog file; the GitHub release notes are the record.
