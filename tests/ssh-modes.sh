#!/bin/bash
# VM Alpine JETABLE, root : authentifications réelles sur un sshd isolé du service de la VM.
set -euo pipefail
if [ "${LOCO_SSH_TEST_VM:-}" != 1 ] || [ "$(id -u)" != 0 ] || [ ! -f /etc/alpine-release ]; then
    echo 'Réservé à une VM Alpine jetable : LOCO_SSH_TEST_VM=1, root.' >&2
    exit 2
fi
command -v ssh >/dev/null || apk add --no-progress openssh-client >/dev/null
ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)
# shellcheck source=linux/lib/appliance.sh
. "$ROOT/linux/lib/appliance.sh"
work=$(mktemp -d /root/loco-ssh-test.XXXXXX)
old_hash=$(awk -F: '$1 == "root" {print $2}' /etc/shadow)
daemon_pid=""
cleanup() {
    local rc=$?
    trap - EXIT
    if [ -n "$daemon_pid" ]; then kill "$daemon_pid" 2>/dev/null || true; wait "$daemon_pid" 2>/dev/null || true; fi
    printf 'root:%s\n' "$old_hash" | chpasswd -e
    rm -rf "$work"
    exit "$rc"
}
trap cleanup EXIT
trap 'echo "Échec du test SSH, ligne $LINENO (${mode:-préparation})." >&2; if [ -f "$work/sshd.log" ]; then tail -15 "$work/sshd.log" >&2; fi' ERR
umask 077
openssl rand -hex 16 > "$work/password"
openssl rand -hex 16 > "$work/wrong-password"
printf 'root:%s\n' "$(cat "$work/password")" | chpasswd
ssh-keygen -q -t ed25519 -N '' -f "$work/host"
ssh-keygen -q -t ed25519 -N '' -f "$work/key"
mkdir -p "$work/etc/ssh"
printf '# configuration de test\n' > "$work/etc/ssh/sshd_config"
cat > "$work/askpass" <<'SH'
#!/bin/sh
cat "$LOCO_SSH_TEST_PASSWORD_FILE"
SH
chmod 700 "$work/askpass"
export LOCO_SSH_TEST_PASSWORD_FILE="$work/password"
port=$(python3 -c 'import socket;s=socket.socket();s.bind(("127.0.0.1",0));print(s.getsockname()[1]);s.close()')
client=(ssh -p "$port" -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR
    -o ConnectTimeout=3 -o IdentityAgent=none -o NumberOfPasswordPrompts=1)
key_access() { "${client[@]}" -i "$work/key" -o BatchMode=yes -o PreferredAuthentications=publickey root@127.0.0.1 true; }
password_access() {
    SSH_ASKPASS="$work/askpass" SSH_ASKPASS_REQUIRE=force DISPLAY=:0 "${client[@]}" \
        -o BatchMode=no -o PreferredAuthentications=password -o PubkeyAuthentication=no root@127.0.0.1 true
}
for mode in password key off; do
    key=""
    if [ "$mode" = key ]; then key=$(cat "$work/key.pub"); fi
    appliance_write_ssh "$work" "$mode" "$key"
    if [ "$mode" = off ]; then
        test ! -L "$work/etc/runlevels/default/sshd"
    else
        test "$(readlink "$work/etc/runlevels/default/sshd")" = /etc/init.d/sshd
    fi
    if [ "$mode" = password ]; then test ! -e "$work/root/.ssh/authorized_keys"; fi
    if [ "$mode" = key ]; then
        test "$(stat -c %a "$work/root/.ssh")" = 700
        test "$(stat -c %a "$work/root/.ssh/authorized_keys")" = 600
    fi
    printf 'Include %s\n' "$work/etc/ssh/sshd_config.d/loco.conf" > "$work/test-sshd.conf"
    config=(-f "$work/test-sshd.conf" -h "$work/host" -p "$port" -o ListenAddress=127.0.0.1
        -o "PidFile=$work/pid"
        -o "AuthorizedKeysFile=$work/root/.ssh/authorized_keys")
    /usr/sbin/sshd -t "${config[@]}"
    /usr/sbin/sshd -T "${config[@]}" | grep -x 'permitemptypasswords no' >/dev/null
    /usr/sbin/sshd -D -e "${config[@]}" > "$work/sshd.log" 2>&1 &
    daemon_pid=$!
    for _ in $(seq 1 50); do
        if python3 - "$port" <<'PY' 2>/dev/null
import socket,sys
with socket.create_connection(('127.0.0.1', int(sys.argv[1])), timeout=.2): pass
PY
        then break; fi
        sleep .1
    done
    case "$mode" in
        password)
            password_access
            if LOCO_SSH_TEST_PASSWORD_FILE="$work/wrong-password" password_access 2>/dev/null; then
                echo 'Mot de passe incorrect accepté.' >&2; exit 1
            fi
            ;;
        key)
            key_access
            if password_access 2>/dev/null; then echo 'Mot de passe accepté en mode clé.' >&2; exit 1; fi
            ;;
        off)
            if key_access 2>/dev/null || password_access 2>/dev/null; then echo 'Connexion root acceptée en mode désactivé.' >&2; exit 1; fi
            ;;
    esac
    kill "$daemon_pid"
    wait "$daemon_pid" || true
    daemon_pid=""
    echo "SSH $mode : authentifications et démarrage configuré OK"
done
