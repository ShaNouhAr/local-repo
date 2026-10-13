#!/bin/sh
# local-repo : configure ce poste Debian / Ubuntu pour utiliser le dépôt local.
#   curl -fsSL {{ base }}/linux/setup.sh | sudo sh
#   ... | sudo sh -s -- --trust-ca   importe l'autorité de certification local-repo et passe le dépôt en HTTPS
#   ... | sudo sh -s -- --no-proxy   dépôt local seulement, sans le cache apt-cacher-ng
#   ... | sudo sh -s -- --remove     annule la configuration (dépôt, cache, autorité)
set -e
[ "$(id -u)" -eq 0 ] || { echo "Lancez ce script en root (sudo)." >&2; exit 1; }

BASE="{{ base }}"
HTTPS_BASE="{{ https_url }}"
KEY=/etc/apt/keyrings/local-repo.gpg
LIST=/etc/apt/sources.list.d/local-repo.list
PROXY=/etc/apt/apt.conf.d/01local-repo-proxy
CA=/usr/local/share/ca-certificates/local-repo.crt

USE_PROXY=1 TRUST_CA=0 REMOVE=0
for arg in "$@"; do
    case "$arg" in
        --no-proxy) USE_PROXY=0 ;;
        --trust-ca) TRUST_CA=1 ;;
        --remove) REMOVE=1 ;;
        *) echo "Option inconnue : $arg" >&2; exit 1 ;;
    esac
done

if [ "$REMOVE" = 1 ]; then
    rm -f "$KEY" "$LIST" "$PROXY"
    if [ -f "$CA" ]; then rm -f "$CA"; update-ca-certificates --fresh >/dev/null 2>&1 || true; fi
    echo "Configuration local-repo retirée."
    exit 0
fi

if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; else GET="wget -qO-"; fi

if [ "$TRUST_CA" = 1 ]; then
    command -v update-ca-certificates >/dev/null 2>&1 || { apt-get update -q >/dev/null && apt-get install -y -q ca-certificates >/dev/null; }
    mkdir -p "$(dirname "$CA")"
    $GET "$BASE/ca.crt" > "$CA"
    chmod 644 "$CA"
    update-ca-certificates >/dev/null
    if command -v openssl >/dev/null 2>&1; then
        echo "Autorité importée, empreinte SHA-256 : $(openssl x509 -in "$CA" -noout -fingerprint -sha256 | cut -d= -f2)"
        echo "Comparez-la à celle de la page Système du portail."
    fi
    BASE="$HTTPS_BASE"
fi

mkdir -p /etc/apt/keyrings
$GET "$BASE/apt/local-repo.gpg" > "$KEY"
chmod 644 "$KEY"
echo "deb [signed-by=$KEY] $BASE/apt ./" > "$LIST"

if [ "$USE_PROXY" = 1 ]; then
    cat > "$PROXY" <<CONF
Acquire::http::Proxy "http://{{ host }}:{{ acng_port }}";
Acquire::http::Proxy::{{ host }} "DIRECT";
CONF
else
    rm -f "$PROXY"
fi

apt-get update
echo "Poste configuré pour local-repo ($BASE)."
