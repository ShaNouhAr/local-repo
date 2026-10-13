#!/bin/bash
# local-repo : désinstallation.
#
#   sudo ./remove.sh            retire les services, la configuration et les paquets installés par local-repo,
#                               mais garde les données (logiciels, paquets .deb, caches, clé de signature)
#   sudo ./remove.sh --purge    supprime aussi toutes les données
#   -y                          ne pas demander de confirmation
set -euo pipefail

ETC=/etc/local-repo
STATE=/var/lib/local-repo
PREFIX=/opt/local-repo

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "lancez ce script en root (sudo ./remove.sh)."

PURGE=0 YES=0
for arg in "$@"; do
    case "$arg" in
        --purge) PURGE=1 ;;
        -y|--yes) YES=1 ;;
        -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "option inconnue : $arg" ;;
    esac
done

DATA=/srv/local-repo
# shellcheck disable=SC1091
[ -f "$ETC/local-repo.conf" ] && . "$ETC/local-repo.conf"
cd /

if [ "$YES" = 0 ]; then
    if [ "$PURGE" = 1 ]; then
        echo "local-repo va être désinstallé ET toutes ses données supprimées ($DATA, $STATE, cache apt-cacher-ng)."
    else
        echo "local-repo va être désinstallé (les données dans $DATA et $STATE sont conservées)."
    fi
    read -r -p "Continuer ? [o/N] " answer < /dev/tty
    [[ "$answer" =~ ^[oOyY]$ ]] || { echo "Annulé."; exit 1; }
fi

say "Arrêt des services"
systemctl disable --now local-repo-portal local-repo-stats.timer local-repo-nginx.path local-repo-backup.timer \
    >/dev/null 2>&1 || true
rm -f /etc/systemd/system/local-repo-portal.service /etc/systemd/system/local-repo-stats.service \
      /etc/systemd/system/local-repo-stats.timer /etc/systemd/system/local-repo-nginx.service \
      /etc/systemd/system/local-repo-nginx.path /etc/systemd/system/local-repo-backup.service \
      /etc/systemd/system/local-repo-backup.timer
rm -rf /etc/systemd/system/apt-cacher-ng.service.d/local-repo.conf
rmdir /etc/systemd/system/apt-cacher-ng.service.d 2>/dev/null || true
rm -f /etc/apt-cacher-ng/zz-local-repo.conf /usr/local/sbin/local-repo-user /usr/local/sbin/local-repo-backup /usr/local/sbin/local-repo-apt
for f in /etc/hosts.allow /etc/hosts.deny; do
    if [ -f "$f" ]; then sed -i '/# local-repo$/d' "$f"; fi
done
systemctl daemon-reload

say "Configuration nginx"
rm -f /etc/nginx/sites-enabled/local-repo-portal.conf /etc/nginx/sites-available/local-repo-portal.conf \
      /etc/nginx/sites-enabled/local-repo-wucache.conf /etc/nginx/sites-available/local-repo-wucache.conf \
      /etc/nginx/modules-enabled/99-local-repo-stream.conf /etc/nginx/snippets/local-repo-clients.conf \
      /etc/nginx/snippets/local-repo-admin.conf /etc/nginx/snippets/local-repo-proxy.conf \
      /etc/nginx/snippets/local-repo-flathub-proxy.conf /etc/nginx/snippets/local-repo-rpm-proxy.conf
if [ -f "$STATE/nginx-default-disabled" ]; then
    [ -e /etc/nginx/sites-available/default ] && ln -sf ../sites-available/default /etc/nginx/sites-enabled/default
    rm -f "$STATE/nginx-default-disabled"
fi

# Paquets installés par local-repo uniquement (ceux déjà présents avant restent en place).
PKGS=()
if [ -f "$STATE/installed-packages" ]; then
    mapfile -t PKGS < "$STATE/installed-packages"
fi
if [ ${#PKGS[@]} -gt 0 ]; then
    say "Désinstallation des paquets : ${PKGS[*]}"
    DEBIAN_FRONTEND=noninteractive apt-get purge -y -q "${PKGS[@]}" >/dev/null || true
    DEBIAN_FRONTEND=noninteractive apt-get autoremove --purge -y -q >/dev/null || true
fi
if systemctl is-active -q nginx 2>/dev/null; then
    if nginx -t >/dev/null 2>&1; then systemctl reload nginx; fi
fi
if systemctl is-active -q apt-cacher-ng 2>/dev/null; then systemctl restart apt-cacher-ng || true; fi

say "Suppression du programme"
rm -rf "$PREFIX" "$PREFIX.new"

if [ "$PURGE" = 1 ]; then
    say "Suppression des données"
    case "$DATA" in /|/srv|/var|/home|/opt|"") die "dossier de données suspect : '$DATA'" ;; esac
    rm -rf "$DATA" "$STATE" "$ETC"
    rm -f /var/log/nginx/local-repo-*.log* /var/log/local-repo-install.log
    # Cache et journaux d'apt-cacher-ng seulement s'il avait été installé par local-repo.
    if printf '%s\n' "${PKGS[@]}" | grep -qx apt-cacher-ng; then
        rm -rf /var/cache/apt-cacher-ng /var/log/apt-cacher-ng
    fi
    userdel localrepo 2>/dev/null || true
    BACKUP_DIR=${BACKUP_DIR:-/var/backups/local-repo}
    if [ -d "$BACKUP_DIR" ]; then
        echo "    Sauvegardes conservées : $BACKUP_DIR (à supprimer à la main si inutiles)"
    fi
else
    echo "    Données conservées : $DATA, $STATE, $ETC (sudo ./remove.sh --purge pour tout effacer)"
fi

say "local-repo est désinstallé"
