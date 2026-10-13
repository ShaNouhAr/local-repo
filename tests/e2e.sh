#!/bin/bash
# VM JETABLE uniquement : installation, migration, clients, isolation, restauration, désinstallation.
set -euo pipefail
cd "$(dirname "$0")/.."
PW="E2e-Password-$(date +%s)"
work=$(mktemp -d)
trap 'sudo rm -rf -- "$work"' EXIT
trap 'echo "Échec du test ligne $LINENO : $BASH_COMMAND" >&2' ERR
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }
ready() { curl -fsk --retry 10 --retry-connrefused --retry-delay 1 --max-time 5 https://127.0.0.1:8443/healthz; }
cli() { (cd /opt/local-repo/portal && sudo -u localrepo env LR_DATA=/srv/local-repo LR_STATE=/var/lib/local-repo LR_ETC=/etc/local-repo python3 -m localrepo.cli "$@"); }
build_deb() {
    local kind=$1
    mkdir -p "$work/$kind/DEBIAN" "$work/$kind/usr/bin"
    printf '#!/bin/sh\necho local-repo-%s-ok\n' "$kind" > "$work/$kind/usr/bin/lr-e2e"
    chmod 755 "$work/$kind/usr/bin/lr-e2e"
    printf 'Package: lr-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n' > "$work/$kind/DEBIAN/control"
    dpkg-deb --root-owner-group -b "$work/$kind" "$work/$kind.deb" >/dev/null
}
build_deb native
build_deb foreign
build_deb legacy
if [ -n "${LR_E2E_LEGACY_SRC:-}" ]; then
    step "Installation 1.0.0 et ancien dépôt"
    sudo "$LR_E2E_LEGACY_SRC/install.sh" --password "$PW" --no-flatpak
    ready
    python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/legacy.deb" linux legacy
    sudo sha256sum /etc/local-repo/tls/ca.crt | cut -d' ' -f1 > "$work/old-ca"
fi
step "Installation / migration vers la version actuelle"
sudo ./install.sh --password "$PW" --flatpak
ready
# Vérifie aussi les redémarrages successifs du serveur applicatif (nouvelles versions de Gunicorn).
for _ in 1 2 3; do sudo systemctl restart local-repo-portal; ready; done
# shellcheck disable=SC1091
HOST=$(. /etc/local-repo/local-repo.conf && echo "$HOST")
if [ -f "$work/old-ca" ]; then
    [ "$(sudo sha256sum /etc/local-repo/tls/ca.crt | cut -d' ' -f1)" = "$(cat "$work/old-ca")" ]
    sudo cmp "$work/legacy.deb" /srv/local-repo/apt/pool/lr-e2e_1.0_all.deb
    grep -q 'lr-e2e' /srv/local-repo/apt/Packages
fi
sudo /opt/local-repo/bin/local-repo-user list
step "Relance (idempotence)"
sudo ./install.sh >/dev/null
ready
# shellcheck disable=SC1091
. /etc/os-release
DIST=$ID SUITE=${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}
REPO="$DIST/$SUITE"
step "Création du dépôt du poste et d'un dépôt distinct"
sudo local-repo-apt add "$DIST" "$SUITE" "$VERSION_ID"
sudo local-repo-apt add ubuntu isolation-e2e test
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/native.deb" linux "$REPO"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/foreign.deb" linux ubuntu/isolation-e2e
sudo cmp "$work/native.deb" "/srv/local-repo/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
sudo cmp "$work/foreign.deb" /srv/local-repo/apt/repositories/ubuntu/isolation-e2e/pool/lr-e2e_1.0_all.deb
step "Poste : détection automatique, source deb822, signatures et HTTPS"
curl -fsSL "http://$HOST:8088/linux/setup.sh" > "$work/setup.sh"
sudo sh "$work/setup.sh" --trust-ca
curl -fsSL "https://$HOST:8443/linux/setup.sh" > "$work/setup.sh"
SOURCE=/etc/apt/sources.list.d/local-repo.sources
grep -q "https://$HOST:8443/apt/repositories/$REPO/" "$SOURCE"
[ ! -e /etc/apt/sources.list.d/local-repo.list ]
curl -fsS "https://$HOST:8443/healthz" >/dev/null
sudo apt-get install -y -q lr-e2e >/dev/null
[ "$(lr-e2e)" = local-repo-native-ok ]
step "Version absente : échec sans changer le dépôt en place"
sudo cp "$SOURCE" "$work/source-before"
if sudo sh "$work/setup.sh" --distribution debian --suite not-created; then echo 'Une version absente doit être refusée.' >&2; exit 1; fi
sudo cmp "$SOURCE" "$work/source-before"
step "Cache seul et retour au dépôt détecté"
sudo sh "$work/setup.sh" --cache-only >/dev/null
[ ! -e "$SOURCE" ]
[ -e /etc/apt/apt.conf.d/01local-repo-proxy ]
sudo sh "$work/setup.sh" >/dev/null
if [ -f "$work/old-ca" ]; then
    step "Compatibilité explicite du dépôt historique"
    sudo apt-get purge -y -q lr-e2e >/dev/null
    sudo sh "$work/setup.sh" --legacy >/dev/null
    sudo apt-get install -y -q lr-e2e >/dev/null
    [ "$(lr-e2e)" = local-repo-legacy-ok ]
    sudo apt-get purge -y -q lr-e2e >/dev/null
    sudo sh "$work/setup.sh" >/dev/null
    sudo apt-get install -y -q lr-e2e >/dev/null
    [ "$(lr-e2e)" = local-repo-native-ok ]
fi
step "Flatpak : application locale et cache Flathub"
fp="$work/fp"; arch=$(flatpak --default-arch)
mkdir -p "$fp/app/files/bin" "$fp/app/export"
printf '#!/bin/sh\necho local-repo-flatpak-ok\n' > "$fp/app/files/bin/lr-e2e"
chmod 755 "$fp/app/files/bin/lr-e2e"
printf '[Application]\nname=org.localrepo.E2e\nruntime=org.freedesktop.Platform/%s/24.08\nsdk=org.freedesktop.Sdk/%s/24.08\ncommand=lr-e2e\n' "$arch" "$arch" > "$fp/app/metadata"
ostree init --mode=archive-z2 --repo="$fp/repo"
flatpak build-export "$fp/repo" "$fp/app" >/dev/null
flatpak build-bundle "$fp/repo" "$fp/e2e.flatpak" org.localrepo.E2e
python3 tests/e2e_portal.py "https://127.0.0.1:8443" "$PW" "$fp/e2e.flatpak" flatpak
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh >/dev/null
flatpak remote-ls --system local-repo --columns=application | grep -qx org.localrepo.E2e
sudo flatpak install --system --noninteractive --no-deps local-repo org.localrepo.E2e >/dev/null
flatpak info --system org.localrepo.E2e | grep -q local-repo
flatpak remote-info --system flathub org.flatpak.Builder >/dev/null
curl -fsS "https://$HOST:8443/flathub/repo/summary.idx" -o /dev/null
sudo flatpak uninstall --system --noninteractive org.localrepo.E2e >/dev/null
curl -fsSL "https://$HOST:8443/flatpak/setup.sh" | sudo sh -s -- --remove >/dev/null
step "Contrôles de sécurité"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:8088/")" = 301 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ]
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/acng-report.html?doCount=1)" = 401 ]
curl -fsS -x http://127.0.0.1:3142 http://deb.debian.org/debian/dists/stable/Release -o /dev/null
curl -sk -D - -o /dev/null https://127.0.0.1:8443/login | grep -qi "content-security-policy: default-src 'none'"
step "Sauvegarde et restauration des dépôts / comptes / signatures"
sudo local-repo-backup create "$work/backups"
archive=$(sudo find "$work/backups" -name '*.tar.gz' -type f | head -1)
sudo rm "/srv/local-repo/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
cli rebuild-apt
sudo local-repo-backup restore "$archive"
sudo ./install.sh >/dev/null
ready
sudo cmp "$work/native.deb" "/srv/local-repo/apt/repositories/$REPO/pool/lr-e2e_1.0_all.deb"
sudo local-repo-apt list | grep -q "$REPO"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/native.deb" linux "$REPO"
step "Désinstallation"
sudo apt-get purge -y -q lr-e2e >/dev/null
curl -fsSL "https://$HOST:8443/linux/setup.sh" | sudo sh -s -- --remove
[ ! -e /etc/apt/sources.list.d/local-repo.sources ]
sudo /opt/local-repo/remove.sh -y --purge
fail() { echo "ÉCHEC : $*" >&2; exit 1; }
if systemctl list-unit-files | grep -q local-repo; then fail 'unités systemd restantes'; fi
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail 'ports encore ouverts'; fi
if id localrepo >/dev/null 2>&1; then fail 'utilisateur localrepo restant'; fi
for d in /etc/local-repo /srv/local-repo /opt/local-repo; do
    if [ -e "$d" ]; then fail "$d restant"; fi
done
echo 'Test de bout en bout réussi.'
