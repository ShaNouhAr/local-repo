#!/bin/bash
# local-repo : installe le dépôt local (paquets Linux, Windows Update, logiciels Windows)
# sur Debian / Ubuntu, sans Docker. Relançable sans risque pour mettre à jour.
#
#   sudo ./install.sh [options]
#
#   --port N            port du portail web (défaut : 8088)
#   --host IP           adresse annoncée aux clients (défaut : IP de la route par défaut)
#   --data DIR          dossier des données et des caches (défaut : /srv/local-repo)
#   --wu-cache-size T   taille max du cache Windows Update, ex. 50g (défaut : moitié de l'espace libre, max 200g)
#   --resolvers "A B"   DNS publics utilisés par le cache (défaut : "1.1.1.1 9.9.9.9")
#   --password MDP      définit le mot de passe du portail (sinon généré à la première installation)
#   --no-wu             n'installe pas le cache Windows Update (libère les ports 80 et 443)
#   --wu                le réactive après un --no-wu
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd /
PREFIX=/opt/local-repo
ETC=/etc/local-repo
STATE=/var/lib/local-repo
APP_PORT=8089
PACKAGES=(nginx libnginx-mod-stream apt-cacher-ng apt-utils gnupg python3-flask gunicorn curl)

say()  { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[1;31mErreur :\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(id -u)" -eq 0 ] || die "lancez ce script en root (sudo ./install.sh)."
command -v apt-get >/dev/null || die "Debian ou Ubuntu requis (apt-get introuvable)."
command -v systemctl >/dev/null || die "systemd requis."

# Valeurs déjà installées (relance) puis options de la ligne de commande.
PORTAL_PORT=8088 HOST="" DATA=/srv/local-repo WU_CACHE_SIZE="" RESOLVERS="1.1.1.1 9.9.9.9" WU_ENABLED=1
# shellcheck disable=SC1091
[ -f "$ETC/local-repo.conf" ] && . "$ETC/local-repo.conf"
PASSWORD=""
while [ $# -gt 0 ]; do
    case "$1" in
        --port) PORTAL_PORT="$2"; shift 2 ;;
        --host) HOST="$2"; shift 2 ;;
        --data) DATA="$2"; shift 2 ;;
        --wu-cache-size) WU_CACHE_SIZE="$2"; shift 2 ;;
        --resolvers) RESOLVERS="$2"; shift 2 ;;
        --password) PASSWORD="$2"; shift 2 ;;
        --no-wu) WU_ENABLED=0; shift ;;
        --wu) WU_ENABLED=1; shift ;;
        -h|--help) sed -n '2,16p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) die "option inconnue : $1 (voir --help)" ;;
    esac
done

[[ "$PORTAL_PORT" =~ ^[0-9]+$ ]] || die "--port doit être un nombre."
if [ -z "$HOST" ]; then
    HOST=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
fi
[ -n "$HOST" ] || die "impossible de détecter l'IP du serveur, utilisez --host."
PUBLIC_URL="http://$HOST:$PORTAL_PORT"

# ---------------------------------------------------------------- vérification des ports
port_owner() { ss -Hltnp "sport = :$1" 2>/dev/null | grep -o 'users:(("[^"]*"' | head -1 | cut -d'"' -f2 || true; }
check_port() {
    local owner
    owner=$(port_owner "$1")
    if [ -n "$owner" ] && [ "$owner" != "$2" ]; then
        die "le port $1 est déjà utilisé par « $owner »."
    fi
}
say "Vérification des ports"
check_port "$PORTAL_PORT" nginx
check_port 3142 apt-cacher-ng
check_port "$APP_PORT" gunicorn
if [ "$WU_ENABLED" = 1 ]; then
    check_port 80 nginx
    check_port 443 nginx
fi

# ---------------------------------------------------------------- paquets
say "Installation des paquets"
mkdir -p "$STATE"
touch "$STATE/installed-packages"
for p in "${PACKAGES[@]}"; do
    if ! dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed"; then
        grep -qx "$p" "$STATE/installed-packages" || echo "$p" >> "$STATE/installed-packages"
    fi
done
echo "apt-cacher-ng apt-cacher-ng/tunnelenable boolean false" | debconf-set-selections
export DEBIAN_FRONTEND=noninteractive
apt-get update -q
apt-get install -y -q --no-install-recommends "${PACKAGES[@]}"

# ---------------------------------------------------------------- utilisateur et dossiers
say "Utilisateur et dossiers"
id localrepo >/dev/null 2>&1 || useradd --system --home-dir "$STATE" --shell /usr/sbin/nologin localrepo
install -d -m 755 -o localrepo -g localrepo "$DATA" "$DATA/apt" "$DATA/apt/pool" "$DATA/win"
install -d -m 700 -o localrepo -g localrepo "$DATA/.tmp" "$STATE/gnupg"
install -d -m 755 -o localrepo -g localrepo "$STATE"
install -d -m 700 -o www-data -g www-data "$DATA/wucache"
chown -R localrepo:localrepo "$DATA/apt" "$DATA/win" "$STATE"
install -d -m 755 "$ETC"

if [ -z "$WU_CACHE_SIZE" ]; then
    free_g=$(df -BG --output=avail "$DATA" | tail -1 | tr -dc '0-9')
    WU_CACHE_SIZE="$(( free_g / 2 > 200 ? 200 : (free_g / 2 < 5 ? 5 : free_g / 2) ))g"
fi

cat > "$ETC/local-repo.conf" <<EOF
# Généré par install.sh : modifiez puis relancez sudo ./install.sh pour appliquer.
PORTAL_PORT=$PORTAL_PORT
HOST=$HOST
DATA=$DATA
WU_CACHE_SIZE=$WU_CACHE_SIZE
RESOLVERS="$RESOLVERS"
WU_ENABLED=$WU_ENABLED
EOF

# ---------------------------------------------------------------- code
say "Copie du portail dans $PREFIX"
rm -rf "$PREFIX.new"
install -d "$PREFIX.new"
cp -r "$SRC/portal" "$SRC/bin" "$SRC/conf" "$SRC/remove.sh" "$PREFIX.new/"
find "$PREFIX.new" -name __pycache__ -prune -exec rm -rf {} +
chmod 755 "$PREFIX.new/bin/"* "$PREFIX.new/remove.sh"
rm -rf "$PREFIX"
mv "$PREFIX.new" "$PREFIX"

# ---------------------------------------------------------------- secrets
GENERATED=""
if [ -z "$PASSWORD" ] && [ ! -f "$ETC/portal.secret" ]; then
    PASSWORD=$(python3 -c 'import secrets; print(secrets.token_urlsafe(12))')
    GENERATED=1
fi
if [ -n "$PASSWORD" ]; then
    say "Mot de passe du portail"
    LR_PASSWORD="$PASSWORD" python3 - "$ETC/portal.secret" <<'EOF'
import json, os, secrets, sys
from werkzeug.security import generate_password_hash
path = sys.argv[1]
try:
    data = json.load(open(path))
except (OSError, ValueError):
    data = {}
data.setdefault("secret_key", secrets.token_hex(32))
data["admin_hash"] = generate_password_hash(os.environ["LR_PASSWORD"])
with open(path, "w") as f:
    json.dump(data, f)
EOF
    if [ -n "$GENERATED" ]; then
        umask 077
        echo "$PASSWORD" > "$ETC/admin-password"
        umask 022
    else
        rm -f "$ETC/admin-password"
    fi
fi
chown root:localrepo "$ETC/portal.secret"
chmod 640 "$ETC/portal.secret"

# ---------------------------------------------------------------- clé de signature APT
if ! runuser -u localrepo -- env GNUPGHOME="$STATE/gnupg" gpg --list-secret-keys 2>/dev/null | grep -q sec; then
    say "Création de la clé de signature du dépôt APT"
    runuser -u localrepo -- env GNUPGHOME="$STATE/gnupg" gpg --batch --quiet --pinentry-mode loopback --passphrase '' \
        --quick-gen-key "local-repo ($(hostname)) <localrepo@$(hostname)>" rsa4096 sign never
fi
runuser -u localrepo -- env GNUPGHOME="$STATE/gnupg" gpg --batch --yes --export -o "$DATA/apt/local-repo.gpg"
chmod 644 "$DATA/apt/local-repo.gpg"
runuser -u localrepo -- env LR_DATA="$DATA" LR_STATE="$STATE" python3 "$PREFIX/portal/app.py" rebuild-apt >/dev/null
runuser -u localrepo -- gpgconf --homedir "$STATE/gnupg" --kill gpg-agent 2>/dev/null || true

# ---------------------------------------------------------------- nginx
say "Configuration de nginx"
render() {
    sed -e "s|@PORTAL_PORT@|$PORTAL_PORT|g" -e "s|@APP_PORT@|$APP_PORT|g" -e "s|@DATA@|$DATA|g" \
        -e "s|@HOST@|$HOST|g" -e "s|@HOSTNAME@|$(hostname)|g" -e "s|@PUBLIC_URL@|$PUBLIC_URL|g" \
        -e "s|@WU_CACHE_SIZE@|$WU_CACHE_SIZE|g" -e "s|@RESOLVERS@|$RESOLVERS|g" "$1"
}
render "$PREFIX/conf/nginx-portal.conf" > /etc/nginx/sites-available/local-repo-portal.conf
ln -sf ../sites-available/local-repo-portal.conf /etc/nginx/sites-enabled/local-repo-portal.conf
if [ "$WU_ENABLED" = 1 ]; then
    # Le cache Windows Update doit être le serveur par défaut du port 80.
    if [ -L /etc/nginx/sites-enabled/default ]; then
        rm /etc/nginx/sites-enabled/default
        touch "$STATE/nginx-default-disabled"
    fi
    render "$PREFIX/conf/nginx-wucache.conf" > /etc/nginx/sites-available/local-repo-wucache.conf
    ln -sf ../sites-available/local-repo-wucache.conf /etc/nginx/sites-enabled/local-repo-wucache.conf
    render "$PREFIX/conf/nginx-stream.conf" > /etc/nginx/modules-enabled/99-local-repo-stream.conf
else
    rm -f /etc/nginx/sites-enabled/local-repo-wucache.conf /etc/nginx/sites-available/local-repo-wucache.conf \
          /etc/nginx/modules-enabled/99-local-repo-stream.conf
fi
nginx -t 2>&1 | grep -v -e "syntax is ok" -e "test is successful" || true
nginx -t >/dev/null 2>&1 || die "configuration nginx invalide (voir ci-dessus)."

# ---------------------------------------------------------------- services
say "Services"
render "$PREFIX/conf/local-repo-portal.service" > /etc/systemd/system/local-repo-portal.service
cp "$PREFIX/conf/local-repo-stats.service" "$PREFIX/conf/local-repo-stats.timer" /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now apt-cacher-ng nginx >/dev/null 2>&1
systemctl reload nginx
systemctl enable local-repo-portal local-repo-stats.timer >/dev/null 2>&1
systemctl restart local-repo-portal
systemctl start local-repo-stats.timer local-repo-stats.service

for _ in $(seq 1 20); do
    curl -fs "http://127.0.0.1:$PORTAL_PORT/healthz" >/dev/null 2>&1 && break
    sleep 0.5
done
curl -fs "http://127.0.0.1:$PORTAL_PORT/healthz" >/dev/null || die "le portail ne répond pas (journalctl -u local-repo-portal)."

# ---------------------------------------------------------------- résumé
echo
say "local-repo est installé"
echo "    Portail            : $PUBLIC_URL"
if [ -n "$GENERATED" ]; then
    echo "    Mot de passe       : $PASSWORD   (copie dans $ETC/admin-password)"
elif [ -n "$PASSWORD" ]; then
    echo "    Mot de passe       : modifié"
fi
echo "    Cache paquets      : http://$HOST:3142"
echo "    Postes Linux       : curl -fsSL $PUBLIC_URL/linux/setup.sh | sudo sh"
if [ "$WU_ENABLED" = 1 ]; then
    echo "    Cache Windows Upd. : $WU_CACHE_SIZE max, DNS à configurer depuis le portail"
fi
echo "    Désinstaller       : sudo $PREFIX/remove.sh"
