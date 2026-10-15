#!/bin/sh
# Overlay de l'ISO : assistant sur tty7, runtime complet, sources embarquées.
set -eu
hostname=${1:?nom requis}
here=${LOCO_ISO_DIR:?build.sh doit fournir LOCO_ISO_DIR}
sources=${LOCO_SOURCES:?archive requise}
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/etc/apk" "$tmp/etc/network" "$tmp/etc/local.d" \
    "$tmp/etc/runlevels/boot" "$tmp/etc/runlevels/default" \
    "$tmp/usr/local/sbin" "$tmp/usr/local/lib/loco" "$tmp/usr/share/loco"
printf '%s\n' "$hostname" > "$tmp/etc/hostname"
cp "$here/packages" "$tmp/etc/apk/world"
cp "$here/packages" "$tmp/usr/share/loco/packages"
printf 'auto lo\niface lo inet loopback\n\nauto eth0\niface eth0 inet dhcp\n' > "$tmp/etc/network/interfaces"
for s in devfs dmesg mdev hwdrivers modloop; do ln -s "/etc/init.d/$s" "$tmp/etc/runlevels/boot/$s"; done
for s in networking local; do ln -s "/etc/init.d/$s" "$tmp/etc/runlevels/default/$s"; done
install -m 755 "$here/loco-installer" "$tmp/usr/local/sbin/loco-installer"
install -m 644 "$here/../lib/appliance.sh" "$tmp/usr/local/lib/loco/appliance.sh"
printf '%s\n' "$LOCO_VERSION" > "$tmp/usr/share/loco/VERSION"
printf '%s\n' "$LOCO_ALPINE_BRANCH" > "$tmp/usr/share/loco/ALPINE_BRANCH"
cp "$sources" "$tmp/usr/share/loco/loco-$LOCO_VERSION.tar.gz"
cat > "$tmp/etc/local.d/loco-installer.start" <<'EOF'
#!/bin/sh
# L'assistant utilise la console graphique virtuelle 7 ; Alt+F1 ouvre le login live.
openvt -c 7 -s -- /usr/local/sbin/loco-installer &
EOF
chmod 755 "$tmp/etc/local.d/loco-installer.start"
printf '\nloco : assistant sur Alt+F7 ; relancer avec loco-installer.\n' > "$tmp/etc/motd"
# Le live est accessible physiquement seulement. SSH n'est jamais activé par défaut.
if [ -n "${LOCO_ANSWERS:-}" ]; then
    install -m 600 "$LOCO_ANSWERS" "$tmp/etc/loco-answers.conf"
fi
if [ -n "${LOCO_SSH_KEY:-}" ]; then
    install -d -m 700 "$tmp/root/.ssh" "$tmp/etc/ssh/sshd_config.d"
    install -m 600 "$LOCO_SSH_KEY" "$tmp/root/.ssh/authorized_keys"
    printf 'PermitRootLogin prohibit-password\nPasswordAuthentication no\n' > "$tmp/etc/ssh/sshd_config.d/loco.conf"
    ln -s /etc/init.d/sshd "$tmp/etc/runlevels/default/sshd"
fi
# Lbu ne doit pas recopier l'assistant et ses secrets dans le système installé.
printf '/etc/loco-answers.conf\n/etc/local.d/loco-installer.start\n/etc/motd\n' > "$tmp/etc/lbu.exclude"
python3 - "$tmp" "$hostname.apkovl.tar.gz" <<'PYCODE'
import sys, tarfile
with tarfile.open(sys.argv[2], "w:gz") as tar:
    def root_owner(m):
        m.uid = m.gid = 0
        m.uname = m.gname = "root"
        return m
    tar.add(sys.argv[1], arcname=".", filter=root_owner)
PYCODE
