#!/bin/sh
# Fabrique une ISO BIOS/UEFI Alpine/BusyBox ; lancer sur Alpine Linux.
# build.sh [--answers F] [--tag v3.24] [--outdir DIR] [--aports DIR] [--source-archive F] [--ssh-key F.pub]
# Sans --answers : assistant interactif. --ssh-key est réservé aux tests du live.
set -eu
TAG=v3.24 OUTDIR="$PWD/out" APORTS="" SSH_KEY="" ANSWERS="" ARCHIVE=""
while [ $# -gt 0 ]; do
    [ $# -ge 2 ] || { echo "Valeur manquante : $1" >&2; exit 1; }
    case "$1" in
        --answers) ANSWERS=$2 ;; --tag) TAG=$2 ;; --outdir) OUTDIR=$2 ;;
        --aports) APORTS=$2 ;; --ssh-key) SSH_KEY=$2 ;; --source-archive) ARCHIVE=$2 ;;
        *) echo "Option inconnue : $1" >&2; exit 1 ;;
    esac
    shift 2
done
HERE=$(cd "$(dirname "$0")" && pwd)
ROOT=$(cd "$HERE/../.." && pwd)
case "$TAG" in v3.24) ;; *) echo 'Branche Alpine validée : v3.24.' >&2; exit 1 ;; esac
[ -f /etc/alpine-release ] || { echo 'Construction sur Alpine requise.' >&2; exit 1; }
[ "$(id -u)" -ne 0 ] || { echo "Lancez le build avec un compte membre du groupe abuild (pas root)." >&2; exit 1; }
for cmd in git xorriso mksquashfs abuild python3; do command -v "$cmd" >/dev/null; done
if [ -n "$ANSWERS" ]; then ANSWERS=$(realpath "$ANSWERS"); test -f "$ANSWERS"; fi
if [ -n "$SSH_KEY" ]; then SSH_KEY=$(realpath "$SSH_KEY"); test -f "$SSH_KEY"; fi
mkdir -p "$OUTDIR"
OUTDIR=$(realpath "$OUTDIR")
WORK=$(mktemp -d -t loco-iso.XXXXXX)
trap 'rm -rf "$WORK"' EXIT
if [ -z "$APORTS" ]; then
    APORTS="$WORK/aports"
    git clone --depth 1 --branch 3.24-stable --filter=blob:none --sparse https://github.com/alpinelinux/aports.git "$APORTS"
    git -C "$APORTS" sparse-checkout set scripts
fi
APORTS=$(realpath "$APORTS")
test -x "$APORTS/scripts/mkimage.sh"
LOCO_VERSION=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/portal/loco/config.py")
test -n "$LOCO_VERSION"
if [ -z "$ARCHIVE" ]; then
    ARCHIVE="$WORK/loco-$LOCO_VERSION.tar.gz"
    python3 "$HERE/package.py" "$ROOT" "$ARCHIVE"
fi
LOCO_SOURCES=$(realpath "$ARCHIVE")
LOCO_ISO_DIR=$HERE LOCO_ALPINE_BRANCH=$TAG LOCO_ANSWERS=$ANSWERS LOCO_SSH_KEY=$SSH_KEY
export LOCO_SOURCES LOCO_ISO_DIR LOCO_ALPINE_BRANCH LOCO_VERSION LOCO_ANSWERS LOCO_SSH_KEY
cp "$HERE/mkimg.loco.sh" "$HERE/genapkovl-loco.sh" "$APORTS/scripts/"
if [ -z "${PACKAGER_PRIVKEY:-}" ]; then
    abuild-keygen -a -i -n >/dev/null
    # abuild-keygen écrit le nom de la clé dans abuild.conf.
    # shellcheck disable=SC1091
    . "$HOME/.abuild/abuild.conf"
    export PACKAGER_PRIVKEY
fi
# L'overlay dépend aussi des sources, paquets et réponses, absents de la clé de cache mkimage.
rm -rf "$OUTDIR/.mkimage"/apkovl_*
(cd "$APORTS/scripts" && sh ./mkimage.sh --tag "$TAG" --arch x86_64 --profile loco --outdir "$OUTDIR" --workdir "$OUTDIR/.mkimage" \
    --repository "https://dl-cdn.alpinelinux.org/alpine/$TAG/main" \
    --repository "https://dl-cdn.alpinelinux.org/alpine/$TAG/community")
(cd "$OUTDIR" && sha256sum "loco-$TAG-x86_64.iso" > "loco-$TAG-x86_64.iso.sha256")
echo "ISO prête : $OUTDIR/loco-$TAG-x86_64.iso"
