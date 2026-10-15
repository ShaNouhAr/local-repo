#!/bin/bash
# Raccourci vers l'installateur Linux (linux/install.sh) : garde « sudo ./install.sh » et les mises à jour
# des installations existantes. Serveur Windows : windows\install.ps1.
exec "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/linux/install.sh" "$@"
