#!/usr/bin/env bash
# Build the DiscBay RPM on Fedora.
#   packaging/build-rpm.sh          build dist/discbay-<version>.noarch.rpm
#   packaging/build-rpm.sh --srpm   build only the source RPM (what COPR uses)
#
# Needs:  sudo dnf install rpm-build python3-devel pyproject-rpm-macros \
#           systemd-rpm-macros desktop-file-utils python3-gobject python3-flask xorriso
set -euo pipefail
cd "$(dirname "$0")/.."

version=$(sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml)
spec_version=$(sed -n 's/^Version: *//p' packaging/discbay.spec)
if [ "$version" != "$spec_version" ]; then
  echo "error: pyproject.toml says $version but packaging/discbay.spec says $spec_version" >&2
  exit 1
fi

outdir=${OUTDIR:-$PWD/dist}
top=$(mktemp -d)
trap 'rm -rf "$top"' EXIT
mkdir -p "$top/SOURCES" "$outdir"

# The source tarball, laid out like GitHub's release archive.
tarball="$top/SOURCES/discbay-$version.tar.gz"
if git rev-parse --git-dir >/dev/null 2>&1; then
  if [ -n "$(git status --porcelain)" ]; then
    # Include uncommitted and new (but not ignored) files, so you can test before committing.
    echo "note: building from uncommitted changes in your working tree"
    git ls-files -z --cached --others --exclude-standard | \
      while IFS= read -r -d '' f; do [ -e "$f" ] && printf '%s\0' "$f"; done | \
      tar --null -T - --transform "s,^,discbay-$version/," -czf "$tarball"
  else
    git archive --prefix="discbay-$version/" -o "$tarball" HEAD
  fi
else
  tar --exclude=./dist --exclude=.git --transform "s,^\.,discbay-$version," -czf "$tarball" .
fi

mode=-ba
[ "${1:-}" = "--srpm" ] && mode=-bs
rpmbuild "$mode" packaging/discbay.spec ${RPMBUILD_ARGS:-} \
  --define "_topdir $top" --define "_sourcedir $top/SOURCES" \
  --define "_rpmdir $top/RPMS" --define "_srcrpmdir $top/SRPMS"

find "$top/RPMS" "$top/SRPMS" -name '*.rpm' -exec cp {} "$outdir/" \; 2>/dev/null || true
echo
echo "Built:"
ls -1 "$outdir"/*.rpm
[ "$mode" = -ba ] && echo && echo "Install it with:  sudo dnf install $(ls "$outdir"/discbay-"$version"-*.noarch.rpm | head -1)"
exit 0
