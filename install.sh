#!/bin/sh
# Raccourci vers l'installateur Linux (linux/install.sh) : garde « sudo ./install.sh » et les mises à jour
# des installations existantes. Serveur Windows : windows\install.ps1.
dir=$(cd "$(dirname "$0")" && pwd)
# Alpine minimal : bash n'est pas installé par défaut.
if ! command -v bash >/dev/null 2>&1 && [ -f /etc/alpine-release ]; then
    apk add --no-progress bash >/dev/null || { echo "bash requis (apk add bash)." >&2; exit 1; }
fi
exec bash "$dir/linux/install.sh" "$@"
