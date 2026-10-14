#!/bin/sh
# local-repo : Flatpak via le serveur local (Flathub en cache + dépôt local de l'entreprise).
#   curl -fsSL {{ base }}/flatpak/setup.sh | sudo sh
#   ... | sudo sh -s -- --remove     Flathub en accès direct, dépôt local retiré
# Les signatures GPG (clé de Flathub, clé du dépôt local) restent vérifiées par Flatpak.
set -e
[ "$(id -u)" -eq 0 ] || { echo "Lancez ce script en root (sudo)." >&2; exit 1; }
BASE="{{ base }}"
FLATHUB="{{ flathub_url }}"

if [ "${1:-}" = "--remove" ]; then
    if command -v flatpak >/dev/null 2>&1; then
        if flatpak remotes --system --columns=name | grep -qx flathub; then
            flatpak remote-modify --system flathub --url="$FLATHUB"
        fi
        flatpak remote-delete --system --force local-repo 2>/dev/null || true
    fi
    echo "Configuration Flatpak local-repo retirée (Flathub en accès direct)."
    exit 0
fi
[ -z "${1:-}" ] || { echo "Option inconnue : $1" >&2; exit 1; }

if ! command -v flatpak >/dev/null 2>&1; then
    apt-get update -q && DEBIAN_FRONTEND=noninteractive apt-get install -y -q flatpak
fi
if command -v curl >/dev/null 2>&1; then GET="curl -fsSL"; else GET="wget -qO-"; fi
TMP=$(mktemp --suffix=.flatpakrepo)
trap 'rm -f "$TMP"' EXIT

# Flathub : dépôt officiel (clé de Flathub), téléchargements via le cache.
$GET "$BASE/flathub/repo/flathub.flatpakrepo" > "$TMP"
flatpak remote-add --system --if-not-exists flathub "$TMP"
flatpak remote-modify --system flathub --url="$BASE/flathub/repo/"

if [ '{{ "1" if local else "0" }}' = 1 ]; then
# Dépôt local de l'entreprise (clé local-repo).
$GET "$BASE/flatpak/local-repo.flatpakrepo" > "$TMP"
flatpak remote-add --system --if-not-exists local-repo "$TMP"
flatpak remote-modify --system local-repo --url="$BASE/flatpak/repo/"
fi

flatpak remotes --system --show-details
echo "Flatpak configuré pour local-repo ($BASE)."
