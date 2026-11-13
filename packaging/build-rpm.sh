#!/bin/sh
# Build the RPM from the committed tree (uncommitted changes are not included).
#   sudo dnf install rpm-build git && sudo dnf builddep packaging/shokzer.spec
#   packaging/build-rpm.sh
# Output goes to build/rpm/. Nothing is installed or published.
set -eu
cd "$(dirname "$0")/.."
version=$(sed -n 's/^%global pypi_version //p' packaging/shokzer.spec)
rm -rf build/rpm
mkdir -p build/rpm/SOURCES
git archive --format=tar.gz --prefix="shokzer-$version/" \
    -o "build/rpm/SOURCES/shokzer-$version.tar.gz" HEAD
rpmbuild -ba packaging/shokzer.spec --define "_topdir $PWD/build/rpm"
find build/rpm -name '*.rpm'
