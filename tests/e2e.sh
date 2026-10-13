#!/bin/bash
# Test de bout en bout sur une machine JETABLE (CI ou VM de test) : installe local-repo, l'utilise comme
# poste client, sauvegarde, puis désinstalle et vérifie qu'il ne reste rien. Ne pas lancer sur un serveur réel.
set -euo pipefail
cd "$(dirname "$0")/.."
PW="E2e-Password-$(date +%s)"
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }

step "Installation"
sudo ./install.sh --password "$PW"
# shellcheck disable=SC1091
HOST=$(. /etc/local-repo/local-repo.conf && echo "$HOST")
curl -fsk "https://127.0.0.1:8443/healthz"
sudo /opt/local-repo/bin/local-repo-user list

step "Relance (idempotence)"
sudo ./install.sh >/dev/null

step "Envoi d'un paquet par le portail"
work=$(mktemp -d)
mkdir -p "$work/pkg/DEBIAN" "$work/pkg/usr/bin"
printf '#!/bin/sh\necho local-repo-e2e-ok\n' > "$work/pkg/usr/bin/lr-e2e"
chmod 755 "$work/pkg/usr/bin/lr-e2e"
printf 'Package: lr-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n' > "$work/pkg/DEBIAN/control"
dpkg-deb --root-owner-group -b "$work/pkg" "$work/lr-e2e.deb" >/dev/null
python3 tests/e2e_portal.py "https://127.0.0.1:8443" "$PW" "$work/lr-e2e.deb"

step "Poste client : amorçage HTTP + autorité, puis dépôt en HTTPS"
curl -fsSL "http://$HOST:8088/linux/setup.sh" | sudo sh -s -- --trust-ca
grep -q "https://$HOST:8443/apt" /etc/apt/sources.list.d/local-repo.list
curl -fsS "https://$HOST:8443/healthz" >/dev/null        # certificat accepté sans -k
sudo apt-get install -y -q lr-e2e >/dev/null
[ "$(lr-e2e)" = "local-repo-e2e-ok" ]

step "Flatpak : application locale et cache Flathub"
fp="$work/fp"
arch=$(flatpak --default-arch)
mkdir -p "$fp/app/files/bin" "$fp/app/export"
printf '#!/bin/sh\necho local-repo-flatpak-ok\n' > "$fp/app/files/bin/lr-e2e"
chmod 755 "$fp/app/files/bin/lr-e2e"
printf '[Application]\nname=org.localrepo.E2e\nruntime=org.freedesktop.Platform/%s/24.08\nsdk=org.freedesktop.Sdk/%s/24.08\ncommand=lr-e2e\n' \
    "$arch" "$arch" > "$fp/app/metadata"
ostree init --mode=archive-z2 --repo="$fp/repo"
flatpak build-export "$fp/repo" "$fp/app" >/dev/null
flatpak build-bundle "$fp/repo" "$fp/e2e.flatpak" org.localrepo.E2e
python3 tests/e2e_portal.py "https://127.0.0.1:8443" "$PW" "$fp/e2e.flatpak" flatpak
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh >/dev/null
flatpak remote-ls --system local-repo --columns=application | grep -qx org.localrepo.E2e
sudo flatpak install --system --noninteractive --no-deps local-repo org.localrepo.E2e >/dev/null
flatpak info --system org.localrepo.E2e | grep -q "local-repo"
flatpak remote-info --system flathub org.flatpak.Builder >/dev/null    # métadonnées Flathub via le cache
obj=$(curl -fsS "https://$HOST:8443/flathub/repo/summary.idx" -o /dev/null -w '%{http_code}')
[ "$obj" = 200 ]
sudo flatpak uninstall --system --noninteractive org.localrepo.E2e >/dev/null
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh -s -- --remove >/dev/null

step "Contrôles de sécurité"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:8088/")" = 301 ]
[ "$(curl -sk -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:3142/acng-report.html?doCount=1")" = 401 ]
curl -sk -D - -o /dev/null "https://127.0.0.1:8443/login" | grep -qi "content-security-policy: default-src 'none'"

step "Sauvegarde"
sudo local-repo-backup create
sudo local-repo-backup list

step "Désinstallation"
sudo apt-get purge -y -q lr-e2e >/dev/null
curl -fsSL "https://$HOST:8443/linux/setup.sh" | sudo sh -s -- --remove
sudo /opt/local-repo/remove.sh -y --purge
fail() { echo "ÉCHEC : $*" >&2; exit 1; }
if systemctl list-unit-files | grep -q local-repo; then fail "unités systemd restantes"; fi
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail "ports encore ouverts"; fi
if id localrepo >/dev/null 2>&1; then fail "utilisateur localrepo restant"; fi
for d in /etc/local-repo /srv/local-repo /opt/local-repo; do
    if [ -e "$d" ]; then fail "$d restant"; fi
done
rm -rf "$work"
echo
echo "Test de bout en bout réussi."
