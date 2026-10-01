#!/bin/bash
set -euo pipefail

COMPOSE_FILE="docker-compose.prod.yml"

fail_with_logs() {
    local service="$1"
    echo "❌ ERROR: verificación fallida para ${service}. Logs recientes:"
    docker compose -f "$COMPOSE_FILE" logs --tail=80 "$service" || true
    exit 1
}

if [[ ! -f .env || ! -f backend/.env ]]; then
    echo "❌ Se requieren .env (raíz) y backend/.env. Créelos desde sus plantillas y complete secretos fuera del repositorio."
    exit 1
fi

if [[ -n "$(git status --porcelain --untracked-files=normal)" ]]; then
    echo "❌ El árbol Git no está limpio. Despliegue una revisión confirmada para mantener builds y rollback reproducibles."
    exit 1
fi

APP_BUILD_SHA="${APP_BUILD_SHA:-$(git rev-parse --short=12 HEAD)}"
export APP_BUILD_SHA
APP_DOMAIN="$(docker compose -f "$COMPOSE_FILE" config --format json | python3 -c 'import json,sys; print(json.load(sys.stdin)["services"]["caddy"]["environment"]["APP_DOMAIN"])')"
export APP_DOMAIN

if [[ -z "$APP_DOMAIN" || "$APP_DOMAIN" == *"/"* || "$APP_DOMAIN" == *"://"* ]]; then
    echo "❌ APP_DOMAIN debe ser un hostname canónico sin esquema ni path."
    exit 1
fi

echo "🚀 Desplegando PromData en ${APP_DOMAIN} (revisión ${APP_BUILD_SHA})..."
docker compose -f "$COMPOSE_FILE" config --quiet
docker compose -f "$COMPOSE_FILE" up -d --build

check_internal_health() {
    local path="$1"
    docker compose -f "$COMPOSE_FILE" exec -T api python -c \
        'import sys,urllib.request; r=urllib.request.urlopen("http://127.0.0.1:8000" + sys.argv[1], timeout=5); sys.exit(0 if r.status == 200 else 1)' \
        "$path"
}

for path in /health/ready /health/runtime /health/observability; do
    echo "🩺 Verificando API ${path}..."
    healthy=false
    for attempt in $(seq 1 30); do
        if check_internal_health "$path" >/dev/null 2>&1; then
            healthy=true
            break
        fi
        sleep 2
    done
    if [[ "$healthy" != true ]]; then
        fail_with_logs api
    fi
done

echo "🌐 Verificando HTTPS público https://${APP_DOMAIN}/health/ready..."
if ! curl --fail --silent --show-error --retry 10 --retry-connrefused --retry-delay 6 \
    --connect-timeout 5 --max-time 15 "https://${APP_DOMAIN}/health/ready" >/dev/null; then
    fail_with_logs caddy
fi

echo "✅ Despliegue verificado en https://${APP_DOMAIN} (revisión ${APP_BUILD_SHA})."
