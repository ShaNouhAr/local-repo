#!/bin/bash
# Serveur natif Rocky 9/10 : VM jetable uniquement (installation puis purge).
set -euo pipefail
cd "$(dirname "$0")/.."
. /etc/os-release
[ "$ID" = rocky ]
case "$VERSION_ID" in 9.*|10.*) ;; *) exit 1 ;; esac
# Les essais locaux peuvent choisir un miroir Rocky plus rapide ; signatures RPM conservées.
if [ -n "${LR_TEST_ROCKY_MIRROR:-}" ]; then
    python3 - <<'PY'
from pathlib import Path
import os,re
mirror=os.environ['LR_TEST_ROCKY_MIRROR'].rstrip('/')+'/'
assert re.fullmatch(r'https://[a-zA-Z0-9./_-]+/',mirror)
for p in Path('/etc/yum.repos.d').glob('rocky*.repo'):
    text=re.sub(r'^mirrorlist=', '#mirrorlist=',p.read_text(),flags=re.M)
    text=re.sub(r'^#baseurl=https?://dl.rockylinux.org/\$contentdir/', 'baseurl='+mirror,text,flags=re.M)
    p.write_text(text)
PY
fi
PW=Native-Rocky-E2e-Password-2026
dnf install -y nginx firewalld rpm-build >/dev/null
systemctl enable --now firewalld >/dev/null
[ "$(getenforce)" = Enforcing ]
original_nginx=$(sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1)
original_bool=$(getsebool httpd_can_network_connect)
install_log=/tmp/native-rocky-install.log
failure_log() {
    local rc=$?
    if [ "$rc" != 0 ]; then tail -40 "$install_log"; journalctl -u local-repo-portal -u apt-cacher-ng -u nginx --no-pager -n 70; fi
}
trap failure_log EXIT
./install.sh --host 10.0.2.15 --password "$PW" > "$install_log" 2>&1
CA=/etc/local-repo/tls/ca.crt
BASE=https://127.0.0.1:8443
health() { curl -fsS --cacert "$CA" "$BASE/healthz" | grep -qx ok; }
health
systemctl is-active --quiet nginx apt-cacher-ng local-repo-portal local-repo-backup.timer local-repo-stats.timer
if [ "${VERSION_ID%%.*}" = 10 ]; then
    systemctl is-active --quiet local-repo-acng-maintenance.timer
    systemctl start local-repo-acng-maintenance.service
fi
firewall-cmd --query-service=local-repo
[ "$(getenforce)" = Enforcing ]
/usr/local/lib/local-repo/venv/bin/python -m unittest discover -s tests -v

work=$(mktemp -d)
mkdir -p "$work/deb/DEBIAN" "$work/deb/usr/share/lr-native-e2e"
printf 'Package: lr-native-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: CI <ci@invalid>\nDescription: Test serveur Rocky\n' > "$work/deb/DEBIAN/control"
echo serveur-rocky > "$work/deb/usr/share/lr-native-e2e/content"
dpkg-deb --build --root-owner-group "$work/deb" "$work/lr-native-e2e.deb"
/usr/local/sbin/local-repo-apt add debian trixie 13
python3 tests/e2e_portal.py "$BASE" "$PW" "$work/lr-native-e2e.deb" linux debian/trixie
curl -fsS --cacert "$CA" "$BASE/apt/repositories/debian/trixie/Packages" | grep -q 'Package: lr-native-e2e'
curl -fsS --cacert "$CA" "$BASE/linux/repositories/debian/trixie.sources" | grep -q 'Signed-By:'
curl -fsS --proxy http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o "$work/debian-release"
grep -q '^Origin: Debian' "$work/debian-release"

major=${VERSION_ID%%.*}
cat > "$work/test.spec" <<EOF
Name: lr-native-e2e
Version: 1.0
Release: 1.el$major
Summary: Test du serveur Rocky
License: MIT
BuildArch: noarch
%description
Test local-repo sur Rocky.
%install
mkdir -p %{buildroot}/usr/share/lr-native-rpm
echo serveur-rocky > %{buildroot}/usr/share/lr-native-rpm/content
%files
/usr/share/lr-native-rpm/content
EOF
rpmbuild --quiet --define "_topdir $work/rpm" --define '_build_id_links none' -bb "$work/test.spec"
python3 tests/e2e_portal.py "$BASE" "$PW" "$work/rpm/RPMS/noarch/lr-native-e2e-1.0-1.el$major.noarch.rpm" rpm "el$major"
curl -fsS --cacert "$CA" "$BASE/rpm/setup.sh" -o "$work/rpm-setup.sh"
sh "$work/rpm-setup.sh" --no-cache --trust-ca
dnf install -y --disablerepo='*' --enablerepo=local-repo lr-native-e2e
grep -qx serveur-rocky /usr/share/lr-native-rpm/content
dnf remove -y lr-native-e2e >/dev/null
sh "$work/rpm-setup.sh" --remove
curl -fsS --cacert "$CA" "$BASE/flatpak/local-repo.flatpakrepo" | grep -q 'GPGKey='

keys=$(sha256sum "$CA" /srv/local-repo/apt/local-repo.gpg)
users=$(python3 -c 'import sqlite3,hashlib; c=sqlite3.connect("/var/lib/local-repo/portal.db"); print(hashlib.sha256(repr(c.execute("SELECT * FROM users").fetchall()).encode()).hexdigest())')
./install.sh --allow 127.0.0.1 > "$install_log" 2>&1
[ "$(curl -sS -o /dev/null -w '%{http_code}' --interface 10.0.2.15 --cacert "$CA" https://10.0.2.15:8443/healthz)" = 403 ]
if curl -fsS --max-time 10 --interface 10.0.2.15 --proxy http://10.0.2.15:3142 http://deb.debian.org/debian/dists/stable/Release > /dev/null; then
    echo 'Le cache APT accepte un client interdit' >&2; exit 1
fi
./install.sh --allow '10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10' > "$install_log" 2>&1
health
[ "$keys" = "$(sha256sum "$CA" /srv/local-repo/apt/local-repo.gpg)" ]
[ "$users" = "$(python3 -c 'import sqlite3,hashlib; c=sqlite3.connect("/var/lib/local-repo/portal.db"); print(hashlib.sha256(repr(c.execute("SELECT * FROM users").fetchall()).encode()).hexdigest())')" ]
backup=$(/usr/local/sbin/local-repo-backup create | awk '{print $1}')
deb=$(find /srv/local-repo/apt/repositories/debian/trixie/pool -name '*.deb' -print -quit)
deb_sum=$(sha256sum "$deb")
rm "$deb"
/usr/local/sbin/local-repo-backup restore "$backup"
./install.sh > "$install_log" 2>&1
[ "$deb_sum" = "$(sha256sum "$deb")" ]
systemctl restart local-repo-portal nginx apt-cacher-ng
for _ in $(seq 1 30); do if health 2>/dev/null; then break; fi; sleep 1; done
health
for _ in 1 2 3; do
    systemctl restart apt-cacher-ng
    sleep 1
    curl -fsS --max-time 20 --proxy http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o "$work/debian-release"
done
./remove.sh --purge -y
[ ! -e /opt/local-repo ]
[ ! -e /etc/local-repo ]
[ ! -e /srv/local-repo ]
[ ! -e /usr/local/lib/local-repo/acng ]
rpm -q nginx firewalld >/dev/null
[ "$original_nginx" = "$(sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1)" ]
[ "$original_bool" = "$(getsebool httpd_can_network_connect)" ]
if firewall-cmd --query-service=local-repo >/dev/null 2>&1; then exit 1; fi
[ "$(getenforce)" = Enforcing ]
rm -rf "$work"
echo "Serveur natif Rocky $VERSION_ID : validation réussie."
