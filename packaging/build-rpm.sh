#!/bin/sh
# Build a local RPM from this checkout. Offline after the build dependencies are installed:
#   sudo dnf install rpm-build && sudo dnf builddep packaging/shokzer.spec
# Output goes to build/rpm/; nothing is installed or published.
set -eu
cd "$(dirname "$0")/.."
rm -rf dist build/rpm
uv build --sdist --out-dir dist
rpmbuild -ba packaging/shokzer.spec \
    --define "_topdir $PWD/build/rpm" \
    --define "_sourcedir $PWD/dist"
find build/rpm -name '*.rpm'
