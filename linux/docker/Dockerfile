# local-repo en conteneur : voir docker-compose.yml (même dossier) et la section Docker du README.
# Contexte de construction : la racine du projet. La configuration est faite au démarrage par
# linux/install.sh --container (linux/docker/entrypoint.sh).
FROM debian:13-slim
LABEL org.opencontainers.image.title="local-repo" \
      org.opencontainers.image.source="https://github.com/ShaNouhAr/local-repo" \
      org.opencontainers.image.licenses="MIT"

ARG DEBIAN_FRONTEND=noninteractive
# Comptes créés avant les paquets : identifiants fixes, les volumes restent valides d'une image à l'autre.
RUN groupadd --system --gid 990 localrepo \
 && useradd --system --uid 990 --gid 990 --groups adm --home-dir /var/lib/local-repo --shell /usr/sbin/nologin localrepo \
 && groupadd --system --gid 991 apt-cacher-ng \
 && useradd --system --uid 991 --gid 991 --home-dir /var/cache/apt-cacher-ng --shell /usr/sbin/nologin apt-cacher-ng \
 && printf '#!/bin/sh\nexit 101\n' > /usr/sbin/policy-rc.d && chmod 755 /usr/sbin/policy-rc.d \
 && echo "apt-cacher-ng apt-cacher-ng/tunnelenable boolean false" | debconf-set-selections \
 && apt-get update -q \
 && apt-get install -y -q --no-install-recommends \
        nginx libnginx-mod-stream apt-cacher-ng apt-utils gnupg python3 python3-flask gunicorn \
        curl openssl ca-certificates flatpak ostree createrepo-c rpm \
        supervisor logrotate iproute2 procps tzdata \
 && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default /usr/sbin/policy-rc.d

COPY . /usr/src/local-repo
RUN install -m 755 /usr/src/local-repo/linux/docker/entrypoint.sh /usr/local/sbin/local-repo-entrypoint \
 && install -m 755 /usr/src/local-repo/linux/docker/local-repo-cron /usr/local/sbin/local-repo-cron \
 && install -m 644 /usr/src/local-repo/linux/docker/logrotate.conf /etc/local-repo-logrotate.conf \
 && install -d /var/lib/logrotate

ENV LR_RUNTIME=container
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=30s --timeout=10s --start-period=3m --retries=3 \
    CMD . /etc/local-repo/local-repo.conf && curl -fsk --max-time 8 "https://127.0.0.1:$HTTPS_PORT/healthz" >/dev/null || exit 1
ENTRYPOINT ["/usr/local/sbin/local-repo-entrypoint"]
