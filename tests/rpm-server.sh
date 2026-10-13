#!/bin/bash
# Serveur du test RPM (VM jetable uniquement) : installe local-repo pour des postes qui le joignent par
# 10.0.2.2 (passerelle QEMU), puis publie un paquet de test dans el8, el9 et el10.
set -euo pipefail
cd "$(dirname "$0")/.."
PW=${1:?Usage : tests/rpm-server.sh MOT_DE_PASSE}
./install.sh --host 10.0.2.2 --password "$PW" --no-wu --no-flatpak 2>&1 | tail -12
work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT
for major in 8 9 10; do
    top="$work/el$major"
    mkdir -p "$top"
    cat > "$top/test.spec" <<SPEC
Name: lr-rpm-e2e
Version: 1.0
Release: 1.el$major
Summary: paquet de test local-repo
License: MIT
BuildArch: noarch
%description
paquet de test local-repo
%install
mkdir -p %{buildroot}/usr/share/lr-rpm-e2e
echo local-repo-rpm-el$major > %{buildroot}/usr/share/lr-rpm-e2e/content
%files
/usr/share/lr-rpm-e2e/content
SPEC
    rpmbuild --quiet --define "_topdir $top" --define "_build_id_links none" -bb "$top/test.spec"
    python3 tests/e2e_portal.py "https://127.0.0.1:8443" "$PW" "$top/RPMS/noarch/lr-rpm-e2e-1.0-1.el$major.noarch.rpm" rpm "el$major"
done
echo "Serveur prêt."
