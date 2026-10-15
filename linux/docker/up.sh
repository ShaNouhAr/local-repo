#!/bin/bash
# Installation / mise à jour Docker, y compris migration automatique de local-repo.
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
legacy=$(docker ps -aq --filter label=com.docker.compose.project=local-repo --filter label=com.docker.compose.service=local-repo)
[ "$(printf '%s\n' "$legacy" | sed '/^$/d' | wc -l)" -le 1 ] || { echo 'Plusieurs conteneurs historiques trouvés.' >&2; exit 1; }
if [ -f .env ] && grep -q '^LR_' .env; then
    [ ! -e .env.before-loco ] || { echo '.env.before-loco existe déjà ; conversion refusée.' >&2; exit 1; }
    cp -p .env .env.before-loco
    sed -i 's/^LR_/LOCO_/' .env
fi
docker compose build
if [ -n "$legacy" ]; then
    if docker ps -aq --filter label=com.docker.compose.project=loco | grep -q .; then
        echo 'Deux projets tournent déjà ; migration refusée.' >&2; exit 1
    fi
    echo 'Migration Docker vers loco : sauvegarde puis copie des volumes, anciens volumes conservés.'
    docker exec "$legacy" local-repo-backup create
    docker stop "$legacy" >/dev/null
    created=()
    # shellcheck disable=SC2317,SC2329  # appelée par le trap EXIT pendant la migration.
    rollback() {
        rc=$?
        if [ "$rc" -ne 0 ]; then
            docker compose down >/dev/null 2>&1 || true
            if [ "${#created[@]}" -gt 0 ]; then docker volume rm "${created[@]}" >/dev/null; fi
            docker start "$legacy" >/dev/null
            echo 'Migration interrompue ; ancien conteneur redémarré. Les anciens volumes sont conservés.' >&2
        fi
    }
    trap rollback EXIT
    # Les volumes nommés portent le préfixe Compose ; les dossiers bind de l'utilisateur restent en place.
    for suffix in data backups state etc apt-cache logs; do
        old="local-repo_local-repo-$suffix"
        new="loco_loco-$suffix"
        if docker volume inspect "$old" >/dev/null 2>&1; then
            if docker volume inspect "$new" >/dev/null 2>&1; then
                echo "Destination $new déjà présente ; copie refusée sans écrasement." >&2; exit 1
            fi
            docker volume create --label com.docker.compose.project=loco --label "com.docker.compose.volume=loco-$suffix" "$new" >/dev/null
            created+=("$new")
            docker run --rm --entrypoint /bin/bash -v "$old:/from:ro" -v "$new:/to" loco:latest -c 'cp -a /from/. /to/'
        fi
    done
fi
docker compose up -d
for ((i=0; i<120; i++)); do
    id=$(docker compose ps -q loco)
    if [ -n "$id" ] && [ "$(docker inspect -f '{{.State.Health.Status}}' "$id")" = healthy ]; then
        if [ -n "$legacy" ]; then docker rm "$legacy" >/dev/null; fi
        trap - EXIT
        echo 'loco prêt. Les anciens volumes restent disponibles comme sauvegarde de migration.'
        exit 0
    fi
    sleep 2
done
echo 'loco ne devient pas sain : consulter docker compose logs.' >&2
exit 1
