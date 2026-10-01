#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# [Fase 0.3 2026-09] Runner honesto: elige un intérprete que REALMENTE pueda
# importar las dependencias (pytest + ibis + duckdb). Un venv roto ya no hace
# que la suite "pase" en silencio con el python del sistema sin deps.
_is_viable() {
  local candidate="$1"
  if [[ -x "${candidate}" ]]; then
    "${candidate}" -c "import pytest, ibis, duckdb" >/dev/null 2>&1
  elif command -v "${candidate}" >/dev/null 2>&1; then
    "${candidate}" -c "import pytest, ibis, duckdb" >/dev/null 2>&1
  else
    return 1
  fi
}

for candidate in \
  "${SCRIPT_DIR}/venv/bin/python" \
  "${SCRIPT_DIR}/../.venv/bin/python" \
  "python3" \
  "python"; do
  if _is_viable "${candidate}"; then
    echo "Running backend pytest suite with ${candidate}..."
    exec "${candidate}" -m pytest -q
  fi
done

# Fallback: mismo stack que producción (Python 3.11) vía Docker.
if command -v docker >/dev/null 2>&1; then
  REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
  echo "No viable local interpreter (need pytest+ibis+duckdb); running suite in Docker..."
  DOCKER_NET_ARGS=()
  if docker network inspect backend_default >/dev/null 2>&1; then
    DOCKER_NET_ARGS=(--network backend_default)
  fi
  exec docker run --rm "${DOCKER_NET_ARGS[@]}" \
    -e PYTHONPATH=/repo/backend \
    -v "${REPO_ROOT}":/repo \
    -w /repo/backend backend-api:latest \
    python -m pytest -q
fi

echo "ERROR: no viable Python interpreter (pytest+ibis+duckdb) and Docker is unavailable." >&2
echo "Hint: activate a working virtualenv or start 'docker compose up -d api'." >&2
exit 1
