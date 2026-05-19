#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

PYTHON_BIN="python3"
if [[ -x "${SCRIPT_DIR}/venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/venv/bin/python"
elif [[ -x "${SCRIPT_DIR}/../.venv/bin/python" ]]; then
  PYTHON_BIN="${SCRIPT_DIR}/../.venv/bin/python"
fi

echo "Running backend pytest suite with ${PYTHON_BIN}..."
"${PYTHON_BIN}" -m pytest -q
