#!/usr/bin/env bash
set -Eeuo pipefail

COMPOSE_FILE="docker-compose.prod.yml"
BACKUP_DIR="${BACKUP_DIR:-./backups/redis}"

if [[ ! -f .env || ! -f backend/.env ]]; then
    echo "Se requieren .env (raíz) y backend/.env para ejecutar Compose." >&2
    exit 1
fi

if ! command -v docker >/dev/null 2>&1 || ! docker compose version >/dev/null 2>&1; then
    echo "Docker Compose v2 es requerido." >&2
    exit 1
fi

umask 077
mkdir -p "$BACKUP_DIR"
chmod 700 "$BACKUP_DIR"

echo "Solicitando snapshot RDB consistente a Redis..."
baseline_info="$(docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli INFO persistence)"
baseline_last_save="$(printf '%s\n' "$baseline_info" | awk -F: '$1 == "rdb_last_save_time" { print $2; exit }')"
if [[ ! "$baseline_last_save" =~ ^[0-9]+$ ]]; then
    echo "Redis no reportó un rdb_last_save_time válido antes de solicitar el snapshot." >&2
    exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli BGSAVE

deadline=$((SECONDS + 90))
observed_bgsave=false
snapshot_ready=false
while (( SECONDS < deadline )); do
    persistence_info="$(docker compose -f "$COMPOSE_FILE" exec -T redis redis-cli INFO persistence)"
    bgsave_in_progress="$(printf '%s\n' "$persistence_info" | awk -F: '$1 == "rdb_bgsave_in_progress" { print $2; exit }')"
    last_bgsave_status="$(printf '%s\n' "$persistence_info" | awk -F: '$1 == "rdb_last_bgsave_status" { print $2; exit }')"
    last_save="$(printf '%s\n' "$persistence_info" | awk -F: '$1 == "rdb_last_save_time" { print $2; exit }')"
    if [[ ! "$bgsave_in_progress" =~ ^[01]$ || "$last_bgsave_status" == "" || ! "$last_save" =~ ^[0-9]+$ ]]; then
        echo "Redis no reportó campos de persistencia válidos; se cancela sin copiar el RDB." >&2
        exit 1
    fi

    if [[ "$bgsave_in_progress" == "1" ]]; then
        observed_bgsave=true
    elif [[ "$bgsave_in_progress" == "0" && "$last_bgsave_status" != "ok" ]]; then
        if [[ "$observed_bgsave" == true || ( "$last_save" =~ ^[0-9]+$ && "$last_save" -gt "$baseline_last_save" ) ]]; then
            echo "Redis reportó un fallo al crear el snapshot." >&2
            exit 1
        fi
    elif [[ "$bgsave_in_progress" == "0" && "$last_bgsave_status" == "ok" ]]; then
        if [[ "$observed_bgsave" == true || ( "$last_save" =~ ^[0-9]+$ && "$last_save" -gt "$baseline_last_save" ) ]]; then
            snapshot_ready=true
            break
        fi
    fi
    sleep 1
done

if [[ "$snapshot_ready" != true ]]; then
    if (( SECONDS >= deadline )); then
        echo "Timeout esperando confirmación del nuevo snapshot RDB de Redis." >&2
    else
        echo "No se confirmó un snapshot RDB nuevo y exitoso." >&2
    fi
    exit 1
fi

docker compose -f "$COMPOSE_FILE" exec -T redis redis-check-rdb /data/dump.rdb >/dev/null
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
backup_path="${BACKUP_DIR%/}/promdata-redis-${timestamp}.rdb"
docker compose -f "$COMPOSE_FILE" cp redis:/data/dump.rdb "$backup_path"
chmod 600 "$backup_path"

echo "Snapshot verificado: ${backup_path}"
echo "Cifre y replique este archivo fuera del VPS según la política de backup del entorno."
