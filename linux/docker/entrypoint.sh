#!/bin/bash
# local-repo (image Docker) : à chaque démarrage, applique les variables d'environnement avec
# install.sh --container (même configuration que l'installation native), puis lance supervisord.
# Les variables font foi : une variable retirée revient à la valeur par défaut.
# Avec une commande (docker compose run --rm local-repo bash), l'exécute à la place.
set -euo pipefail

if [ $# -gt 0 ]; then exec "$@"; fi

PRIVATE_NETS="10.0.0.0/8 172.16.0.0/12 192.168.0.0/16 fc00::/7 fe80::/10"

flag() {   # VARIABLE option-si-1 option-si-0 (défaut : 1)
    case "${!1:-1}" in
        1|yes|true|on) args+=("$2") ;;
        0|no|false|off) args+=("$3") ;;
        *) echo "$1 : 1 ou 0 attendu (valeur : ${!1})." >&2; exit 1 ;;
    esac
}

args=(--container
      --host "${LR_HOST:-}"
      --https-port "${LR_HTTPS_PORT:-8443}"
      --http-port "${LR_HTTP_PORT:-8088}"
      --allow "${LR_ALLOW:-$PRIVATE_NETS}"
      --admin-allow "${LR_ADMIN_ALLOW:-${LR_ALLOW:-$PRIVATE_NETS}}"
      --resolvers "${LR_RESOLVERS:-1.1.1.1 9.9.9.9}"
      --max-upload "${LR_MAX_UPLOAD:-8g}"
      --backup-keep "${LR_BACKUP_KEEP:-3}")
flag LR_HTTP --http --no-http
flag LR_WU --wu --no-wu
flag LR_FLATPAK --flatpak --no-flatpak
flag LR_RPM --rpm --no-rpm
if [ -n "${LR_PROXY:-}" ]; then args+=(--proxy "$LR_PROXY"); else args+=(--no-proxy); fi
# Tailles des caches : vides, elles sont calculées au premier démarrage puis conservées.
if [ -n "${LR_WU_CACHE_SIZE:-}" ]; then args+=(--wu-cache-size "$LR_WU_CACHE_SIZE"); fi
if [ -n "${LR_FLATPAK_CACHE_SIZE:-}" ]; then args+=(--flatpak-cache-size "$LR_FLATPAK_CACHE_SIZE"); fi
if [ -n "${LR_RPM_CACHE_SIZE:-}" ]; then args+=(--rpm-cache-size "$LR_RPM_CACHE_SIZE"); fi
# Certificat d'entreprise : fichiers montés en lecture seule dans le conteneur.
if [ -n "${LR_TLS_CERT:-}${LR_TLS_KEY:-}" ]; then args+=(--tls-cert "${LR_TLS_CERT:-}" --tls-key "${LR_TLS_KEY:-}"); fi

/usr/src/local-repo/linux/install.sh "${args[@]}"
exec /usr/bin/supervisord -c /etc/supervisor/local-repo.conf
