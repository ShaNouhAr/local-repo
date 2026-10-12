#!/bin/sh
# local-repo : configure ce poste Debian/Ubuntu pour utiliser le dépôt local.
#   curl -fsSL {{ public_url }}/linux/setup.sh | sudo sh
#   ... | sudo sh -s -- --no-proxy   (dépôt local seulement, sans cache apt-cacher-ng)
#   ... | sudo sh -s -- --remove     (annule la configuration)
set -e
[ "$(id -u)" -eq 0 ] || { echo "Lancez ce script en root (sudo)." >&2; exit 1; }

KEY=/etc/apt/keyrings/local-repo.gpg
LIST=/etc/apt/sources.list.d/local-repo.list
PROXY=/etc/apt/apt.conf.d/01local-repo-proxy

if [ "$1" = "--remove" ]; then
    rm -f "$KEY" "$LIST" "$PROXY"
    echo "Configuration local-repo retirée."
    exit 0
fi

if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; else GET="wget -qO-"; fi
mkdir -p /etc/apt/keyrings
$GET {{ public_url }}/apt/local-repo.gpg > "$KEY"
chmod 644 "$KEY"
echo "deb [signed-by=$KEY] {{ public_url }}/apt ./" > "$LIST"

if [ "$1" = "--no-proxy" ]; then
    rm -f "$PROXY"
else
    cat > "$PROXY" <<CONF
Acquire::http::Proxy "http://{{ host }}:{{ acng_port }}";
Acquire::http::Proxy::{{ host }} "DIRECT";
CONF
fi

apt-get update
echo "Poste configuré pour local-repo ({{ public_url }})."
