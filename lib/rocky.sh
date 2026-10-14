#!/bin/bash
# Adaptation native Rocky Linux 9/10, sourcée par install.sh (mêmes réglages et services).
ROCKY_STATE="$ETC/install-state"

rocky_packages() {
    PACKAGES=(tar xz gzip logrotate nginx nginx-mod-stream gnupg2 apt-utils dpkg curl openssl ca-certificates policycoreutils-python-utils)
    if [ "$OS_MAJOR" = 9 ]; then PACKAGES+=(apt-cacher-ng python3.11 python3.11-pip); else
        PACKAGES+=(python3-pip)
        PACKAGES+=(gcc-c++ cmake make patch zlib-devel bzip2-devel xz-devel openssl-devel libevent-devel c-ares-devel)
    fi
    if [ "$FLATPAK_ENABLED" = 1 ]; then PACKAGES+=(flatpak ostree); fi
    if [ "$RPM_ENABLED" = 1 ]; then PACKAGES+=(createrepo_c rpm); fi
    install -d -m 700 -o root -g root "$ROCKY_STATE"
    touch "$PACKAGE_RECORD"
    : > "$LOG"
    for p in epel-release dnf-plugins-core "${PACKAGES[@]}"; do
        if ! rpm -q "$p" >/dev/null 2>&1; then
            grep -qx "$p" "$PACKAGE_RECORD" || echo "$p" >> "$PACKAGE_RECORD"
        fi
    done
    dnf install -y epel-release dnf-plugins-core >> "$LOG" 2>&1
    if ! dnf config-manager --dump crb | grep -q '^enabled = 1'; then
        dnf config-manager --set-enabled crb >> "$LOG" 2>&1
        touch "$ROCKY_STATE/rocky-crb-enabled"
    fi
    if ! dnf install -y "${PACKAGES[@]}" >> "$LOG" 2>&1; then
        tail -20 "$LOG" >&2
        die "installation DNF impossible (journal : $LOG)."
    fi
    # Runtime isolé : les comptes/sauvegardes scrypt restent compatibles entre distributions.
    local runtime=/usr/local/lib/local-repo/venv
    local interpreter=python3
    if [ "$OS_MAJOR" = 9 ]; then interpreter=python3.11; fi
    if [ ! -x "$runtime/bin/python" ]; then "$interpreter" -m venv "$runtime"; fi
    "$runtime/bin/python" -m pip install --disable-pip-version-check --only-binary=:all: --require-hashes \
        -r "$SRC/lib/requirements-rocky.txt" >> "$LOG" 2>&1
    if [ "$OS_MAJOR" = 10 ]; then rocky_build_acng; fi
}

# shellcheck disable=SC2129 # Étapes de compilation explicites, journal commun.
rocky_build_acng() {
    # Absent d'EPEL 10 : source Debian et correctifs figés, vérifiés par SHA-256.
    local deps=/usr/local/lib/local-repo/acng build series patch_file
    if [ -x "$deps/sbin/apt-cacher-ng" ] && [ -f /etc/apt-cacher-ng/acng.conf ] && [ "$(cat "$deps/version" 2>/dev/null)" = 3.7.5-1.3 ]; then
        rocky_acng_service
        return
    fi
    build=$(mktemp -d)
    local upstream=https://deb.debian.org/debian/pool/main/a/apt-cacher-ng
    curl -fSL --retry 3 "$upstream/apt-cacher-ng_3.7.5.orig.tar.xz" -o "$build/source.tar.xz" >> "$LOG" 2>&1
    curl -fSL --retry 3 "$upstream/apt-cacher-ng_3.7.5-1.3.debian.tar.xz" -o "$build/patches.tar.xz" >> "$LOG" 2>&1
    printf '%s  %s\n' 2e4887d6870396334992accd331e12cba86dfef857532f610bb0635a8e32900d "$build/source.tar.xz" \
        7c96dd3e591f67bbda72c6881e714bb170f770f788fc72cb01e9f3ebaa639946 "$build/patches.tar.xz" | sha256sum -c - >> "$LOG"
    tar -xf "$build/source.tar.xz" -C "$build"
    local source="$build/apt-cacher-ng-3.7.5"
    tar -xf "$build/patches.tar.xz" -C "$source"
    series="$source/debian/patches/series"
    while read -r patch_file _; do
        case "$patch_file" in ''|'#'*) continue ;; esac
        patch -d "$source" -p1 < "$source/debian/patches/$patch_file" >> "$LOG" 2>&1
    done < "$series"
    say "Compilation du cache APT (Rocky 10, source vérifiée)"
    cmake -S "$source" -B "$build/build" -DCMAKE_BUILD_TYPE=Release -DCMAKE_INSTALL_PREFIX="$deps" \
        -DCMAKE_INSTALL_SYSCONFDIR=/etc -DCMAKE_INSTALL_RPATH="$deps/lib64;$deps/lib" -DACNG_CACHE_DIR=/var/cache/apt-cacher-ng \
        -DAVAHIDIR="$deps/etc/avahi/services" -DACNG_LOG_DIR=/var/log/apt-cacher-ng -DENABLE_TOOLS=OFF >> "$LOG" 2>&1
    cmake --build "$build/build" -j2 >> "$LOG" 2>&1
    cmake --install "$build/build" >> "$LOG" 2>&1
    # Comme le paquet EPEL : conserver l'URL du miroir demandé plutôt que les anciens backends.
    sed -i -E '/^Remap-(debrep|uburep|klxrep):/s/;.*$//' /etc/apt-cacher-ng/acng.conf
    echo 3.7.5-1.3 > "$deps/version"
    rm -rf "$build"
    rocky_acng_service
}

rocky_acng_service() {
    local deps=/usr/local/lib/local-repo/acng
    if ! id apt-cacher-ng >/dev/null 2>&1; then
        useradd --system --home-dir /var/cache/apt-cacher-ng --shell /sbin/nologin apt-cacher-ng
        touch "$ROCKY_STATE/rocky-acng-user-created"
    fi
    install -d -o apt-cacher-ng -g apt-cacher-ng /var/cache/apt-cacher-ng /var/log/apt-cacher-ng
    cat > /etc/systemd/system/apt-cacher-ng.service <<EOF
[Unit]
Description=Cache APT local-repo (source Debian 3.7.5-1.3)
After=network-online.target
[Service]
User=apt-cacher-ng
Group=apt-cacher-ng
RuntimeDirectory=apt-cacher-ng
ExecStart=$deps/sbin/apt-cacher-ng -c /etc/apt-cacher-ng ForeGround=1
Restart=on-failure
[Install]
WantedBy=multi-user.target
EOF
    cat > /etc/systemd/system/local-repo-acng-maintenance.service <<EOF
[Unit]
Description=Expiration du cache APT local-repo
After=apt-cacher-ng.service
[Service]
Type=oneshot
ExecStart=$deps/lib/apt-cacher-ng/acngtool maint -c /etc/apt-cacher-ng SocketPath=/run/apt-cacher-ng/socket
NoNewPrivileges=yes
ProtectHome=yes
EOF
    cat > /etc/systemd/system/local-repo-acng-maintenance.timer <<'EOF'
[Unit]
Description=Maintenance quotidienne du cache APT local-repo
[Timer]
OnCalendar=daily
RandomizedDelaySec=1h
Persistent=true
[Install]
WantedBy=timers.target
EOF
    cat > /etc/logrotate.d/local-repo-acng <<'EOF'
/var/log/apt-cacher-ng/apt-cacher.log /var/log/apt-cacher-ng/apt-cacher.err {
    daily
    rotate 7
    compress
    missingok
    notifempty
    su apt-cacher-ng apt-cacher-ng
    sharedscripts
    postrotate
        systemctl kill -s USR1 --kill-whom=main apt-cacher-ng.service >/dev/null 2>&1 || true
    endscript
}
EOF
}

rocky_nginx() {
    # Les fichiers du portail utilisent la disposition commune ; conf.d les inclut dans http{}.
    install -d /etc/nginx/sites-available /etc/nginx/sites-enabled /etc/nginx/modules-enabled
    echo 'include /etc/nginx/sites-enabled/*.conf;' > /etc/nginx/conf.d/local-repo.conf
    if [ ! -f "$ROCKY_STATE/nginx-rocky-original.conf" ]; then cp -p /etc/nginx/nginx.conf "$ROCKY_STATE/nginx-rocky-original.conf"; fi
    python3 - "$WU_ENABLED" <<'PY'
from pathlib import Path
import sys,re
p=Path('/etc/nginx/nginx.conf')
s=p.read_text()
# Le serveur de bienvenue fourni par Rocky réserve default_server:80. Les autres sites restent.
if sys.argv[1]=='1':
    for m in reversed(list(re.finditer(r'(?m)^\s*server\s*\{',s))):
        level=1; end=m.end()
        while level and end<len(s):
            if s[end]=='{': level+=1
            elif s[end]=='}': level-=1
            end+=1
        block=s[m.start():end]
        if re.search(r'root\s+/usr/share/nginx/html\s*;',block):
            s=s[:m.start()]+s[end:]
line='include /etc/nginx/local-repo-stream.conf; # local-repo\n'
s=s.replace(line,'')
s=s.rstrip()+'\n'+line
p.write_text(s)
PY
    sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1 > "$ROCKY_STATE/nginx-rocky-managed.sha256"
    # TCP : le backend APT écoute uniquement en boucle locale ; nginx filtre les clients.
    {
        if [ "$WU_ENABLED" = 1 ]; then
            render "$PREFIX/conf/nginx-stream.conf" | sed '$d'
        else echo 'stream {'; fi
        cat <<'EOF'
    server {
        listen 3142;
        listen [::]:3142;
        include /etc/nginx/snippets/local-repo-clients.conf;
        proxy_connect_timeout 10s;
        proxy_timeout 1h;
        proxy_pass 127.0.0.1:23142;
    }
}
EOF
    } > /etc/nginx/local-repo-stream.conf
    # Le module stream est chargé par le paquet nginx-mod-stream dans la configuration Rocky.
    rm -f /etc/nginx/modules-enabled/99-local-repo-stream.conf
}

rocky_access() {
    if selinuxenabled; then
        # Ne jamais désactiver SELinux ; enregistrer seulement nos ajouts pour la désinstallation.
        if [ ! -e "$ROCKY_STATE/rocky-selinux-connect" ]; then
            getsebool httpd_can_network_connect | awk '{print $3}' > "$ROCKY_STATE/rocky-selinux-connect"
        fi
        setsebool -P httpd_can_network_connect on
        local path pattern port
        rocky_context "$GUNICORN" bin_t "$GUNICORN"
        restorecon "$GUNICORN"
        if [ "$OS_MAJOR" = 10 ]; then
            # Un exécutable placé sous lib/ hérite sinon du domaine init_t, impropre au cache.
            rocky_context /usr/local/lib/local-repo/acng/sbin/apt-cacher-ng bin_t /usr/local/lib/local-repo/acng/sbin/apt-cacher-ng
            restorecon /usr/local/lib/local-repo/acng/sbin/apt-cacher-ng
            rocky_context /usr/local/lib/local-repo/acng/lib/apt-cacher-ng/acngtool bin_t /usr/local/lib/local-repo/acng/lib/apt-cacher-ng/acngtool
            restorecon /usr/local/lib/local-repo/acng/lib/apt-cacher-ng/acngtool
        fi
        for path in "$DATA" "$STATE/nginx" /opt/local-repo/portal/static; do
            pattern=$(python3 -c 'import re,sys; print(re.escape(sys.argv[1])+"(/.*)?")' "$path")
            rocky_context "$pattern" httpd_sys_content_t "$path"
            restorecon -R "$path"
        done
        for path in "$DATA/wucache" "$DATA/flatpakcache" "$DATA/rpmcache"; do
            pattern=$(python3 -c 'import re,sys; print(re.escape(sys.argv[1])+"(/.*)?")' "$path")
            rocky_context "$pattern" httpd_cache_t "$path"
            restorecon -R "$path"
        done
        for port in "$HTTPS_PORT" "$PORTAL_PORT" 3142; do
            if ! semanage port -l | awk '$1=="http_port_t" && $2=="tcp" {for(i=3;i<=NF;i++){gsub(/,/,"",$i);print $i}}' | grep -qx "$port"; then
                semanage port -a -t http_port_t -p tcp "$port" >> "$LOG" 2>&1 || die "SELinux : port $port réservé à un autre service ; choisissez un autre port."
                echo "$port" >> "$ROCKY_STATE/rocky-selinux-ports"
            fi
        done
    fi
    if systemctl is-active -q firewalld; then
        local zone
        {
            echo '<?xml version="1.0" encoding="utf-8"?><service><short>local-repo</short>'
            echo "<port protocol=\"tcp\" port=\"$HTTPS_PORT\"/><port protocol=\"tcp\" port=\"3142\"/>"
            if [ "$HTTP_ENABLED" = 1 ]; then echo "<port protocol=\"tcp\" port=\"$PORTAL_PORT\"/>"; fi
            if [ "$WU_ENABLED" = 1 ]; then echo '<port protocol="tcp" port="80"/><port protocol="tcp" port="443"/>'; fi
            echo '</service>'
        } > /etc/firewalld/services/local-repo.xml
        firewall-cmd --reload >> "$LOG"
        while read -r zone; do
            if ! firewall-cmd --permanent --zone="$zone" --query-service=local-repo >/dev/null; then
                firewall-cmd --permanent --zone="$zone" --add-service=local-repo >> "$LOG"
                echo "$zone" >> "$ROCKY_STATE/rocky-firewall-zones"
            fi
        done < <(firewall-cmd --get-active-zones | awk '/^[^ ]/{print $1}')
        firewall-cmd --reload >> "$LOG"
    fi
}

rocky_context() {
    local existing
    existing=$(semanage fcontext -l -C | python3 -c 'import sys; print(next((row.split()[-1].split(":")[2] for row in sys.stdin if row.split() and row.split()[0]==sys.argv[1]), ""))' "$1")
    if [ -n "$existing" ]; then
        [ "$existing" = "$2" ] || die "SELinux : $3 possède une règle personnalisée incompatible avec $2."
    else
        semanage fcontext -a -t "$2" "$1" >> "$LOG" 2>&1
        grep -Fxq "$1" "$ROCKY_STATE/rocky-selinux-contexts" 2>/dev/null || echo "$1" >> "$ROCKY_STATE/rocky-selinux-contexts"
    fi
    if ! matchpathcon -n "$3" | grep -q ":$2:"; then
        die "SELinux : $3 possède une règle personnalisée incompatible avec $2."
    fi
}

rocky_remove() {
    local port pattern zone
    if [ -f "$ROCKY_STATE/rocky-firewall-zones" ] && systemctl is-active -q firewalld; then
        while read -r zone; do firewall-cmd --permanent --zone="$zone" --remove-service=local-repo >/dev/null; done < "$ROCKY_STATE/rocky-firewall-zones"
        rm -f /etc/firewalld/services/local-repo.xml
        firewall-cmd --reload >/dev/null
    fi
    if [ -f "$ROCKY_STATE/rocky-selinux-ports" ]; then
        while read -r port; do semanage port -d -p tcp "$port"; done < "$ROCKY_STATE/rocky-selinux-ports"
    fi
    if [ -f "$ROCKY_STATE/rocky-selinux-contexts" ]; then
        while read -r pattern; do semanage fcontext -d "$pattern"; done < "$ROCKY_STATE/rocky-selinux-contexts"
        restorecon -R "$DATA" "$STATE/nginx" /opt/local-repo/portal/static
    fi
    if [ -f "$ROCKY_STATE/rocky-selinux-connect" ]; then
        setsebool -P httpd_can_network_connect "$(cat "$ROCKY_STATE/rocky-selinux-connect")"
    fi
    if [ -f "$ROCKY_STATE/nginx-rocky-managed.sha256" ] && [ "$(sha256sum /etc/nginx/nginx.conf | cut -d' ' -f1)" = "$(cat "$ROCKY_STATE/nginx-rocky-managed.sha256")" ]; then
        cp -p "$ROCKY_STATE/nginx-rocky-original.conf" /etc/nginx/nginx.conf
    else
        sed -i '/# local-repo$/d' /etc/nginx/nginx.conf
    fi
    rm -f /etc/nginx/conf.d/local-repo.conf /etc/nginx/local-repo-stream.conf
    if [ -f "$ROCKY_STATE/rocky-crb-enabled" ]; then dnf config-manager --set-disabled crb >/dev/null; fi
    if [ -d /usr/local/lib/local-repo/acng ]; then
        systemctl disable --now local-repo-acng-maintenance.timer >/dev/null 2>&1 || true
        systemctl disable --now apt-cacher-ng >/dev/null 2>&1 || true
        rm -f /etc/systemd/system/apt-cacher-ng.service /etc/systemd/system/local-repo-acng-maintenance.service /etc/systemd/system/local-repo-acng-maintenance.timer /etc/logrotate.d/local-repo-acng
        rm -rf /usr/local/lib/local-repo/acng
        systemctl daemon-reload
    fi
    rm -rf /usr/local/lib/local-repo/venv
    rm -f "$ROCKY_STATE"/rocky-selinux-* "$ROCKY_STATE/rocky-firewall-zones" "$ROCKY_STATE/rocky-crb-enabled" "$ROCKY_STATE/nginx-rocky-managed.sha256" "$ROCKY_STATE/nginx-rocky-original.conf"
}
