#!/bin/bash
# Construit l'ISO sur une VM Alpine officielle, contrôle le média puis l'installe en BIOS et UEFI.
set -euo pipefail
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=tests/lib-vm.sh
. "$ROOT/tests/lib-vm.sh"
WORK=$(mktemp -d)
LOG_DIR=${LOCO_VM_ARTIFACTS:-$ROOT/test-results}/alpine-build
mkdir -p "$LOG_DIR"
cleanup() {
    rc=$?
    trap - EXIT
    vm_stop iso-builder
    rm -rf "$WORK"
    exit "$rc"
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
image=$(vm_image "$ROOT/tests/alpine-platforms.json" alpine-3.24)
VM_SHELL=/bin/sh VM_UNLOCK=1 VM_PACKAGES='sudo, bash, curl' vm_start iso-builder "$image" "$(free_port)"
vm_ssh iso-builder 'sudo apk add --no-progress git xorriso squashfs-tools fakeroot abuild alpine-base mkinitfs syslinux grub-bios grub-efi mtools dosfstools python3; sudo addgroup ci abuild; echo "permit nopass ci as root" | sudo tee /etc/doas.d/loco-ci.conf >/dev/null' < /dev/null
python3 "$ROOT/linux/iso/package.py" "$ROOT" "$WORK/source.tar.gz"
vm_ssh iso-builder 'mkdir -p src; tar xzf - -C src' < "$WORK/source.tar.gz"
version=$(sed -n 's/^VERSION = "\(.*\)"/\1/p' "$ROOT/portal/loco/config.py")
vm_ssh iso-builder "cd src/loco-$version && sh linux/iso/build.sh --outdir /home/ci/out" < /dev/null \
    > "$LOG_DIR/build.log" 2>&1 || { tail -60 "$LOG_DIR/build.log"; exit 1; }
ISO="$LOG_DIR/loco-v3.24-x86_64.iso"
vm_ssh iso-builder 'cat /home/ci/out/loco-v3.24-x86_64.iso' < /dev/null > "$ISO"
vm_ssh iso-builder 'cat /home/ci/out/loco-v3.24-x86_64.iso.sha256' < /dev/null > "$ISO.sha256"
(cd "$LOG_DIR" && sha256sum -c "$(basename "$ISO.sha256")")
vm_stop iso-builder
# Lecture sans montage root ; les éléments d'amorçage et l'overlay doivent être présents.
xorriso -osirrox on -indev "$ISO" -extract /loco.apkovl.tar.gz "$WORK/overlay.tar.gz" >/dev/null 2>&1
mkdir "$WORK/overlay"
tar xzf "$WORK/overlay.tar.gz" -C "$WORK/overlay"
test -x "$WORK/overlay/usr/local/sbin/loco-installer"
test -s "$WORK/overlay/usr/share/loco/loco-$version.tar.gz"
test ! -e "$WORK/overlay/root/.ssh/authorized_keys"
test ! -e "$WORK/overlay/etc/loco-answers.conf"
test ! -e "$WORK/overlay/etc/runlevels/default/sshd"
"$ROOT/tests/vm-alpine-iso-e2e.sh" "$ISO"
echo 'ISO construite et installée en BIOS / UEFI ; redémarrages sans média réussis.'
