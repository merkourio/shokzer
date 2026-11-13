# Releasing

1. Update the version in all four places: `pyproject.toml`, `src/shokzer/__init__.py`, `packaging/shokzer.spec` (`pypi_version`, `Version`, a `%changelog` entry) and `install.sh` (`VERSION`).
2. Add a section to `CHANGELOG.md` headed `## <version> — <date>`, and a `<release>` entry to `data/pm.markos.Shokzer.metainfo.xml`.
3. Run the checks in `CONTRIBUTING.md`, including the GUI tests.
4. Commit, then tag and push:

   ```sh
   git tag v<version> && git push origin main v<version>
   ```

The `Release` workflow checks the tag matches `pyproject.toml`, builds the wheel, the source archive and RPMs for Fedora 43 and 44, and publishes a GitHub release with them and `install.sh`. The release notes come from the matching `CHANGELOG.md` section.

The installer downloads the tag named by its `VERSION`, so the `install.sh` on `main` must only be bumped together with the release.

## Fedora COPR (optional)

A COPR repository gives Fedora users `dnf` updates. It needs a [Fedora account](https://accounts.fedoraproject.org/).

1. Create a project at [copr.fedorainfracloud.org](https://copr.fedorainfracloud.org/) named `shokzer`, with the Fedora releases to build for.
2. Add a package with source type **SCM**: clone URL `https://github.com/merkourio/shokzer`, build method **make srpm** (it uses `.copr/Makefile`), and enable rebuilds on new tags via the webhook COPR shows.
3. Users then install with `sudo dnf copr enable <account>/shokzer && sudo dnf install shokzer`.
