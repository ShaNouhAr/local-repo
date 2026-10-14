#!/bin/bash
# VM JETABLE uniquement : déploiement Docker (docker compose), poste client, persistance, sauvegarde.
set -euo pipefail
cd "$(dirname "$0")/.."
PW="E2e-Docker-$(date +%s)"
work=$(mktemp -d)
trap 'sudo rm -rf -- "$work"' EXIT
trap 'echo "Échec du test ligne $LINENO : $BASH_COMMAND" >&2' ERR
step() { printf '\n\033[1m--- %s\033[0m\n' "$*"; }
dc() { sudo docker compose "$@"; }
healthy() {
    local i
    for (( i=0; i<90; i++ )); do
        [ "$(sudo docker inspect -f '{{.State.Health.Status}}' "$(dc ps -q local-repo)")" = healthy ] && return 0
        sleep 4
    done
    dc logs --tail 80 local-repo >&2
    return 1
}
fail() { echo "ÉCHEC : $*" >&2; exit 1; }

mkdir -p "$work/deb/DEBIAN" "$work/deb/usr/bin"
printf '#!/bin/sh\necho local-repo-docker-ok\n' > "$work/deb/usr/bin/lr-e2e"
chmod 755 "$work/deb/usr/bin/lr-e2e"
printf 'Package: lr-e2e\nVersion: 1.0\nArchitecture: all\nMaintainer: ci <ci@example.invalid>\nDescription: test\n' > "$work/deb/DEBIAN/control"
dpkg-deb --root-owner-group -b "$work/deb" "$work/lr-e2e.deb" >/dev/null

step "Construction et premier démarrage"
printf 'LR_BACKUP_KEEP=2\nTZ=Europe/Paris\n' > .env
dc up -d --build
healthy
dc logs local-repo > "$work/first.log"
grep -q "Compte initial     : admin / " "$work/first.log" || fail "mot de passe initial absent du journal"
# shellcheck disable=SC2016  # expansions faites dans le conteneur
HOST=$(dc exec -T local-repo sh -c '. /etc/local-repo/local-repo.conf && echo "$HOST"')
[ -n "$HOST" ] || fail "adresse non détectée"
# shellcheck disable=SC2016
dc exec -T -e LR_PASSWORD="$PW" local-repo sh -c 'cd /opt/local-repo/portal && runuser -u localrepo -- env LR_DATA=/srv/local-repo LR_STATE=/var/lib/local-repo LR_ETC=/etc/local-repo LR_PASSWORD="$LR_PASSWORD" python3 -m localrepo.cli user set-password admin'

step "Processus, comptes et services"
dc exec -T local-repo ps -eo user:16,args > "$work/ps"
grep -E '^localrepo .*gunicorn' "$work/ps" >/dev/null || fail "portail pas lancé sous localrepo"
grep -E '^apt-cacher-ng .*apt-cacher-ng' "$work/ps" >/dev/null || fail "apt-cacher-ng pas lancé sous son compte"
curl -fsk https://127.0.0.1:8443/metrics > "$work/metrics"
for unit in local-repo-portal nginx apt-cacher-ng local-repo-cron; do
    grep -qx "local_repo_service_up{unit=\"$unit\"} 1" "$work/metrics" || fail "service inactif : $unit"
done
for (( i=0; i<20; i++ )); do dc exec -T local-repo test -s /var/lib/local-repo/stats.json && break; sleep 2; done
dc exec -T local-repo test -s /var/lib/local-repo/stats.json || fail "tâches périodiques inactives"

step "Dépôt APT, poste client via le cache"
# shellcheck disable=SC1091
. /etc/os-release
dc exec -T local-repo local-repo-apt add "$ID" "$VERSION_CODENAME" "$VERSION_ID"
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/lr-e2e.deb" linux "$ID/$VERSION_CODENAME"
curl -fsSL "http://$HOST:8088/linux/setup.sh" | sudo sh -s -- --trust-ca
sudo apt-get install -y -q lr-e2e >/dev/null
[ "$(lr-e2e)" = local-repo-docker-ok ] || fail "paquet du dépôt local"
grep -q "https://$HOST:8443/apt/repositories/$ID/$VERSION_CODENAME/" /etc/apt/sources.list.d/local-repo.sources

step "Famille Red Hat, Flatpak, Windows Update, sécurité"
curl -fsS "https://$HOST:8443/rpm/el9/repodata/repomd.xml.asc" -o /dev/null
curl -fsS "https://$HOST:8443/rpm/local-repo.asc" | grep -q "BEGIN PGP PUBLIC KEY BLOCK"
curl -fsS "https://$HOST:8443/rpmcache/rocky/9/BaseOS/x86_64/os/repodata/repomd.xml" -o /dev/null
curl -fsS "https://$HOST:8443/flatpak/repo/config" -o /dev/null
curl -fsS "https://$HOST:8443/flathub/repo/summary.idx" -o /dev/null
[ "$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: example.com' http://127.0.0.1/)" = 403 ] || fail "relais Windows Update ouvert"
[ "$(curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:3142/acng-report.html?doCount=1)" = 401 ] || fail "rapport apt-cacher-ng accessible"
[ "$(curl -s -o /dev/null -w '%{http_code}' "http://$HOST:8088/")" = 301 ] || fail "HTTP doit renvoyer vers HTTPS"
curl -sk -D - -o /dev/null https://127.0.0.1:8443/login | grep -qi "content-security-policy: default-src 'none'"

step "Sauvegarde"
dc exec -T local-repo local-repo-backup create | tee "$work/backup"
dc exec -T local-repo local-repo-backup list | grep -q "local-repo-.*\.tar\.gz"

step "Recréation du conteneur : données, comptes et autorité conservés"
CA_BEFORE=$(curl -fsS "http://$HOST:8088/ca.crt" | sha256sum | cut -d' ' -f1)
dc down
dc up -d
healthy
[ "$(curl -fsS "http://$HOST:8088/ca.crt" | sha256sum | cut -d' ' -f1)" = "$CA_BEFORE" ] || fail "autorité HTTPS régénérée"
if dc logs local-repo | grep -q "Compte initial"; then fail "nouveau compte initial après recréation"; fi
python3 tests/e2e_portal.py https://127.0.0.1:8443 "$PW" "$work/lr-e2e.deb" linux "$ID/$VERSION_CODENAME"
sudo apt-get update -q >/dev/null
sudo apt-get install -y -q --reinstall lr-e2e >/dev/null

step "Réglages par variables : sans Windows Update ni HTTP"
printf 'LR_BACKUP_KEEP=2\nLR_WU=0\nLR_HTTP=0\n' > .env
dc up -d
healthy
if ss -Hltn | grep -qE ':(80|443|8088) '; then fail "ports 80, 443 ou 8088 encore ouverts"; fi
grep -q "WU_ENABLED=0" <(dc exec -T local-repo cat /etc/local-repo/local-repo.conf)
printf 'LR_BACKUP_KEEP=2\n' > .env
dc up -d
healthy
ss -Hltn | grep -qE ':80 ' || fail "Windows Update non réactivé quand la variable est retirée"

step "Nettoyage"
sudo apt-get purge -y -q lr-e2e >/dev/null
curl -fsSL "https://$HOST:8443/linux/setup.sh" | sudo sh -s -- --remove
dc down -v
rm -f .env
if ss -Hltn | grep -qE ':(8443|8088|3142) '; then fail "ports encore ouverts"; fi
echo 'Test Docker de bout en bout réussi.'
