#!/bin/bash
# Paquets réels pour les essais du serveur Windows (construction sur Linux).
set -euo pipefail
dest=${1:?dossier de sortie requis}
mkdir -p "$dest"
dest=$(realpath "$dest")
work=$(mktemp -d)
trap 'rm -rf -- "$work"' EXIT
mkdir -p "$work/deb/DEBIAN" "$work/deb/usr/share/lr-native-e2e"
printf 'Package: lr-native-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: Tests <tests@invalid>\nDescription: native Windows server test\n' > "$work/deb/DEBIAN/control"
printf 'local-repo native server\n' > "$work/deb/usr/share/lr-native-e2e/content"
for compression in xz gzip zstd; do
    dpkg-deb --root-owner-group -Z"$compression" -b "$work/deb" "$dest/$compression.deb"
done
cat > "$work/test.spec" <<'SPEC'
Name: lr-native-e2e
Version: 1.0
Release: 1.el9
Summary: native Windows server test
License: MIT
BuildArch: noarch
%description
Test repository served by native Windows.
%install
mkdir -p %{buildroot}/usr/share/lr-native-e2e
echo 'local-repo native server' > %{buildroot}/usr/share/lr-native-e2e/content
%files
/usr/share/lr-native-e2e/content
SPEC
rpmbuild --define "_topdir $work/rpm" --define '_build_id_links none' -bb "$work/test.spec"
cp "$work"/rpm/RPMS/noarch/*.rpm "$dest/test.rpm"
