# Releasing

1. Update the version in all four places: `pyproject.toml`, `src/shokzer/__init__.py`, `packaging/shokzer.spec` (`pypi_version`, `Version`, a `%changelog` entry) and `install.sh` (`VERSION`).
2. In `CHANGELOG.md`, rename `## Unreleased` to `## <version> — <date>` and add a new empty `## Unreleased` above it. Add a `<release>` entry to `data/pm.markos.Shokzer.metainfo.xml`.
3. Run the checks in `CONTRIBUTING.md`, including the GUI tests.
4. Commit, then tag and push:

   ```sh
   git tag v<version> && git push origin main v<version>
   ```

The `Release` workflow checks the tag matches `pyproject.toml`, builds the wheel, the source archive and RPMs for Fedora 43 and 44, and publishes a GitHub release with them and `install.sh`. The release notes come from the matching `CHANGELOG.md` section.

The installer downloads the tag named by its `VERSION`, so the `install.sh` on `main` must only be bumped together with the release.

## Repository setup (once, after the first push)

These commands configure the GitHub repository:

- Description and topics; wiki and projects off.
- Squash merges only, using the pull request's title and body.
- Private vulnerability reporting, Dependabot alerts and security fixes.
- The rulesets in `.github/rulesets/`:
  - `main` can't be deleted or force-pushed.
  - Changes to `main` go through a pull request with passing CI. Repository admins can bypass this, so the maintainer can still push directly.
  - `v*` tags can't be moved or deleted.

```sh
repo=merkourio/shokzer
gh repo edit "$repo" \
  --description "Settings for the Shokz OpenRun Pro 2 on Linux: EQ, custom EQ, multipoint" \
  --add-topic shokz,openrun,bluetooth,headphones,equalizer,bluez,gtk4,libadwaita,linux \
  --enable-wiki=false --enable-projects=false \
  --enable-squash-merge --enable-merge-commit=false --enable-rebase-merge=false \
  --delete-branch-on-merge
gh api -X PATCH "repos/$repo" -f squash_merge_commit_title=PR_TITLE -f squash_merge_commit_message=PR_BODY
gh api -X PUT "repos/$repo/private-vulnerability-reporting"
gh api -X PUT "repos/$repo/vulnerability-alerts"
gh api -X PUT "repos/$repo/automated-security-fixes"
for f in .github/rulesets/*.json; do gh api -X POST "repos/$repo/rulesets" --input "$f"; done
```

GitHub doesn't read `.github/rulesets/` by itself, so a changed ruleset has to be applied again with `gh api`. The required check names in `main-ci.json` must match the job names in `.github/workflows/ci.yml`; update them when the Python or Fedora versions change.

## Fedora COPR (optional)

A COPR repository gives Fedora users `dnf` updates. It needs a [Fedora account](https://accounts.fedoraproject.org/).

1. Create a project at [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) named `shokzer`, with the Fedora releases to build for.
2. Add a package with source type **SCM**: clone URL `https://github.com/merkourio/shokzer`, build method **make srpm** (it uses `.copr/Makefile`), and enable rebuilds on new tags via the webhook COPR shows.
3. Users then install with `sudo dnf copr enable <account>/shokzer && sudo dnf install shokzer`.
